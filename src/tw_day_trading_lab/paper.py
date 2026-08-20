"""Paper trading daily cycle: canonical bars in, closed trades out.

PA-P8 scope. Wires PA-P1..P7 together for one trading day and writes only to
a paper ledger - no broker, no order, no cancel.

Rules that are not negotiable, because each one exists to stop a specific way
of fooling yourself:

- **Stop is checked before target inside the same bar.** A 5m bar that spans
  both is ambiguous; assuming the good outcome would inflate expectancy on
  exactly the volatile bars that matter.
- **One trade per setup_id, ever.** The in-memory ledger key is never
  released, and `traded_setups_path` persists the ids so a crash mid-day
  cannot re-enter a setup after a restart.
- **No new entry while market data is unhealthy.** PA-P1 reports the health;
  a signal computed from a feed that dropped ticks is not a signal.
- **Every position is closed before the day ends.** Force exit at the
  configured time, and anything still open at the last bar is closed there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .bars import MarketBar
from .cost import TaiwanDayTradeCostModel
from .ledger import DuplicateIntentError, OrderIntent, PaperLedger
from .rvol import RvolResult
from .setup import SIGNAL, BreakoutRetestEngine, SetupEvent

DEFAULT_STRATEGY_ID = "price_action_v1"
DEFAULT_MAX_NEW_ENTRIES = 2
DEFAULT_NO_ENTRY_AFTER = "13:20"
DEFAULT_FORCE_EXIT_AT = "13:25"

EXIT_STOP = "stop"
EXIT_TARGET = "target_2r"
EXIT_FORCE = "force_exit"
EXIT_PRE_CLOSE = "pre_close"


@dataclass(frozen=True)
class PaperTrade:
    setup_id: str
    symbol: str
    entry_time: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_time: str
    exit_price: float
    exit_reason: str
    r: float
    cost_r: float
    net_r: float
    risk_ticks: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "r": self.r,
            "cost_r": self.cost_r,
            "net_r": self.net_r,
            "risk_ticks": self.risk_ticks,
        }


@dataclass
class _OpenPosition:
    setup_id: str
    symbol: str
    entry_time: str
    entry_price: float
    stop_price: float
    target_price: float
    last_bar: MarketBar | None = None


@dataclass
class _DayState:
    ledger: PaperLedger = field(default_factory=PaperLedger)
    positions: dict[str, _OpenPosition] = field(default_factory=dict)
    trades: list[PaperTrade] = field(default_factory=list)
    events: list[SetupEvent] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    entries: int = 0
    traded_setups: set[str] = field(default_factory=set)
    traded_setups_path: Path | None = None
    cost_model: TaiwanDayTradeCostModel = field(default_factory=TaiwanDayTradeCostModel)


def run_paper_trading_day(
    *,
    trading_date: str,
    bars: Sequence[MarketBar],
    rvol_by_key: dict[str, RvolResult] | None = None,
    market_data_healthy: bool = True,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    max_new_entries: int = DEFAULT_MAX_NEW_ENTRIES,
    no_entry_after: str = DEFAULT_NO_ENTRY_AFTER,
    force_exit_at: str = DEFAULT_FORCE_EXIT_AT,
    engine: BreakoutRetestEngine | None = None,
    traded_setups_path: Path | None = None,
    cost_model: TaiwanDayTradeCostModel | None = None,
) -> dict[str, Any]:
    """Replay one trading day and return trades plus an audit of what was skipped."""
    engine = engine or BreakoutRetestEngine()
    cost_model = cost_model or TaiwanDayTradeCostModel()
    lookup = rvol_by_key or {}
    state = _DayState(traded_setups=load_traded_setups(traded_setups_path))
    state.traded_setups_path = traded_setups_path
    state.cost_model = cost_model

    ordered = sorted(bars, key=lambda bar: (bar.start_at, bar.symbol))
    for bar in ordered:
        clock = _clock(bar.start_at)
        # Manage what is already open before considering anything new.
        _manage_position(state, bar, clock, force_exit_at)

        event = engine.on_bar(bar, lookup.get(f"{bar.symbol}|{bar.start_at}"))
        if event is None:
            continue
        state.events.append(event)
        if event.state != SIGNAL:
            continue
        _try_enter(
            state,
            event,
            bar,
            clock,
            trading_date=trading_date,
            strategy_id=strategy_id,
            market_data_healthy=market_data_healthy,
            max_new_entries=max_new_entries,
            no_entry_after=no_entry_after,
        )

    _close_remaining(state)
    return _report(trading_date, state, market_data_healthy)


def load_traded_setups(path: Path | None) -> set[str]:
    """Load setup ids already traded today, so a restart cannot re-enter them."""
    if path is None or not Path(path).exists():
        return set()
    ids = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("setup_id"):
                ids.add(str(row["setup_id"]))
    return ids


def record_traded_setup(path: Path | None, setup_id: str) -> None:
    """Append a traded setup id before the position is opened in memory."""
    if path is None:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"setup_id": setup_id}, sort_keys=True) + "\n")


def _manage_position(
    state: _DayState,
    bar: MarketBar,
    clock: str,
    force_exit_at: str,
) -> None:
    position = state.positions.get(bar.symbol)
    if position is None:
        return
    position.last_bar = bar

    # Worst case first: a bar covering both levels is assumed to hit the stop.
    # Times recorded are bar.end_at: a fill inside a 5m bar is only known to
    # have happened by the time that bar closes, and labelling it with the
    # bar's start reads as a fill at a price that had not traded yet.
    if bar.low <= position.stop_price:
        _close(state, position, bar.end_at, position.stop_price, EXIT_STOP)
        return
    if bar.high >= position.target_price:
        _close(state, position, bar.end_at, position.target_price, EXIT_TARGET)
        return
    # 13:25 is a principle, not a parameter: flat before the closing auction.
    # The test is on the bar's end because that is when the fill happens, so
    # the first bar ending at or after 13:25 is the one to exit on. Testing
    # bar.start_at instead exits one bar late, at the 13:30 auction price.
    if _clock(bar.end_at) >= force_exit_at:
        _close(state, position, bar.end_at, bar.close, EXIT_FORCE)


def _try_enter(
    state: _DayState,
    event: SetupEvent,
    bar: MarketBar,
    clock: str,
    *,
    trading_date: str,
    strategy_id: str,
    market_data_healthy: bool,
    max_new_entries: int,
    no_entry_after: str,
) -> None:
    def skip(reason: str) -> None:
        state.skipped.append(
            {"setup_id": event.setup_id, "symbol": event.symbol, "at": event.at, "reason": reason}
        )

    if not market_data_healthy:
        return skip("market_data_unhealthy")
    if clock >= no_entry_after:
        return skip("after_hard_stop")
    if state.entries >= max_new_entries:
        return skip("max_new_entries")
    if event.symbol in state.positions:
        return skip("position_already_open")
    if event.entry_price is None or event.stop_price is None or event.target_price is None:
        return skip("incomplete_signal")
    if event.setup_id in state.traded_setups:
        # Survives a restart: the id is on disk, not just in this process.
        return skip("already_traded")

    intent = OrderIntent(
        trading_date=trading_date,
        strategy_id=strategy_id,
        symbol=event.symbol,
        setup_id=event.setup_id,
        side="buy",
    )
    try:
        # Never released: one setup_id can only ever produce one trade.
        state.ledger.register_intent(intent)
    except DuplicateIntentError:
        return skip("duplicate_setup")

    state.entries += 1
    state.traded_setups.add(event.setup_id)
    record_traded_setup(state.traded_setups_path, event.setup_id)
    state.positions[event.symbol] = _OpenPosition(
        setup_id=event.setup_id,
        symbol=event.symbol,
        entry_time=bar.end_at,
        entry_price=event.entry_price,
        stop_price=event.stop_price,
        target_price=event.target_price,
        last_bar=bar,
    )


def _close_remaining(state: _DayState) -> None:
    for position in list(state.positions.values()):
        bar = position.last_bar
        exit_price = bar.close if bar is not None else position.entry_price
        exit_time = bar.end_at if bar is not None else position.entry_time
        _close(state, position, exit_time, exit_price, EXIT_PRE_CLOSE)


def _close(
    state: _DayState,
    position: _OpenPosition,
    exit_time: str,
    exit_price: float,
    reason: str,
) -> None:
    risk = position.entry_price - position.stop_price
    gross_r = ((exit_price - position.entry_price) / risk) if risk else 0.0
    # Cost is charged in R so it stays comparable across a 47 dollar stock and a
    # 3870 dollar one. At these stop distances it is not a rounding term: a two
    # tick stop on a 5 dollar tick pays more than one R just to open and close.
    cost_r = state.cost_model.cost_r(position.entry_price, position.stop_price)
    tick = state.cost_model.tick_size(position.entry_price)
    state.trades.append(
        PaperTrade(
            setup_id=position.setup_id,
            symbol=position.symbol,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=reason,
            r=gross_r,
            cost_r=cost_r,
            net_r=gross_r - cost_r,
            risk_ticks=(risk / tick) if tick else 0.0,
        )
    )
    state.positions.pop(position.symbol, None)


def summarize_expectancy(trades: Sequence[Any]) -> dict[str, Any]:
    """Expectancy, profit factor and max drawdown, gross and net of cost.

    Accepts PaperTrade or the dicts they serialise to, so a multi-day funnel can
    pass accumulated rows straight in. Gross and net are always reported side by
    side: gross alone has never been the number that decides anything, and net
    alone hides whether the cost or the rule is what lost the money.
    """
    rows = [t if isinstance(t, dict) else t.to_dict() for t in trades]
    out: dict[str, Any] = {"trades": len(rows)}
    for label, key in (("gross", "r"), ("net", "net_r")):
        values = [float(row[key]) for row in rows]
        wins = [v for v in values if v > 0]
        losses = [v for v in values if v < 0]
        gain = sum(wins)
        pain = -sum(losses)
        equity = 0.0
        peak = 0.0
        drawdown = 0.0
        for value in values:
            equity += value
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        out[label] = {
            "total_r": round(sum(values), 4),
            "expectancy_r": round(sum(values) / len(values), 4) if values else 0.0,
            "win_rate": round(len(wins) / len(values), 4) if values else 0.0,
            "average_win_r": round(gain / len(wins), 4) if wins else 0.0,
            "average_loss_r": round(-pain / len(losses), 4) if losses else 0.0,
            # No trade can lose an infinite amount, but a sample with no losing
            # trade has no measurable profit factor - say so instead of dividing.
            "profit_factor": round(gain / pain, 4) if pain else None,
            "max_drawdown_r": round(drawdown, 4),
        }
    if rows:
        out["cost_r"] = {
            "median": round(sorted(float(row["cost_r"]) for row in rows)[len(rows) // 2], 4),
            "max": round(max(float(row["cost_r"]) for row in rows), 4),
        }
        out["risk_ticks"] = {
            "min": round(min(float(row["risk_ticks"]) for row in rows), 2),
            "median": round(sorted(float(row["risk_ticks"]) for row in rows)[len(rows) // 2], 2),
        }
    return out


def _report(trading_date: str, state: _DayState, healthy: bool) -> dict[str, Any]:
    trades = state.trades
    total_r = sum(trade.r for trade in trades)
    net_total_r = sum(trade.net_r for trade in trades)
    return {
        "trading_date": trading_date,
        "market_data_healthy": healthy,
        "summary": {
            "trades": len(trades),
            "entries": state.entries,
            "wins": sum(1 for trade in trades if trade.r > 0),
            "losses": sum(1 for trade in trades if trade.r < 0),
            "total_r": total_r,
            "average_r": (total_r / len(trades)) if trades else 0.0,
            "net_total_r": net_total_r,
            "average_net_r": (net_total_r / len(trades)) if trades else 0.0,
            "open_positions": len(state.positions),
            "skipped": len(state.skipped),
        },
        "expectancy": summarize_expectancy(trades),
        "trades": [trade.to_dict() for trade in trades],
        "setup_events": [event.to_dict() for event in state.events],
        "skipped": state.skipped,
    }


def _clock(start_at: str) -> str:
    return start_at[11:16]
