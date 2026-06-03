from __future__ import annotations

import unittest
from typing import Any

from tw_day_trading_lab.market_regime import MarketRegimeResult, compute_market_regime


def _make_rows(
    n: int,
    *,
    close_start: float = 100.0,
    close_step: float = 1.0,
    high_offset: float = 2.0,
    low_offset: float = 2.0,
    open_offset: float = 0.0,
    stock_id: str = "0050",
    date_prefix: str = "2026-01-",
) -> list[dict[str, Any]]:
    """Generate n synthetic price rows with steadily rising close."""
    rows = []
    for i in range(n):
        day = i + 1
        close = close_start + i * close_step
        rows.append(
            {
                "date": f"{date_prefix}{day:02d}",
                "stock_id": stock_id,
                "close": close,
                "open": close + open_offset,
                "max": close + high_offset,
                "min": close - low_offset,
            }
        )
    return rows


class TestComputeMarketRegime(unittest.TestCase):

    def test_bullish_regime(self):
        """Steadily rising close above EMA20, decent ATR, no gap down → bullish."""
        # 25 rows close from 100 to 124, step=1
        # EMA20 seed = mean(100..119) = 109.5; by row 25 close=124 > ema
        # ATR = high_offset + low_offset = 4 per day (constant), last_close=124
        # atr5d_pct = 4 / 124 * 100 ≈ 3.2% >= 0.5 ✓
        # open = close + 0, prev_close = 123 → gap = (124 - 123) / 123 * 100 ≈ 0.81% > -1.5 ✓
        rows = _make_rows(25, close_start=100.0, close_step=1.0, high_offset=2.0, low_offset=2.0)
        result = compute_market_regime(rows)
        self.assertEqual(result.regime, "bullish")
        self.assertGreater(result.last_close, result.ema20)
        self.assertGreaterEqual(result.atr5d_pct, 0.5)
        self.assertGreater(result.gap_open_pct, -1.5)

    def test_bearish_close_below_ema20(self):
        """last_close < ema20 → bearish_skip (regardless of ATR/gap)."""
        # 25 rows: first 24 rows at close=100 to 123 (rising), then last row at 80
        # EMA20 will be ~100+ after seeding; close=80 < ema → bearish
        rows = _make_rows(24, close_start=100.0, close_step=1.0, high_offset=2.0, low_offset=2.0)
        # Override last row with a big drop
        rows.append(
            {
                "date": "2026-01-25",
                "stock_id": "0050",
                "close": 80.0,
                "open": 80.0,
                "max": 82.0,
                "min": 78.0,
            }
        )
        result = compute_market_regime(rows)
        self.assertEqual(result.regime, "bearish_skip")
        self.assertIn("close_below_ema20", result.reasons)

    def test_bearish_low_atr(self):
        """atr5d_pct < 0.5 → bearish_skip (flat market, no volatility)."""
        # All 25 rows at same close=100, high=100.01, low=99.99
        # TR ≈ 0.02 per day, atr5d_pct ≈ 0.02/100*100 = 0.02 < 0.5
        rows = _make_rows(
            25,
            close_start=100.0,
            close_step=0.0,
            high_offset=0.01,
            low_offset=0.01,
        )
        result = compute_market_regime(rows)
        self.assertEqual(result.regime, "bearish_skip")
        self.assertIn("low_atr5d", result.reasons)

    def test_bearish_gap_down(self):
        """gap_open_pct <= -1.5 → bearish_skip."""
        # Build 25 normal rows then patch last row's open to create -3% gap
        rows = _make_rows(25, close_start=100.0, close_step=1.0, high_offset=2.0, low_offset=2.0)
        # prev_close of last row = rows[-2]['close'] = 123
        # set open so gap = (open - 123) / 123 * 100 = -3%  → open ≈ 119.31
        rows[-1] = dict(rows[-1])
        rows[-1]["open"] = 119.31  # ~-3% gap
        result = compute_market_regime(rows)
        self.assertEqual(result.regime, "bearish_skip")
        self.assertIn("gap_down", result.reasons)

    def test_insufficient_history(self):
        """Fewer than 21 rows → regime='neutral', reasons include 'insufficient_history'."""
        rows = _make_rows(15, close_start=100.0, close_step=1.0)
        result = compute_market_regime(rows)
        self.assertEqual(result.regime, "neutral")
        self.assertIn("insufficient_history", result.reasons)
        self.assertEqual(result.ema20, 0.0)

    def test_exactly_21_rows_works(self):
        """21 rows is the minimum to compute a result (not neutral/insufficient)."""
        rows = _make_rows(21, close_start=100.0, close_step=1.0, high_offset=2.0, low_offset=2.0)
        result = compute_market_regime(rows)
        # Should not return insufficient_history
        self.assertNotIn("insufficient_history", result.reasons)
        self.assertIn(result.regime, ("bullish", "neutral", "bearish_skip"))

    def test_neutral_regime_edge(self):
        """Verify neutral is not accidentally overwritten to bearish when no conditions trigger."""
        # Construct a case where all is good → expect bullish or neutral, not bearish
        rows = _make_rows(25, close_start=100.0, close_step=1.0, high_offset=2.0, low_offset=2.0)
        result = compute_market_regime(rows)
        self.assertNotEqual(result.regime, "bearish_skip")


if __name__ == "__main__":
    unittest.main()
