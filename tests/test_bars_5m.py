import unittest

from tw_day_trading_lab.bars import (
    STATUS_CLOSED,
    STATUS_CORRECTED,
    FiveMinuteBarAggregator,
    MarketBar,
    aggregate_1m_to_5m,
    latest_bars,
)

DATE = "2026-08-17"


def bar_1m(
    clock,
    open_,
    high,
    low,
    close,
    volume=1000,
    symbol="2330",
    revision=1,
    status=STATUS_CLOSED,
    trade_count=1,
    sequence_gap=False,
):
    """Build one canonical 1m bar starting at `clock` (HH:MM)."""
    hour, minute = (int(part) for part in clock.split(":"))
    end_hour, end_minute = (hour, minute + 1) if minute < 59 else (hour + 1, 0)
    return MarketBar(
        symbol=symbol,
        timeframe="1m",
        start_at=f"{DATE}T{hour:02d}:{minute:02d}:00",
        end_at=f"{DATE}T{end_hour:02d}:{end_minute:02d}:00",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_count=trade_count,
        status=status,
        revision=revision,
        source="shioaji_tick_aggregated",
        sequence_gap=sequence_gap,
    )


def flat(clock, price, volume=1000, **kwargs):
    return bar_1m(clock, price, price, price, price, volume=volume, **kwargs)


class BucketBoundaryTest(unittest.TestCase):
    """Item 1: every 1m falls into exactly one correct 5m bucket."""

    def test_minutes_zero_through_four_share_one_bucket(self):
        bars = aggregate_1m_to_5m(
            [flat(f"09:0{minute}", 100.0 + minute) for minute in range(5)]
        )

        collapsed = latest_bars(bars)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].start_at, f"{DATE}T09:00:00")
        self.assertEqual(collapsed[0].end_at, f"{DATE}T09:05:00")
        self.assertEqual(collapsed[0].timeframe, "5m")
        self.assertEqual(collapsed[0].source, "local_1m_aggregated")

    def test_boundary_between_0904_and_0905(self):
        bars = latest_bars(aggregate_1m_to_5m([flat("09:04", 100.0), flat("09:05", 200.0)]))

        self.assertEqual(
            [bar.start_at for bar in bars], [f"{DATE}T09:00:00", f"{DATE}T09:05:00"]
        )
        self.assertEqual([bar.close for bar in bars], [100.0, 200.0])

    def test_later_buckets_floor_correctly(self):
        bars = latest_bars(
            aggregate_1m_to_5m([flat("09:09", 1.0), flat("09:10", 2.0), flat("13:24", 3.0)])
        )

        self.assertEqual(
            [bar.start_at for bar in bars],
            [f"{DATE}T09:05:00", f"{DATE}T09:10:00", f"{DATE}T13:20:00"],
        )


class OhlcvAggregationTest(unittest.TestCase):
    """Item 2: 5m OHLCV matches the 1m bars that compose it."""

    def test_ohlcv_comes_from_the_component_bars(self):
        bars = aggregate_1m_to_5m(
            [
                bar_1m("09:00", 100.0, 103.0, 99.0, 101.0, volume=1000),
                bar_1m("09:01", 101.0, 104.0, 100.0, 103.0, volume=2000),
                bar_1m("09:02", 103.0, 105.0, 102.0, 104.0, volume=3000),
            ]
        )

        bar = latest_bars(bars)[0]
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.high, 105.0)
        self.assertEqual(bar.low, 99.0)
        self.assertEqual(bar.close, 104.0)
        self.assertEqual(bar.volume, 6000)

    def test_open_and_close_follow_bucket_order_not_arrival_order(self):
        bars = aggregate_1m_to_5m(
            [
                bar_1m("09:02", 103.0, 105.0, 102.0, 104.0),
                bar_1m("09:00", 100.0, 103.0, 99.0, 101.0),
            ]
        )

        bar = latest_bars(bars)[0]
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.close, 104.0)

    def test_each_symbol_keeps_its_own_bucket(self):
        bars = latest_bars(
            aggregate_1m_to_5m(
                [
                    flat("09:00", 100.0, volume=1000, symbol="2330"),
                    flat("09:01", 50.0, volume=2000, symbol="2317"),
                    flat("09:03", 102.0, volume=3000, symbol="2330"),
                ]
            )
        )

        by_symbol = {bar.symbol: bar for bar in bars}
        self.assertEqual(by_symbol["2330"].volume, 4000)
        self.assertEqual(by_symbol["2330"].close, 102.0)
        self.assertEqual(by_symbol["2317"].volume, 2000)

    def test_non_1m_input_is_ignored(self):
        aggregator = FiveMinuteBarAggregator()
        five_minute = flat("09:00", 100.0)
        five_minute = MarketBar(**{**five_minute.to_dict(), "timeframe": "5m"})

        self.assertEqual(aggregator.on_bar(five_minute), [])
        self.assertEqual(aggregator.close_all(), [])
        self.assertEqual(aggregator.ignored_timeframe, 1)


class MissingMinuteTest(unittest.TestCase):
    """Item 3: a bucket is a time range, not a count of five bars."""

    def test_bucket_with_a_missing_minute_still_produces_a_5m_bar(self):
        bars = latest_bars(
            aggregate_1m_to_5m(
                [
                    bar_1m("09:00", 100.0, 101.0, 99.0, 100.5, volume=1000),
                    bar_1m("09:01", 100.5, 102.0, 100.0, 101.0, volume=1000),
                    # 09:02 never traded, so P2 emitted nothing for it.
                    bar_1m("09:03", 101.0, 103.0, 100.5, 102.0, volume=1000),
                    bar_1m("09:04", 102.0, 104.0, 101.0, 103.0, volume=1000),
                ]
            )
        )

        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.high, 104.0)
        self.assertEqual(bar.low, 99.0)
        self.assertEqual(bar.close, 103.0)
        # Only the four minutes that exist; nothing padded to reach five.
        self.assertEqual(bar.volume, 4000)

    def test_next_bucket_is_never_borrowed_to_reach_five_bars(self):
        bars = latest_bars(
            aggregate_1m_to_5m([flat("09:00", 100.0), flat("09:05", 200.0)])
        )

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].volume, 1000)
        self.assertEqual(bars[0].close, 100.0)
        self.assertEqual(bars[1].volume, 1000)

    def test_single_minute_bucket_is_valid(self):
        bars = latest_bars(aggregate_1m_to_5m([flat("09:03", 100.0, volume=7000)]))

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].start_at, f"{DATE}T09:00:00")
        self.assertEqual(bars[0].volume, 7000)


class CorrectionTest(unittest.TestCase):
    """Item 4: a corrected 1m replaces its minute, it never adds to it."""

    def _closed_bucket(self):
        aggregator = FiveMinuteBarAggregator()
        emitted = []
        for minute in range(5):
            emitted.extend(
                aggregator.on_bar(flat(f"09:0{minute}", 100.0 + minute, volume=1000))
            )
        # A 09:05 bar closes the 09:00 bucket.
        emitted.extend(aggregator.on_bar(flat("09:05", 200.0)))
        return aggregator, emitted

    def test_corrected_1m_replaces_the_minute_and_bumps_the_5m_revision(self):
        aggregator, emitted = self._closed_bucket()
        original = [bar for bar in emitted if bar.start_at == f"{DATE}T09:00:00"][0]
        self.assertEqual(original.status, STATUS_CLOSED)
        self.assertEqual(original.revision, 1)
        self.assertEqual(original.volume, 5000)

        corrected = aggregator.on_bar(
            flat("09:03", 103.0, volume=1500, revision=2, status=STATUS_CORRECTED)
        )[0]

        self.assertEqual(corrected.start_at, f"{DATE}T09:00:00")
        self.assertEqual(corrected.status, STATUS_CORRECTED)
        self.assertEqual(corrected.revision, 2)
        # 4 unchanged minutes at 1000 plus the corrected 1500, not 5000 + 1500.
        self.assertEqual(corrected.volume, 5500)
        # The revision the strategy already acted on is untouched.
        self.assertEqual(original.volume, 5000)
        self.assertEqual(original.revision, 1)

    def test_correction_changes_ohlc_not_just_volume(self):
        aggregator, _ = self._closed_bucket()

        corrected = aggregator.on_bar(
            bar_1m("09:03", 103.0, 999.0, 1.0, 103.0, revision=2, status=STATUS_CORRECTED)
        )[0]

        self.assertEqual(corrected.high, 999.0)
        self.assertEqual(corrected.low, 1.0)

    def test_repeated_corrections_never_double_count(self):
        aggregator, _ = self._closed_bucket()

        aggregator.on_bar(flat("09:03", 103.0, volume=1500, revision=2))
        third = aggregator.on_bar(flat("09:03", 103.0, volume=2000, revision=3))[0]

        self.assertEqual(third.revision, 3)
        self.assertEqual(third.volume, 6000)

    def test_stale_revision_is_ignored(self):
        aggregator, _ = self._closed_bucket()
        aggregator.on_bar(flat("09:03", 103.0, volume=1500, revision=2))

        emitted = aggregator.on_bar(flat("09:03", 103.0, volume=9999, revision=1))

        self.assertEqual(emitted, [])
        self.assertEqual(aggregator.stale_revisions, 1)

    def test_correction_before_the_bucket_closes_needs_no_new_revision(self):
        aggregator = FiveMinuteBarAggregator()
        aggregator.on_bar(flat("09:00", 100.0, volume=1000))
        aggregator.on_bar(flat("09:00", 100.0, volume=1500, revision=2))

        bars = aggregator.close_all()

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].status, STATUS_CLOSED)
        self.assertEqual(bars[0].revision, 1)
        self.assertEqual(bars[0].volume, 1500)
        self.assertEqual(aggregator.bars_corrected, 0)

    def test_correction_beyond_the_window_is_dropped_and_counted(self):
        aggregator = FiveMinuteBarAggregator(correction_window_minutes=10)
        aggregator.on_bar(flat("09:00", 100.0))
        aggregator.on_bar(flat("09:30", 200.0))

        emitted = aggregator.on_bar(flat("09:00", 100.0, volume=5000, revision=2))

        self.assertEqual(emitted, [])
        self.assertEqual(aggregator.dropped_late, 1)


class FlushTest(unittest.TestCase):
    def test_flush_closes_a_bucket_without_a_later_1m_bar(self):
        aggregator = FiveMinuteBarAggregator()
        aggregator.on_bar(flat("09:00", 100.0))

        self.assertEqual(aggregator.flush(now=f"{DATE}T09:04:00"), [])
        closed = aggregator.flush(now=f"{DATE}T09:05:00")

        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].start_at, f"{DATE}T09:00:00")


class DeterministicReplayTest(unittest.TestCase):
    """Item 5: identical input always produces identical latest 5m output."""

    def _event_log(self):
        return [
            flat("09:00", 100.0, volume=1000),
            flat("09:01", 101.0, volume=1000, symbol="2317"),
            flat("09:01", 101.0, volume=1000),
            # 09:02 missing on purpose.
            flat("09:03", 103.0, volume=1000),
            flat("09:04", 104.0, volume=1000),
            flat("09:05", 105.0, volume=1000),
            # Late correction of a minute in the already-closed bucket.
            flat("09:03", 103.5, volume=1500, revision=2, status=STATUS_CORRECTED),
            flat("09:06", 106.0, volume=1000),
            flat("09:11", 111.0, volume=1000, symbol="2317"),
        ]

    def test_three_runs_produce_identical_latest_bars(self):
        runs = [
            [bar.to_dict() for bar in latest_bars(aggregate_1m_to_5m(self._event_log()))]
            for _ in range(3)
        ]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_latest_output_reflects_the_correction_exactly_once(self):
        bars = latest_bars(aggregate_1m_to_5m(self._event_log()))

        by_key = {(bar.symbol, bar.start_at): bar for bar in bars}
        first_bucket = by_key[("2330", f"{DATE}T09:00:00")]
        self.assertEqual(first_bucket.status, STATUS_CORRECTED)
        self.assertEqual(first_bucket.revision, 2)
        # 09:00, 09:01, 09:04 at 1000 plus corrected 09:03 at 1500.
        self.assertEqual(first_bucket.volume, 4500)
        self.assertEqual(first_bucket.close, 104.0)

    def test_replay_covers_every_symbol_and_bucket(self):
        bars = latest_bars(aggregate_1m_to_5m(self._event_log()))

        self.assertEqual(
            [(bar.symbol, bar.start_at) for bar in bars],
            [
                ("2317", f"{DATE}T09:00:00"),
                ("2330", f"{DATE}T09:00:00"),
                ("2330", f"{DATE}T09:05:00"),
                ("2317", f"{DATE}T09:10:00"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
