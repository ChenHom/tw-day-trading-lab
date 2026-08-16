"""Paper trading daily cycle: canonical bars in, closed trades out.

PA-P8 scope. Wires PA-P1..P7 together for one trading day and writes only to
a paper ledger - no broker, no order, no cancel.

Rules that are not negotiable, because each one exists to stop a specific way
of fooling yourself:

- **Stop is checked before target inside the same bar.** A 5m bar that spans
  both is ambiguous; assuming the good outcome would inflate expectancy on
  exactly the volatile bars that matter.
- **One trade per setup_id, ever.** The ledger key is never released, so a
  replay or a restart cannot re-enter the same setup.
- **No new entry while market data is unhealthy.** PA-P1 reports the health;
  a signal computed from a feed that dropped ticks is not a signal.
- **Every position is closed before the day ends.** Force exit at the
  configured time, and anything still open at the last bar is closed there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .bars import MarketBar
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
) -> dict[str, Any]:
    """Replay one trading day and return trades plus an audit of what was skipped."""
    engine = engine or BreakoutRetestEngine()
    lookup = rvol_by_key or {}
    state = _DayState()

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
    if bar.low <= position.stop_price:
        _close(state, position, bar.start_at, position.stop_price, EXIT_STOP)
        return
    if bar.high >= position.target_price:
        _close(state, position, bar.start_at, position.target_price, EXIT_TARGET)
        return
    if clock >= force_exit_at:
        _close(state, position, bar.start_at, bar.close, EXIT_FORCE)


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
    state.positions[event.symbol] = _OpenPosition(
        setup_id=event.setup_id,
        symbol=event.symbol,
        entry_time=event.at,
        entry_price=event.entry_price,
        stop_price=event.stop_price,
        target_price=event.target_price,
        last_bar=bar,
    )


def _close_remaining(state: _DayState) -> None:
    for position in list(state.positions.values()):
        bar = position.last_bar
        exit_price = bar.close if bar is not None else position.entry_price
        exit_time = bar.start_at if bar is not None else position.entry_time
        _close(state, position, exit_time, exit_price, EXIT_PRE_CLOSE)


def _close(
    state: _DayState,
    position: _OpenPosition,
    exit_time: str,
    exit_price: float,
    reason: str,
) -> None:
    risk = position.entry_price - position.stop_price
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
            r=((exit_price - position.entry_price) / risk) if risk else 0.0,
        )
    )
    state.positions.pop(position.symbol, None)


def _report(trading_date: str, state: _DayState, healthy: bool) -> dict[str, Any]:
    trades = state.trades
    total_r = sum(trade.r for trade in trades)
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
            "open_positions": len(state.positions),
            "skipped": len(state.skipped),
        },
        "trades": [trade.to_dict() for trade in trades],
        "setup_events": [event.to_dict() for event in state.events],
        "skipped": state.skipped,
    }


def _clock(start_at: str) -> str:
    return start_at[11:16]
