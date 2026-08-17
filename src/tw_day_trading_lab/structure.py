"""Swing points and market structure from canonical bars.

PA-P6 scope. Pure function of a bar list: same bars in, same structure out,
which is what lets a live session, a replay and a correction rerun agree.

A swing is only confirmed once `swing_n` bars exist on BOTH sides of it, so
the newest bars are deliberately not swings yet. Calling the current bar a
swing high the moment it prints would let structure flip on noise and would
make live and replay disagree.

Missing bars are never treated as price 0: PA-P2 emits no bar for a minute
nobody traded, so neighbours are the neighbouring bars in the list, not the
neighbouring clock buckets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .bars import MarketBar, TIMEFRAME_5M

DEFAULT_SWING_N = 2
# V1 production rule. Chosen from a 20-day x 3-symbol yield measurement: the
# strict rule left 2330 with 1 of 20 days classifiable because Taiwan tick
# sizes make 5m highs repeat. See docs/price-action-p6-p8-design.md.
DEFAULT_SWING_RULE = "plateau"

TREND_UP = "UP"
TREND_DOWN = "DOWN"
TREND_RANGE = "RANGE"
TREND_UNKNOWN = "UNKNOWN"

STRUCTURE_HH_HL = "HH_HL"
STRUCTURE_LH_LL = "LH_LL"
STRUCTURE_MIXED = "MIXED"
STRUCTURE_UNKNOWN = "UNKNOWN"

BOS_BULLISH = "bullish"
BOS_BEARISH = "bearish"
BOS_NONE = "none"


@dataclass(frozen=True)
class SwingPoint:
    kind: str
    start_at: str
    price: float

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "start_at": self.start_at, "price": self.price}


@dataclass(frozen=True)
class MarketStructure:
    symbol: str
    timeframe: str
    as_of: str
    trend: str
    structure: str
    swing_highs: tuple[SwingPoint, ...]
    swing_lows: tuple[SwingPoint, ...]
    last_swing_high: float | None
    last_swing_low: float | None
    bos: str
    bos_level: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of,
            "trend": self.trend,
            "structure": self.structure,
            "last_swing_high": self.last_swing_high,
            "last_swing_low": self.last_swing_low,
            "bos": self.bos,
            "bos_level": self.bos_level,
            "swing_highs": [point.to_dict() for point in self.swing_highs],
            "swing_lows": [point.to_dict() for point in self.swing_lows],
        }


def find_swing_points(
    bars: Sequence[MarketBar],
    *,
    swing_n: int = DEFAULT_SWING_N,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Return (swing_highs, swing_lows) confirmed by `swing_n` bars each side.

    Strictly greater / strictly less on both sides, so an equal neighbour is
    not a swing and the result never depends on tie-breaking.
    """
    ordered = sorted(bars, key=lambda bar: bar.start_at)
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    for index in range(swing_n, len(ordered) - swing_n):
        bar = ordered[index]
        left = ordered[index - swing_n : index]
        right = ordered[index + 1 : index + 1 + swing_n]
        if all(bar.high > other.high for other in left) and all(
            bar.high > other.high for other in right
        ):
            highs.append(SwingPoint(kind="high", start_at=bar.start_at, price=bar.high))
        if all(bar.low < other.low for other in left) and all(
            bar.low < other.low for other in right
        ):
            lows.append(SwingPoint(kind="low", start_at=bar.start_at, price=bar.low))
    return highs, lows


def compute_structure(
    bars: Sequence[MarketBar],
    *,
    swing_n: int = DEFAULT_SWING_N,
    timeframe: str = TIMEFRAME_5M,
    rule: str = DEFAULT_SWING_RULE,
) -> MarketStructure:
    """Classify HH/HL/LH/LL and detect a break of structure."""
    ordered = sorted(
        (bar for bar in bars if bar.timeframe == timeframe), key=lambda bar: bar.start_at
    )
    symbol = ordered[-1].symbol if ordered else ""
    as_of = ordered[-1].start_at if ordered else ""
    highs, lows = detect_swing_points(ordered, rule=rule, swing_n=swing_n)

    structure = _classify(highs, lows)
    trend = {
        STRUCTURE_HH_HL: TREND_UP,
        STRUCTURE_LH_LL: TREND_DOWN,
        STRUCTURE_MIXED: TREND_RANGE,
    }.get(structure, TREND_UNKNOWN)

    last_high = highs[-1].price if highs else None
    last_low = lows[-1].price if lows else None
    bos, bos_level = _break_of_structure(ordered, last_high, last_low)

    return MarketStructure(
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of,
        trend=trend,
        structure=structure,
        swing_highs=tuple(highs),
        swing_lows=tuple(lows),
        last_swing_high=last_high,
        last_swing_low=last_low,
        bos=bos,
        bos_level=bos_level,
    )


def _classify(highs: Sequence[SwingPoint], lows: Sequence[SwingPoint]) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return STRUCTURE_UNKNOWN
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    if higher_high and higher_low:
        return STRUCTURE_HH_HL
    if not higher_high and not higher_low:
        return STRUCTURE_LH_LL
    return STRUCTURE_MIXED


def _break_of_structure(
    ordered: Sequence[MarketBar],
    last_high: float | None,
    last_low: float | None,
) -> tuple[str, float | None]:
    if not ordered:
        return BOS_NONE, None
    close = ordered[-1].close
    if last_high is not None and close > last_high:
        return BOS_BULLISH, last_high
    if last_low is not None and close < last_low:
        return BOS_BEARISH, last_low
    return BOS_NONE, None

# -- alternative swing rules, for PA-P6 yield comparison only ----------------
#
# `find_swing_points` (rule A, strict) stays the default. These are here to be
# measured against 20 days of real bars before any rule change is decided:
# on 2026-08-17 rule A found 0 / 0 / 1 swing highs on 2330 / 2317 / 2454,
# because Taiwan tick sizes make 5m highs repeat and a strictly-greater test
# rejects every equal neighbour.

DEFAULT_DIRECTIONAL_TICKS = 2


def find_swing_points_plateau(
    bars: Sequence[MarketBar],
    *,
    swing_n: int = DEFAULT_SWING_N,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Rule B: consecutive equal extremes count as ONE plateau, not many bars.

    Answers the question `>=` leaves open - four bars sharing a high are one
    high, not four - without letting a flat stretch become a swing.
    A plateau is anchored at its last bar, which is when it is complete.
    """
    ordered = sorted(bars, key=lambda bar: bar.start_at)
    highs = _plateau_swings(ordered, [bar.high for bar in ordered], swing_n, "high", higher=True)
    lows = _plateau_swings(ordered, [bar.low for bar in ordered], swing_n, "low", higher=False)
    return highs, lows


def find_swing_points_directional(
    bars: Sequence[MarketBar],
    *,
    min_ticks: float = DEFAULT_DIRECTIONAL_TICKS,
    tick_size: Callable[[float], float] | None = None,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Rule C: an extreme is confirmed once price reverses `min_ticks` from it.

    Bar-count confirmation is replaced by a price-move confirmation, so a
    tight range simply produces no swings instead of producing noise, and a
    real reversal is recognised as soon as it happens.
    """
    ordered = sorted(bars, key=lambda bar: bar.start_at)
    if not ordered:
        return [], []
    size = tick_size or _default_tick_size
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    state = ""
    candidate_high = candidate_low = ordered[0]

    for bar in ordered[1:]:
        if bar.high > candidate_high.high:
            candidate_high = bar
        if bar.low < candidate_low.low:
            candidate_low = bar

        if state != "down":
            if candidate_high.high - bar.low >= min_ticks * size(candidate_high.high):
                highs.append(
                    SwingPoint(kind="high", start_at=candidate_high.start_at, price=candidate_high.high)
                )
                state, candidate_low = "down", bar
                continue
        if state != "up":
            if bar.high - candidate_low.low >= min_ticks * size(candidate_low.low):
                lows.append(
                    SwingPoint(kind="low", start_at=candidate_low.start_at, price=candidate_low.low)
                )
                state, candidate_high = "up", bar
    return highs, lows


SWING_RULES: dict[str, Callable[..., tuple[list[SwingPoint], list[SwingPoint]]]] = {
    "strict": find_swing_points,
    "plateau": find_swing_points_plateau,
    "directional": find_swing_points_directional,
}


def _plateau_swings(
    ordered: Sequence[MarketBar],
    values: Sequence[float],
    swing_n: int,
    kind: str,
    *,
    higher: bool,
) -> list[SwingPoint]:
    points = []
    for start, end, value in _runs(values):
        if start - swing_n < 0 or end + swing_n >= len(values):
            continue
        left = values[start - swing_n : start]
        right = values[end + 1 : end + 1 + swing_n]
        if higher:
            ok = all(value > other for other in left) and all(value > other for other in right)
        else:
            ok = all(value < other for other in left) and all(value < other for other in right)
        if ok:
            points.append(SwingPoint(kind=kind, start_at=ordered[end].start_at, price=value))
    return points


def _runs(values: Sequence[float]) -> list[tuple[int, int, float]]:
    """Group consecutive equal values into (start_index, end_index, value)."""
    runs = []
    start = 0
    for index in range(1, len(values) + 1):
        if index == len(values) or values[index] != values[start]:
            runs.append((start, index - 1, values[start]))
            start = index
    return runs


def _default_tick_size(price: float) -> float:
    from .cost import TaiwanDayTradeCostModel

    return TaiwanDayTradeCostModel().tick_size(price)


def detect_swing_points(
    bars: Sequence[MarketBar],
    *,
    rule: str = DEFAULT_SWING_RULE,
    swing_n: int = DEFAULT_SWING_N,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Dispatch to a named swing rule.

    `directional` confirms on price reversal, so a bar count has no meaning
    there and `swing_n` is not passed to it.
    """
    if rule not in SWING_RULES:
        raise ValueError(f"unknown swing rule: {rule}")
    if rule == "directional":
        return find_swing_points_directional(bars)
    return SWING_RULES[rule](bars, swing_n=swing_n)
