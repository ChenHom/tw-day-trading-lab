import unittest

from tw_day_trading_lab.bars import STATUS_CLOSED, MarketBar
from tw_day_trading_lab.structure import (
    BOS_BEARISH,
    BOS_BULLISH,
    BOS_NONE,
    STRUCTURE_HH_HL,
    STRUCTURE_LH_LL,
    STRUCTURE_MIXED,
    STRUCTURE_UNKNOWN,
    TREND_DOWN,
    TREND_RANGE,
    TREND_UNKNOWN,
    TREND_UP,
    compute_structure,
    find_swing_points,
)

DATE = "2026-08-17"

# Zigzag with swing_n=2 pivots: swing lows at index 2 and 8, swing highs at 5 and 11.
HH_HL = [
    (25, 20), (22, 15), (18, 10), (20, 14), (24, 18),
    (30, 22), (26, 20), (22, 16), (20, 12), (24, 16),
    (28, 20), (34, 24), (30, 22), (28, 21),
]
# Reversed in time: highs 34 then 30, lows 12 then 10.
LH_LL = list(reversed(HH_HL))
# Same highs as HH_HL, but the second swing low drops below the first.
MIXED = [pair if index != 8 else (20, 8) for index, pair in enumerate(HH_HL)]


def bar(index, high, low, close=None, symbol="2330", timeframe="5m"):
    minute = index * 5
    hour, minute = 9 + minute // 60, minute % 60
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        start_at=f"{DATE}T{hour:02d}:{minute:02d}:00",
        end_at=f"{DATE}T{hour:02d}:{minute + 5:02d}:00" if minute < 55 else f"{DATE}T{hour + 1:02d}:00:00",
        open=low,
        high=high,
        low=low,
        close=close if close is not None else (high + low) / 2,
        volume=1000,
        trade_count=1,
        status=STATUS_CLOSED,
        revision=1,
        source="local_1m_aggregated",
    )


def series(points):
    """points: list of (high, low) or (high, low, close)."""
    return [bar(index, *point) for index, point in enumerate(points)]


class SwingDetectionTest(unittest.TestCase):
    def test_swing_high_needs_n_lower_bars_on_both_sides(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5)])

        highs, _ = find_swing_points(bars, swing_n=2)

        self.assertEqual([point.price for point in highs], [15])
        self.assertEqual(highs[0].start_at, f"{DATE}T09:10:00")

    def test_swing_low_needs_n_higher_bars_on_both_sides(self):
        bars = series([(12, 8), (11, 7), (10, 3), (11, 6), (12, 7)])

        _, lows = find_swing_points(bars, swing_n=2)

        self.assertEqual([point.price for point in lows], [3])

    def test_newest_bars_are_not_confirmed_swings(self):
        """A swing needs n bars after it, so the tail cannot be a swing yet."""
        bars = series([(10, 5), (11, 6), (20, 7)])

        highs, _ = find_swing_points(bars, swing_n=2)

        self.assertEqual(highs, [])

    def test_equal_neighbour_is_not_a_swing(self):
        bars = series([(10, 5), (15, 6), (15, 7), (12, 6), (11, 5)])

        highs, _ = find_swing_points(bars, swing_n=2)

        self.assertEqual(highs, [])

    def test_detection_is_deterministic_across_runs(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5), (13, 8), (9, 4), (10, 5)])

        runs = [
            [point.to_dict() for point in find_swing_points(bars, swing_n=2)[0]] for _ in range(3)
        ]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])


class StructureClassificationTest(unittest.TestCase):
    def test_higher_high_and_higher_low_is_an_uptrend(self):
        result = compute_structure(series(HH_HL), swing_n=2)

        self.assertEqual([p.price for p in result.swing_highs], [30, 34])
        self.assertEqual([p.price for p in result.swing_lows], [10, 12])
        self.assertEqual(result.structure, STRUCTURE_HH_HL)
        self.assertEqual(result.trend, TREND_UP)
        self.assertEqual(result.last_swing_high, 34)

    def test_lower_high_and_lower_low_is_a_downtrend(self):
        result = compute_structure(series(LH_LL), swing_n=2)

        self.assertEqual([p.price for p in result.swing_highs], [34, 30])
        self.assertEqual([p.price for p in result.swing_lows], [12, 10])
        self.assertEqual(result.structure, STRUCTURE_LH_LL)
        self.assertEqual(result.trend, TREND_DOWN)

    def test_mixed_swings_are_a_range(self):
        result = compute_structure(series(MIXED), swing_n=2)

        # Higher high but lower low: neither a clean uptrend nor downtrend.
        self.assertEqual([p.price for p in result.swing_highs], [30, 34])
        self.assertEqual([p.price for p in result.swing_lows], [10, 8])
        self.assertEqual(result.structure, STRUCTURE_MIXED)
        self.assertEqual(result.trend, TREND_RANGE)

    def test_too_few_swings_is_unknown_not_a_guess(self):
        result = compute_structure(series([(10, 5), (11, 6), (15, 7)]), swing_n=2)

        self.assertEqual(result.structure, STRUCTURE_UNKNOWN)
        self.assertEqual(result.trend, TREND_UNKNOWN)
        self.assertEqual(result.bos, BOS_NONE)

    def test_other_timeframes_are_ignored(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5)])
        noise = MarketBar(**{**bars[0].to_dict(), "timeframe": "1m", "high": 999})

        highs = compute_structure([*bars, noise], swing_n=2).swing_highs

        self.assertEqual([point.price for point in highs], [15])


class BreakOfStructureTest(unittest.TestCase):
    def test_close_above_the_last_swing_high_is_a_bullish_bos(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5), (20, 9, 19)])

        result = compute_structure(bars, swing_n=2)

        self.assertEqual(result.bos, BOS_BULLISH)
        self.assertEqual(result.bos_level, 15)

    def test_close_below_the_last_swing_low_is_a_bearish_bos(self):
        bars = series([(12, 8), (11, 7), (10, 3), (11, 6), (12, 7), (9, 1, 2)])

        result = compute_structure(bars, swing_n=2)

        self.assertEqual(result.bos, BOS_BEARISH)
        self.assertEqual(result.bos_level, 3)

    def test_inside_the_range_is_no_bos(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5), (13, 8, 12)])

        self.assertEqual(compute_structure(bars, swing_n=2).bos, BOS_NONE)


class RecomputeTest(unittest.TestCase):
    def test_a_corrected_bar_changes_the_structure(self):
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5)])
        before = compute_structure(bars, swing_n=2)

        corrected = list(bars)
        corrected[2] = MarketBar(
            **{**bars[2].to_dict(), "high": 9.5, "revision": 2, "status": "CORRECTED"}
        )
        after = compute_structure(corrected, swing_n=2)

        self.assertEqual(before.last_swing_high, 15)
        self.assertIsNone(after.last_swing_high)

    def test_same_bars_always_produce_the_same_structure(self):
        bars = series(HH_HL)

        runs = [compute_structure(bars, swing_n=2).to_dict() for _ in range(3)]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_a_gap_in_bars_never_becomes_a_zero_price(self):
        """PA-P2 emits no bar for a quiet minute; neighbours are bars, not clocks."""
        bars = series([(10, 5), (11, 6), (15, 7), (12, 6), (11, 5)])
        with_gap = [bars[0], bars[1], bars[2], bars[3], bars[4]]
        del with_gap[1]

        result = compute_structure(with_gap, swing_n=1)

        self.assertNotIn(0, [point.price for point in result.swing_lows])
        self.assertNotIn(0, [point.price for point in result.swing_highs])


if __name__ == "__main__":
    unittest.main()
