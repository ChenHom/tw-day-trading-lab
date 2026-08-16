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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .bars import MarketBar, TIMEFRAME_5M
from .rvol import RvolResult, STATUS_OK
from .structure import DEFAULT_SWING_N, find_swing_points

WAIT_BREAKOUT = "WAIT_BREAKOUT"
WAIT_RETEST = "WAIT_RETEST"
WAIT_TRIGGER = "WAIT_TRIGGER"
SIGNAL = "SIGNAL"
INVALIDATED = "INVALIDATED"

DEFAULT_BREAKOUT_RVOL = 1.5
DEFAULT_RETEST_TOLERANCE = 0.003
DEFAULT_RETEST_WINDOW = 6
DEFAULT_TARGET_R = 2.0


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
    ) -> None:
        self._swing_n = swing_n
        self._breakout_rvol = breakout_rvol
        self._tolerance = retest_tolerance
        self._retest_window = retest_window
        self._target_r = target_r
        self._timeframe = timeframe
        self._symbols: dict[str, _SymbolSetup] = {}

    def on_bar(self, bar: MarketBar, rvol: RvolResult | None = None) -> SetupEvent | None:
        if bar.timeframe != self._timeframe:
            return None
        state = self._symbols.setdefault(bar.symbol, _SymbolSetup())
        state.bars.append(bar)

        if state.state == WAIT_BREAKOUT:
            return self._check_breakout(state, bar, rvol)
        if state.state == WAIT_RETEST:
            return self._check_retest(state, bar)
        if state.state == WAIT_TRIGGER:
            return self._check_trigger(state, bar)
        return None

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
        highs, _lows = find_swing_points(state.bars, swing_n=self._swing_n)
        if not highs:
            return None
        level = highs[-1].price
        if level in state.used_levels or bar.close <= level:
            return None
        # An unknown volume is not a passing volume gate.
        if rvol is None or rvol.status != STATUS_OK or rvol.tod_rvol is None:
            return None
        if rvol.tod_rvol < self._breakout_rvol:
            return None

        state.used_levels.add(level)
        state.state = WAIT_RETEST
        state.setup_id = f"{bar.start_at[:10]}:{bar.symbol}:{bar.start_at}:brk"
        state.breakout_level = level
        state.bars_since_breakout = 0
        state.retest_low = None
        state.trigger_level = None
        return SetupEvent(
            setup_id=state.setup_id,
            symbol=bar.symbol,
            state=WAIT_RETEST,
            at=bar.start_at,
            reason="breakout_confirmed",
            breakout_level=level,
            tod_rvol=rvol.tod_rvol,
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
            entry = bar.close
            stop = retest_low
            risk = entry - stop
            if risk <= 0:
                return self._invalidate(state, bar, "non_positive_risk")
            event = SetupEvent(
                setup_id=state.setup_id,
                symbol=bar.symbol,
                state=SIGNAL,
                at=bar.start_at,
                reason="trigger_break_local_high",
                breakout_level=state.breakout_level,
                retest_low=retest_low,
                entry_price=entry,
                stop_price=stop,
                target_price=entry + self._target_r * risk,
            )
            self._reset(state)
            return event

        state.trigger_level = max(trigger_level, bar.high)
        return None

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
