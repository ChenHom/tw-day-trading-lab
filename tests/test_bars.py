import unittest

from tw_day_trading_lab.bars import (
    STATUS_CLOSED,
    STATUS_CORRECTED,
    OneMinuteBarAggregator,
    aggregate_ticks,
    check_bar_volume,
    latest_bars,
)
from tw_day_trading_lab.market_data import MarketTick

DATE = "2026-08-17"
_SEQ = {}


def tick(clock, price, volume=1000, symbol="2330", sequence=None):
    """Build a MarketTick at `clock` (HH:MM:SS or HH:MM:SS.ffffff)."""
    if sequence is None:
        sequence = _SEQ.get(symbol, 0)
        _SEQ[symbol] = sequence + 1
    return MarketTick(
        symbol=symbol,
        exchange="TSE",
        timestamp=f"{DATE}T{clock}",
        price=price,
        trade_volume=volume,
        cumulative_volume=0,
        source="shioaji_tick",
        sequence=sequence,
    )


def reset_sequences():
    _SEQ.clear()


class OhlcvAggregationTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_ticks_in_one_minute_become_one_ohlcv_bar(self):
        bars = aggregate_ticks(
            [
                tick("09:00:02", 100.0, 1000),
                tick("09:00:15", 101.0, 2000),
                tick("09:00:43", 99.5, 1000),
                tick("09:00:58", 100.5, 3000),
            ]
        )

        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(bar.symbol, "2330")
        self.assertEqual(bar.timeframe, "1m")
        self.assertEqual(bar.start_at, f"{DATE}T09:00:00")
        self.assertEqual(bar.end_at, f"{DATE}T09:01:00")
        self.assertEqual((bar.open, bar.high, bar.low, bar.close), (100.0, 101.0, 99.5, 100.5))
        self.assertEqual(bar.volume, 7000)
        self.assertEqual(bar.trade_count, 4)
        self.assertEqual(bar.status, STATUS_CLOSED)
        self.assertEqual(bar.revision, 1)
        self.assertEqual(bar.source, "shioaji_tick_aggregated")

    def test_bar_volume_sums_trade_volume_only(self):
        bars = aggregate_ticks(
            [
                tick("09:00:02", 100.0, 1000),
                tick("09:00:15", 100.0, 2000),
                tick("09:00:30", 100.0, 3000),
            ]
        )

        self.assertEqual(bars[0].volume, 6000)

    def test_minute_boundary_is_half_open(self):
        """09:00:59 belongs to 09:00; 09:01:00 starts the next bucket."""
        bars = aggregate_ticks(
            [
                tick("09:00:59", 100.0, 1000),
                tick("09:01:00", 200.0, 2000),
            ]
        )

        self.assertEqual([bar.start_at for bar in bars], [f"{DATE}T09:00:00", f"{DATE}T09:01:00"])
        self.assertEqual([bar.close for bar in bars], [100.0, 200.0])
        self.assertEqual([bar.volume for bar in bars], [1000, 2000])

    def test_each_symbol_keeps_its_own_bar(self):
        bars = aggregate_ticks(
            [
                tick("09:00:05", 100.0, 1000, symbol="2330"),
                tick("09:00:06", 50.0, 2000, symbol="2317"),
                tick("09:00:40", 102.0, 1000, symbol="2330"),
                tick("09:01:10", 51.0, 3000, symbol="2317"),
            ]
        )

        by_key = {(bar.symbol, bar.start_at): bar for bar in bars}
        self.assertEqual(len(by_key), 3)
        self.assertEqual(by_key[("2330", f"{DATE}T09:00:00")].volume, 2000)
        self.assertEqual(by_key[("2330", f"{DATE}T09:00:00")].close, 102.0)
        self.assertEqual(by_key[("2317", f"{DATE}T09:00:00")].volume, 2000)
        self.assertEqual(by_key[("2317", f"{DATE}T09:01:00")].volume, 3000)

    def test_open_and_close_follow_event_time_not_arrival_order(self):
        aggregator = OneMinuteBarAggregator()
        aggregator.on_tick(tick("09:00:30", 100.0))
        aggregator.on_tick(tick("09:00:10", 99.0))

        bar = aggregator.close_all()[0]

        self.assertEqual(bar.open, 99.0)
        self.assertEqual(bar.close, 100.0)


class MinuteRolloverTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_bar_closes_only_after_the_lateness_window(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)

        self.assertEqual(aggregator.on_tick(tick("09:00:10", 100.0)), [])
        # 09:01:01 is past the minute but inside the lateness window.
        self.assertEqual(aggregator.on_tick(tick("09:01:01", 101.0)), [])
        closed = aggregator.on_tick(tick("09:01:05", 102.0))

        self.assertEqual([bar.start_at for bar in closed], [f"{DATE}T09:00:00"])
        self.assertEqual(closed[0].status, STATUS_CLOSED)

    def test_flush_closes_due_bars_without_new_ticks(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        aggregator.on_tick(tick("09:00:10", 100.0))

        self.assertEqual(aggregator.flush(now=f"{DATE}T09:01:02"), [])
        closed = aggregator.flush(now=f"{DATE}T09:01:03")

        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].start_at, f"{DATE}T09:00:00")

    def test_close_all_finalizes_everything_pending(self):
        aggregator = OneMinuteBarAggregator()
        aggregator.on_tick(tick("09:00:10", 100.0, symbol="2330"))
        aggregator.on_tick(tick("09:00:11", 50.0, symbol="2317"))

        bars = aggregator.close_all()

        self.assertEqual(sorted(bar.symbol for bar in bars), ["2317", "2330"])
        self.assertEqual(aggregator.pending_count(), 0)


class MissingMinuteTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_no_trade_minute_produces_no_synthetic_bar(self):
        """A minute nobody traded is absent, not a flat previous-close bar."""
        aggregator = OneMinuteBarAggregator()
        bars = []
        for item in (tick("09:00:10", 100.0), tick("09:02:10", 101.0)):
            bars.extend(aggregator.on_tick(item))
        bars.extend(aggregator.close_all())

        self.assertEqual(
            [bar.start_at for bar in bars], [f"{DATE}T09:00:00", f"{DATE}T09:02:00"]
        )
        self.assertEqual(aggregator.no_trade_minutes, 1)

    def test_zero_volume_tick_still_produces_a_real_bar(self):
        """Volume 0 with a trade is not the same fact as no trade at all."""
        bars = aggregate_ticks([tick("09:00:10", 100.0, volume=0)])

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].volume, 0)
        self.assertEqual(bars[0].trade_count, 1)


class LateTickTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_late_tick_inside_the_window_needs_no_correction(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        aggregator.on_tick(tick("09:00:10", 100.0, 1000))
        aggregator.on_tick(tick("09:01:01", 200.0, 1000))
        aggregator.on_tick(tick("09:00:59.800000", 105.0, 2000))

        closed = aggregator.on_tick(tick("09:01:05", 201.0, 1000))

        self.assertEqual(len(closed), 1)
        bar = closed[0]
        self.assertEqual(bar.status, STATUS_CLOSED)
        self.assertEqual(bar.revision, 1)
        self.assertEqual(bar.trade_count, 2)
        self.assertEqual(bar.volume, 3000)
        self.assertEqual(bar.close, 105.0)
        self.assertEqual(aggregator.bars_corrected, 0)

    def test_late_tick_after_close_emits_a_corrected_revision(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        aggregator.on_tick(tick("09:00:10", 100.0, 1000))
        original = aggregator.on_tick(tick("09:01:05", 200.0, 1000))[0]

        corrected = aggregator.on_tick(tick("09:00:59.800000", 105.0, 2000))[0]

        self.assertEqual(original.status, STATUS_CLOSED)
        self.assertEqual(original.revision, 1)
        self.assertEqual(corrected.status, STATUS_CORRECTED)
        self.assertEqual(corrected.revision, 2)
        self.assertEqual(corrected.volume, 3000)
        self.assertEqual(corrected.close, 105.0)
        # The revision the strategy already saw must not have changed.
        self.assertEqual(original.volume, 1000)
        self.assertEqual(original.close, 100.0)

    def test_tick_older_than_the_correction_window_is_dropped_and_counted(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3, correction_window_minutes=10)
        aggregator.on_tick(tick("09:00:10", 100.0))
        aggregator.on_tick(tick("09:15:00", 110.0))

        emitted = aggregator.on_tick(tick("09:00:30", 99.0))

        self.assertEqual(emitted, [])
        self.assertEqual(aggregator.dropped_late, 1)


class DataQualityTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_sequence_gap_is_marked_on_the_affected_bar(self):
        bars = aggregate_ticks(
            [
                tick("09:00:02", 100.0, sequence=0),
                tick("09:00:20", 101.0, sequence=1),
                tick("09:01:02", 102.0, sequence=3),
            ]
        )

        by_start = {bar.start_at: bar for bar in bars}
        self.assertFalse(by_start[f"{DATE}T09:00:00"].sequence_gap)
        self.assertTrue(by_start[f"{DATE}T09:01:00"].sequence_gap)

    def test_first_tick_of_a_symbol_is_not_a_sequence_gap(self):
        bars = aggregate_ticks([tick("09:00:02", 100.0, sequence=41)])

        self.assertFalse(bars[0].sequence_gap)

    def test_unparseable_timestamp_is_counted_and_never_creates_a_bar(self):
        aggregator = OneMinuteBarAggregator()
        broken = MarketTick(
            symbol="2330",
            exchange="TSE",
            timestamp="not-a-timestamp",
            price=100.0,
            trade_volume=1000,
            cumulative_volume=0,
            source="shioaji_tick",
            sequence=0,
        )

        self.assertEqual(aggregator.on_tick(broken), [])
        self.assertEqual(aggregator.close_all(), [])
        self.assertEqual(aggregator.invalid_timestamps, 1)
        self.assertEqual(aggregator.tick_count, 0)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        reset_sequences()

    def test_replay_is_deterministic(self):
        ticks = [
            tick("09:00:02", 100.0, 1000),
            tick("09:00:45", 101.0, 2000),
            tick("09:01:10", 99.0, 1500),
            tick("09:03:00", 103.0, 500),
        ]

        first = [bar.to_dict() for bar in aggregate_ticks(ticks)]
        second = [bar.to_dict() for bar in aggregate_ticks(ticks)]

        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)

    def test_bar_volume_matches_source_tick_volume(self):
        ticks = [
            tick("09:00:02", 100.0, 1000),
            tick("09:00:45", 101.0, 2000),
            tick("09:01:10", 99.0, 1500),
        ]

        result = check_bar_volume(aggregate_ticks(ticks), ticks)

        self.assertEqual(result["bar_volume"], 4500)
        self.assertEqual(result["tick_volume"], 4500)
        self.assertEqual(result["bar_trade_count"], 3)
        self.assertTrue(result["consistent"])

    def test_latest_bars_collapses_a_corrected_minute_once(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        emitted = []
        emitted.extend(aggregator.on_tick(tick("09:00:10", 100.0, 1000)))
        emitted.extend(aggregator.on_tick(tick("09:01:05", 200.0, 1000)))
        emitted.extend(aggregator.on_tick(tick("09:00:59.800000", 105.0, 2000)))
        emitted.extend(aggregator.close_all())

        collapsed = latest_bars(emitted)

        by_start = {bar.start_at: bar for bar in collapsed}
        self.assertEqual(len(collapsed), 2)
        self.assertEqual(by_start[f"{DATE}T09:00:00"].revision, 2)
        self.assertEqual(by_start[f"{DATE}T09:00:00"].status, STATUS_CORRECTED)
        self.assertEqual(by_start[f"{DATE}T09:00:00"].volume, 3000)

    def test_volume_check_does_not_double_count_corrections(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        ticks = [
            tick("09:00:10", 100.0, 1000),
            tick("09:01:05", 200.0, 1000),
            tick("09:00:59.800000", 105.0, 2000),
        ]
        emitted = []
        for item in ticks:
            emitted.extend(aggregator.on_tick(item))
        emitted.extend(aggregator.close_all())

        result = check_bar_volume(emitted, ticks)

        self.assertEqual(result["bar_volume"], 4000)
        self.assertEqual(result["tick_volume"], 4000)
        self.assertTrue(result["consistent"])

    def test_stats_expose_every_data_quality_counter(self):
        aggregator = OneMinuteBarAggregator()
        aggregator.on_tick(tick("09:00:02", 100.0))
        aggregator.close_all()

        stats = aggregator.stats()

        self.assertEqual(stats["symbols"], ["2330"])
        self.assertEqual(stats["ticks"], 1)
        self.assertEqual(stats["bars_closed"], 1)
        self.assertEqual(stats["pending_bars"], 0)
        for key in (
            "bars_corrected",
            "late_ticks",
            "dropped_late",
            "sequence_gaps",
            "no_trade_minutes",
            "invalid_timestamps",
        ):
            self.assertEqual(stats[key], 0, key)


if __name__ == "__main__":
    unittest.main()
