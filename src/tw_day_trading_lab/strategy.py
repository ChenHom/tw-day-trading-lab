from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

@dataclass(frozen=True)
class IntradaySignal:
    symbol: str
    triggered: bool
    direction: str  # "buy" or "sell"
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    reason: str = ""

class VwapBreakoutStrategy:
    """
    Opening Range / VWAP Breakout Strategy.
    - Observes the opening range (09:00 - 09:15).
    - Triggers a BUY when price crosses above the opening high with a volume surge.
    - Sets stop loss at opening range low.
    - Sets target price at entry + 2 * risk (2R).
    """

    def __init__(self, observation_minutes: int = 15, volume_surge_ratio: float = 1.5) -> None:
        self.observation_minutes = observation_minutes
        self.volume_surge_ratio = volume_surge_ratio

    def generate_signal(
        self,
        symbol: str,
        minute_bars: Sequence[Mapping[str, Any]],
        prev_close: float | None = None,
    ) -> IntradaySignal:
        if not minute_bars:
            return IntradaySignal(symbol=symbol, triggered=False, direction="buy", reason="no_data")

        # Sort bars chronologically by time
        sorted_bars = sorted(minute_bars, key=lambda b: str(b.get("time", "")))

        # Find opening range (first 15 minutes)
        opening_bars = sorted_bars[:self.observation_minutes]
        if len(opening_bars) < self.observation_minutes:
            return IntradaySignal(symbol=symbol, triggered=False, direction="buy", reason="insufficient_opening_bars")

        opening_high = max(float(bar.get("high", bar.get("max", 0))) for bar in opening_bars)
        opening_low = min(float(bar.get("low", bar.get("min", float("inf")))) for bar in opening_bars)

        # Calculate opening average volume for surge comparison
        avg_opening_volume = sum(float(bar.get("volume", bar.get("Trading_Volume", 0))) for bar in opening_bars) / len(opening_bars)

        # Look for breakout after the observation period
        for bar in sorted_bars[self.observation_minutes:]:
            close_price = float(bar.get("close", 0))
            vol = float(bar.get("volume", bar.get("Trading_Volume", 0)))

            if close_price > opening_high:
                # Check volume surge
                if avg_opening_volume > 0 and vol >= avg_opening_volume * self.volume_surge_ratio:
                    risk = opening_high - opening_low
                    if risk <= 0:
                        risk = close_price * 0.01  # Fallback to 1% risk

                    target = close_price + 2 * risk
                    return IntradaySignal(
                        symbol=symbol,
                        triggered=True,
                        direction="buy",
                        entry_price=close_price,
                        stop_price=opening_low,
                        target_price=target,
                        reason="opening_range_breakout_with_volume_surge",
                    )

        return IntradaySignal(symbol=symbol, triggered=False, direction="buy", reason="no_breakout_detected")

def calculate_atr(price_rows: Sequence[Mapping[str, Any]], period: int = 14) -> float:
    """Calculate average true range (ATR) from daily price rows."""
    if len(price_rows) < 2:
        return 0.0

    sorted_rows = sorted(price_rows, key=lambda r: str(r.get("date", "")))
    true_ranges = []

    for i in range(1, len(sorted_rows)):
        current = sorted_rows[i]
        prev = sorted_rows[i-1]

        c_high = float(current.get("max", current.get("high", 0)))
        c_low = float(current.get("min", current.get("low", 0)))
        p_close = float(prev.get("close", 0))

        tr = max(
            c_high - c_low,
            abs(c_high - p_close),
            abs(c_low - p_close)
        )
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    # Standard simple rolling ATR average
    recent_tr = true_ranges[-period:]
    return sum(recent_tr) / len(recent_tr)
