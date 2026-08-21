"""Breakout -> Retest -> Trigger setup state machine.

PA-P7 scope. One state machine per symbol, driven by canonical 5m bars plus
the RVOL result for the same bar:

    WAIT_BREAKOUT -> WAIT_RETEST -> WAIT_TRIGGER -> SIGNAL
                          |               |
                          +--> INVALIDATED <-+

The rules are deliberately fixed, not scored. Every transition has one
explicit condition so a replay can be argued about:

- **Breakout**: close above a CONFIRMED swing high, and TOD-RVOL >= the gate.
  An unconfirmed swing or a missing / insufficient RVOL never breaks out -
  a volume gate that passes when volume is unknown is not a gate.
- **Retest**: price returns to the breakout level within a bounded window
  without effectively losing it.
- **Trigger**: close above the local high formed after the retest.
- **Stop**: the retest low. Entry, stop and target are all known at signal
  time; nothing is decided later.

A breakout level is used at most once per symbol per run, so a signal cannot
re-fire off the same swing while price sits above it.

`require_rvol` / `require_retest` / `require_trigger` exist for the ablation the
repo's Research Rule demands, and for nothing else. Turning a stage off is not a
tuning knob: the arms are not nested, because production takes its stop from the
retest low, which does not exist once the retest is gone. An ablation therefore
has to put every arm on one `stop_rule` and treat the production configuration
as a separate reference arm - see docs/development-work.md 2026-08-19 (6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .bars import MarketBar, TIMEFRAME_5M
from .cost import TaiwanDayTradeCostModel, is_etf_symbol
from .rvol import RvolResult, STATUS_OK
from .structure import DEFAULT_SWING_N, DEFAULT_SWING_RULE, detect_swing_points

WAIT_BREAKOUT = "WAIT_BREAKOUT"
WAIT_RETEST = "WAIT_RETEST"
WAIT_TRIGGER = "WAIT_TRIGGER"
SIGNAL = "SIGNAL"
INVALIDATED = "INVALIDATED"

DEFAULT_BREAKOUT_RVOL = 1.5
DEFAULT_RETEST_TOLERANCE = 0.003
DEFAULT_RETEST_WINDOW = 6
DEFAULT_TARGET_R = 2.0

# Stop definitions. The retest low is production. The entry bar low exists in
# every arm, which is what makes an ablation comparable at all.
STOP_RETEST_LOW = "retest_low"
STOP_ENTRY_BAR_LOW = "entry_bar_low"
# A stop at a flat number of ticks below entry. It carries no structure at all,
# which is the point: if it matches the retest low at the same distance, then
# the retest low was never anything but a distance.
STOP_FIXED_TICKS = "fixed_ticks"
STOP_RULES = (STOP_RETEST_LOW, STOP_ENTRY_BAR_LOW, STOP_FIXED_TICKS)
DEFAULT_STOP_TICKS = 4.0
# Round-trip friction must not exceed the risk being taken. At 1.0 a trade
# pays as much to open and close as it stands to lose at the stop, so the 2R
# target is already down to 1R before the market does anything. The number is
# fixed on that principle, not picked off a table of what backtests best.
DEFAULT_MAX_COST_R = 1.0


@dataclass(frozen=True)
class SetupEvent:
    """One state transition. `state=SIGNAL` is the only tradable outcome."""

    setup_id: str
    symbol: str
    state: str
    at: str
    reason: str
    breakout_level: float | None = None
    retest_low: float | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    tod_rvol: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "state": self.state,
            "at": self.at,
            "reason": self.reason,
            "breakout_level": self.breakout_level,
            "retest_low": self.retest_low,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "tod_rvol": self.tod_rvol,
        }


@dataclass
class _SymbolSetup:
    bars: list[MarketBar] = field(default_factory=list)
    # Kept so a correction can recompute from exactly the inputs live saw.
    rvols: dict[str, RvolResult | None] = field(default_factory=dict)
    last_signal_at: str = ""
    state: str = WAIT_BREAKOUT
    setup_id: str = ""
    breakout_level: float | None = None
    retest_low: float | None = None
    trigger_level: float | None = None
    bars_since_breakout: int = 0
    used_levels: set[float] = field(default_factory=set)


class BreakoutRetestEngine:
    """Feed 5m bars in time order; get one event per state change."""

    def __init__(
        self,
        *,
        swing_n: int = DEFAULT_SWING_N,
        breakout_rvol: float = DEFAULT_BREAKOUT_RVOL,
        retest_tolerance: float = DEFAULT_RETEST_TOLERANCE,
        retest_window: int = DEFAULT_RETEST_WINDOW,
        target_r: float = DEFAULT_TARGET_R,
        timeframe: str = TIMEFRAME_5M,
        swing_rule: str = DEFAULT_SWING_RULE,
        require_rvol: bool = True,
        require_retest: bool = True,
        require_trigger: bool = True,
        stop_rule: str = STOP_RETEST_LOW,
        stop_ticks: float = DEFAULT_STOP_TICKS,
        max_cost_r: float | None = DEFAULT_MAX_COST_R,
    ) -> None:
        if stop_rule not in STOP_RULES:
            raise ValueError(f"unknown stop_rule: {stop_rule}")
        # A trigger is defined as the break of the high formed by the retest, so
        # it cannot outlive the stage it is measured against.
        if require_trigger and not require_retest:
            raise ValueError("require_trigger needs require_retest")
        if stop_rule == STOP_RETEST_LOW and not require_retest:
            raise ValueError("stop_rule=retest_low needs require_retest")
        if stop_rule == STOP_FIXED_TICKS and stop_ticks <= 0:
            raise ValueError("stop_ticks must be > 0")
        if max_cost_r is not None and max_cost_r <= 0:
            raise ValueError("max_cost_r must be > 0 or None")
        self._swing_n = swing_n
        self._swing_rule = swing_rule
        self._breakout_rvol = breakout_rvol
        self._tolerance = retest_tolerance
        self._retest_window = retest_window
        self._target_r = target_r
        self._timeframe = timeframe
        self._require_rvol = require_rvol
        self._require_retest = require_retest
        self._require_trigger = require_trigger
        self._stop_rule = stop_rule
        self._stop_ticks = stop_ticks
        self._max_cost_r = max_cost_r
        self._cost_model = TaiwanDayTradeCostModel()
        self._symbols: dict[str, _SymbolSetup] = {}
        self.corrections_applied = 0
        self.corrections_after_signal = 0
        self.signals_suppressed_by_recompute = 0
        self.stale_bars = 0

    def on_bar(self, bar: MarketBar, rvol: RvolResult | None = None) -> SetupEvent | None:
        if bar.timeframe != self._timeframe:
            return None
        state = self._symbols.setdefault(bar.symbol, _SymbolSetup())
        outcome = self._store_bar(state, bar)
        if outcome == "stale":
            return None
        if outcome == "corrected":
            return self._apply_correction(state, bar)

        state.rvols[bar.start_at] = rvol
        return self._advance(state, bar, rvol)

    def _advance(
        self,
        state: _SymbolSetup,
        bar: MarketBar,
        rvol: RvolResult | None,
    ) -> SetupEvent | None:
        if state.state == WAIT_BREAKOUT:
            return self._check_breakout(state, bar, rvol)
        if state.state == WAIT_RETEST:
            return self._check_retest(state, bar)
        if state.state == WAIT_TRIGGER:
            return self._check_trigger(state, bar)
        return None

    def _apply_correction(self, state: _SymbolSetup, bar: MarketBar) -> SetupEvent | None:
        """Recompute from corrected history without revisiting a decision.

        A correction is not a new time step. If the setup already emitted a
        SIGNAL it is audited and left alone: the position was opened on the
        information available then, and rewriting that would be look-ahead.

        Otherwise the in-flight setup is invalidated and the state machine is
        replayed over the corrected bars, so the setup that survives reflects
        the corrected data instead of starting from nothing. Any SIGNAL the
        replay would produce is suppressed and counted, not emitted - it would
        be an entry priced at a bar that closed before the correction arrived.
        """
        # A bar the signalled setup already used must not be rewound.
        if state.last_signal_at and bar.start_at <= state.last_signal_at:
            self.corrections_after_signal += 1
            return None

        event = None
        if state.state != WAIT_BREAKOUT:
            event = self._invalidate(state, bar, "corrected_bar_in_setup")

        self._reset(state)
        state.used_levels.clear()
        for stored in sorted(state.bars, key=lambda item: item.start_at):
            replayed = self._advance(state, stored, state.rvols.get(stored.start_at))
            if replayed is not None and replayed.state == SIGNAL:
                self.signals_suppressed_by_recompute += 1
        return event

    def _store_bar(self, state: _SymbolSetup, bar: MarketBar) -> str:
        """Insert or replace a bar by start_at. Returns new / corrected / stale.

        Live bars can be re-emitted at a higher revision, so appending blindly
        would leave two bars at the same timestamp and quietly corrupt swing
        detection.
        """
        for index, existing in enumerate(state.bars):
            if existing.start_at != bar.start_at:
                continue
            if bar.revision < existing.revision:
                self.stale_bars += 1
                return "stale"
            if bar.revision == existing.revision and bar.to_dict() == existing.to_dict():
                return "stale"
            state.bars[index] = bar
            self.corrections_applied += 1
            return "corrected"
        state.bars.append(bar)
        return "new"

    def state_of(self, symbol: str) -> str:
        state = self._symbols.get(symbol)
        return state.state if state else WAIT_BREAKOUT

    # -- transitions -------------------------------------------------------

    def _check_breakout(
        self,
        state: _SymbolSetup,
        bar: MarketBar,
        rvol: RvolResult | None,
    ) -> SetupEvent | None:
        highs, _lows = detect_swing_points(
            state.bars, rule=self._swing_rule, swing_n=self._swing_n
        )
        if not highs:
            return None
        level = highs[-1].price
        if level in state.used_levels or bar.close <= level:
            return None
        if self._require_rvol:
            # An unknown volume is not a passing volume gate.
            if rvol is None or rvol.status != STATUS_OK or rvol.tod_rvol is None:
                return None
            if rvol.tod_rvol < self._breakout_rvol:
                return None
        measured_rvol = rvol.tod_rvol if rvol is not None else None

        state.used_levels.add(level)
        state.state = WAIT_RETEST
        state.setup_id = f"{bar.start_at[:10]}:{bar.symbol}:{bar.start_at}:brk"
        state.breakout_level = level
        state.bars_since_breakout = 0
        state.retest_low = None
        state.trigger_level = None
        if not self._require_retest:
            return self._emit_signal(
                state, bar, "breakout_close_above_swing_high", tod_rvol=measured_rvol
            )
        return SetupEvent(
            setup_id=state.setup_id,
            symbol=bar.symbol,
            state=WAIT_RETEST,
            at=bar.start_at,
            reason="breakout_confirmed",
            breakout_level=level,
            tod_rvol=measured_rvol,
        )

    def _check_retest(self, state: _SymbolSetup, bar: MarketBar) -> SetupEvent | None:
        level = state.breakout_level or 0.0
        state.bars_since_breakout += 1

        if bar.close < level * (1 - self._tolerance):
            return self._invalidate(state, bar, "breakout_level_lost")

        if bar.low <= level * (1 + self._tolerance):
            state.state = WAIT_TRIGGER
            state.retest_low = bar.low
            state.trigger_level = bar.high
            if not self._require_trigger:
                return self._emit_signal(state, bar, "retest_accepted_no_trigger")
            return SetupEvent(
                setup_id=state.setup_id,
                symbol=bar.symbol,
                state=WAIT_TRIGGER,
                at=bar.start_at,
                reason="retest_accepted",
                breakout_level=level,
                retest_low=bar.low,
            )

        if state.bars_since_breakout >= self._retest_window:
            return self._invalidate(state, bar, "retest_window_expired")
        return None

    def _check_trigger(self, state: _SymbolSetup, bar: MarketBar) -> SetupEvent | None:
        retest_low = state.retest_low or 0.0
        if bar.low < retest_low:
            return self._invalidate(state, bar, "retest_low_lost")

        trigger_level = state.trigger_level or 0.0
        if bar.close > trigger_level:
            return self._emit_signal(state, bar, "trigger_break_local_high")

        state.trigger_level = max(trigger_level, bar.high)
        return None

    def _emit_signal(
        self,
        state: _SymbolSetup,
        bar: MarketBar,
        reason: str,
        *,
        tod_rvol: float | None = None,
    ) -> SetupEvent:
        """Price the signal and close the setup. Entry, stop and target are all
        decided here, on this bar, so nothing about the trade is settled later.
        """
        entry = bar.close
        etf = is_etf_symbol(bar.symbol)
        if self._stop_rule == STOP_RETEST_LOW:
            stop = state.retest_low
        elif self._stop_rule == STOP_FIXED_TICKS:
            stop = entry - self._stop_ticks * self._cost_model.tick_size(entry, etf=etf)
        else:
            stop = bar.low
        risk = entry - stop if stop is not None else 0.0
        if risk <= 0:
            return self._invalidate(state, bar, "non_positive_risk")
        # Cost is knowable here, before the outcome is: it falls out of the
        # entry price and the stop distance. A two tick stop on a high priced
        # stock pays more in friction than it risks, which no hit rate can
        # repair, so the setup should not produce the signal at all.
        if self._max_cost_r is not None:
            cost_r = self._cost_model.cost_r(entry, stop, etf=etf)
            if cost_r >= self._max_cost_r:
                return self._invalidate(state, bar, "cost_exceeds_risk")
        event = SetupEvent(
            setup_id=state.setup_id,
            symbol=bar.symbol,
            state=SIGNAL,
            at=bar.start_at,
            reason=reason,
            breakout_level=state.breakout_level,
            retest_low=state.retest_low,
            entry_price=entry,
            stop_price=stop,
            target_price=entry + self._target_r * risk,
            tod_rvol=tod_rvol,
        )
        state.last_signal_at = bar.start_at
        self._reset(state)
        return event

    def _invalidate(self, state: _SymbolSetup, bar: MarketBar, reason: str) -> SetupEvent:
        event = SetupEvent(
            setup_id=state.setup_id,
            symbol=bar.symbol,
            state=INVALIDATED,
            at=bar.start_at,
            reason=reason,
            breakout_level=state.breakout_level,
            retest_low=state.retest_low,
        )
        self._reset(state)
        return event

    def _reset(self, state: _SymbolSetup) -> None:
        state.state = WAIT_BREAKOUT
        state.setup_id = ""
        state.breakout_level = None
        state.retest_low = None
        state.trigger_level = None
        state.bars_since_breakout = 0


def run_setup_engine(
    bars: Sequence[MarketBar],
    rvol_by_start: dict[str, RvolResult] | None = None,
    **options: Any,
) -> list[SetupEvent]:
    """Replay a bar sequence through the engine and return every event."""
    engine = BreakoutRetestEngine(**options)
    lookup = rvol_by_start or {}
    events = []
    for bar in sorted(bars, key=lambda item: (item.start_at, item.symbol)):
        event = engine.on_bar(bar, lookup.get(f"{bar.symbol}|{bar.start_at}"))
        if event is not None:
            events.append(event)
    return events
