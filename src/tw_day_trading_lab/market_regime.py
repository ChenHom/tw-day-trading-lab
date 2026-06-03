from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class MarketRegimeResult:
    regime: str  # 'bullish' | 'neutral' | 'bearish_skip'
    ema20: float
    last_close: float
    atr5d_pct: float
    gap_open_pct: float  # today's open vs yesterday's close
    reasons: tuple[str, ...]


def compute_market_regime(
    price_rows: Sequence[Mapping[str, Any]],
    *,
    min_atr5d_pct: float = 0.5,
    gap_down_threshold_pct: float = -1.5,
) -> MarketRegimeResult:
    """Classify market regime from price rows (e.g. 0050 history).

    Rules (bearish_skip if ANY applies):
    - EMA20 trend is down: last_close < ema20
    - 5-day ATR% is below min_atr5d_pct (low volatility = momentum strategies fail)
    - Today's gap open vs yesterday's close <= gap_down_threshold_pct

    Returns 'bullish' if none of the bearish conditions apply and
    last_close >= ema20. Otherwise 'neutral' unless gap_down alone triggers
    'bearish_skip'.
    """
    if len(price_rows) < 21:
        return MarketRegimeResult(
            regime="neutral",
            ema20=0.0,
            last_close=0.0,
            atr5d_pct=0.0,
            gap_open_pct=0.0,
            reasons=("insufficient_history",),
        )

    sorted_rows = sorted(price_rows, key=lambda r: str(r.get("date", "")))

    # --- EMA20 ---
    k = 2.0 / (20 + 1)
    closes = [float(r["close"]) for r in sorted_rows]
    ema = sum(closes[:20]) / 20.0
    for close in closes[20:]:
        ema = close * k + ema * (1 - k)

    last_close = closes[-1]

    # --- 5-day ATR% ---
    last5 = sorted_rows[-5:]
    true_ranges: list[float] = []
    for i, row in enumerate(last5):
        high = float(row.get("max", row.get("high", 0)))
        low = float(row.get("min", row.get("low", 0)))
        # Find the previous row's close within sorted_rows
        row_idx = sorted_rows.index(row)
        if row_idx > 0:
            prev_close = float(sorted_rows[row_idx - 1]["close"])
        else:
            prev_close = low  # fallback: no previous row
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    avg_atr = sum(true_ranges) / len(true_ranges) if true_ranges else 0.0
    atr5d_pct = (avg_atr / last_close * 100) if last_close > 0 else 0.0

    # --- Gap open% ---
    today_open = float(sorted_rows[-1].get("open", sorted_rows[-1]["close"]))
    prev_close = float(sorted_rows[-2]["close"])
    gap_open_pct = (
        (today_open - prev_close) / prev_close * 100
    ) if prev_close > 0 else 0.0

    # --- Regime logic ---
    reasons: list[str] = []
    bearish = False

    if last_close < ema:
        reasons.append("close_below_ema20")
        bearish = True

    if atr5d_pct < min_atr5d_pct:
        reasons.append("low_atr5d")
        bearish = True

    if gap_open_pct <= gap_down_threshold_pct:
        reasons.append("gap_down")
        bearish = True

    if bearish:
        regime = "bearish_skip"
    elif last_close >= ema and atr5d_pct >= min_atr5d_pct and gap_open_pct > gap_down_threshold_pct:
        regime = "bullish"
        reasons.append("all_conditions_met")
    else:
        regime = "neutral"

    return MarketRegimeResult(
        regime=regime,
        ema20=round(ema, 4),
        last_close=round(last_close, 4),
        atr5d_pct=round(atr5d_pct, 4),
        gap_open_pct=round(gap_open_pct, 4),
        reasons=tuple(reasons),
    )
