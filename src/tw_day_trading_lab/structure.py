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
from typing import Any, Sequence

from .bars import MarketBar, TIMEFRAME_5M

DEFAULT_SWING_N = 2

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
) -> MarketStructure:
    """Classify HH/HL/LH/LL and detect a break of structure."""
    ordered = sorted(
        (bar for bar in bars if bar.timeframe == timeframe), key=lambda bar: bar.start_at
    )
    symbol = ordered[-1].symbol if ordered else ""
    as_of = ordered[-1].start_at if ordered else ""
    highs, lows = find_swing_points(ordered, swing_n=swing_n)

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
