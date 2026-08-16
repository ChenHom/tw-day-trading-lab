import json
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.bars import (
    STATUS_CLOSED,
    STATUS_CORRECTED,
    FiveMinuteBarAggregator,
    MarketBar,
    OneMinuteBarAggregator,
    aggregate_1m_to_5m,
    append_bars,
    bar_store_path,
    latest_bars,
    load_bar_revision,
    load_latest_bars,
    read_bar_events,
)
from tw_day_trading_lab.market_data import MarketTick

DATE = "2026-08-17"


def bar(
    clock,
    price,
    volume=1000,
    symbol="2330",
    timeframe="1m",
    revision=1,
    status=STATUS_CLOSED,
    span=1,
):
    hour, minute = (int(part) for part in clock.split(":"))
    end = minute + span
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        start_at=f"{DATE}T{hour:02d}:{minute:02d}:00",
        end_at=f"{DATE}T{hour:02d}:{end:02d}:00",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
        trade_count=1,
        status=status,
        revision=revision,
        source="shioaji_tick_aggregated",
    )


def tick(clock, price, volume=1000, symbol="2330", sequence=0):
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


class BarStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name)

    def test_1m_and_5m_are_stored_without_key_collision(self):
        """Same symbol and same start_at, different timeframe."""
        append_bars(
            self.store,
            [bar("09:00", 100.0), bar("09:00", 200.0, timeframe="5m", span=5)],
        )

        one = load_latest_bars(self.store, timeframe="1m", trading_date=DATE)
        five = load_latest_bars(self.store, timeframe="5m", trading_date=DATE)

        self.assertEqual([b.close for b in one], [100.0])
        self.assertEqual([b.close for b in five], [200.0])
        self.assertNotEqual(
            bar_store_path(self.store, "1m", DATE, "2330"),
            bar_store_path(self.store, "5m", DATE, "2330"),
        )

    def test_revisions_never_overwrite_each_other(self):
        append_bars(self.store, [bar("09:00", 100.0, volume=1000)])
        append_bars(
            self.store,
            [bar("09:00", 105.0, volume=1500, revision=2, status=STATUS_CORRECTED)],
        )

        events = read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="2330")

        self.assertEqual([event.revision for event in events], [1, 2])
        self.assertEqual([event.volume for event in events], [1000, 1500])

    def test_latest_revision_of_one_bar_can_be_queried(self):
        append_bars(
            self.store,
            [
                bar("09:00", 100.0, volume=1000),
                bar("09:01", 101.0, volume=1000),
                bar("09:00", 105.0, volume=1500, revision=2, status=STATUS_CORRECTED),
            ],
        )

        found = load_bar_revision(
            self.store,
            timeframe="1m",
            trading_date=DATE,
            symbol="2330",
            start_at=f"{DATE}T09:00:00",
        )

        self.assertEqual(found.revision, 2)
        self.assertEqual(found.volume, 1500)
        self.assertEqual(found.status, STATUS_CORRECTED)

    def test_missing_bar_revision_returns_none(self):
        self.assertIsNone(
            load_bar_revision(
                self.store,
                timeframe="1m",
                trading_date=DATE,
                symbol="2330",
                start_at=f"{DATE}T09:00:00",
            )
        )

    def test_a_time_range_returns_only_latest_revisions(self):
        append_bars(
            self.store,
            [
                bar("09:00", 100.0),
                bar("09:01", 101.0),
                bar("09:02", 102.0),
                bar("09:03", 103.0),
                bar("09:01", 111.0, volume=9000, revision=2, status=STATUS_CORRECTED),
            ],
        )

        window = load_latest_bars(
            self.store,
            timeframe="1m",
            trading_date=DATE,
            start_at=f"{DATE}T09:01:00",
            end_at=f"{DATE}T09:03:00",
        )

        self.assertEqual([b.start_at[-8:] for b in window], ["09:01:00", "09:02:00"])
        self.assertEqual(window[0].revision, 2)
        self.assertEqual(window[0].volume, 9000)

    def test_symbols_are_discovered_when_not_given(self):
        append_bars(
            self.store,
            [bar("09:00", 100.0, symbol="2330"), bar("09:00", 50.0, symbol="2317")],
        )

        bars = load_latest_bars(self.store, timeframe="1m", trading_date=DATE)

        self.assertEqual(sorted(b.symbol for b in bars), ["2317", "2330"])

    def test_reading_an_absent_day_is_empty(self):
        self.assertEqual(
            load_latest_bars(self.store, timeframe="1m", trading_date="2026-01-01"), []
        )
        self.assertEqual(
            read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="9999"), []
        )

    def test_stored_rows_are_plain_json(self):
        append_bars(self.store, [bar("09:00", 100.0)])
        path = bar_store_path(self.store, "1m", DATE, "2330")

        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timeframe"], "1m")
        self.assertEqual(rows[0]["revision"], 1)


class RestartRebuildTest(unittest.TestCase):
    """P4 done means: after a restart, the stored data rebuilds identical bars."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name)

    def _build_session(self):
        ticks = [
            tick("09:00:02", 100.0, 1000, sequence=0),
            tick("09:00:45", 101.0, 2000, sequence=1),
            tick("09:01:10", 99.0, 1500, sequence=2),
            tick("09:03:00", 103.0, 500, sequence=3),
            tick("09:06:00", 106.0, 500, sequence=4),
            tick("09:00:30", 98.0, 1000, symbol="2317", sequence=0),
        ]
        aggregator = OneMinuteBarAggregator()
        emitted_1m = []
        for item in ticks:
            emitted_1m.extend(aggregator.on_tick(item))
        emitted_1m.extend(aggregator.close_all())
        emitted_5m = aggregate_1m_to_5m(emitted_1m)
        return emitted_1m, emitted_5m

    def test_stored_events_rebuild_identical_canonical_bars(self):
        emitted_1m, emitted_5m = self._build_session()
        append_bars(self.store, emitted_1m + emitted_5m)

        # Simulate a restart: nothing in memory, only the store on disk.
        rebuilt_1m = load_latest_bars(self.store, timeframe="1m", trading_date=DATE)
        rebuilt_5m = load_latest_bars(self.store, timeframe="5m", trading_date=DATE)

        self.assertEqual(
            [b.to_dict() for b in rebuilt_1m], [b.to_dict() for b in latest_bars(emitted_1m)]
        )
        self.assertEqual(
            [b.to_dict() for b in rebuilt_5m], [b.to_dict() for b in latest_bars(emitted_5m)]
        )

    def test_replaying_the_stored_event_log_is_deterministic(self):
        emitted_1m, _ = self._build_session()
        append_bars(self.store, emitted_1m)

        events = read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="2330")
        runs = [[b.to_dict() for b in aggregate_1m_to_5m(events)] for _ in range(3)]

        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_appending_a_second_session_keeps_the_earlier_events(self):
        emitted_1m, _ = self._build_session()
        append_bars(self.store, emitted_1m)
        first = len(read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="2330"))

        append_bars(self.store, emitted_1m)
        second = len(read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="2330"))

        self.assertEqual(second, first * 2)
        # Duplicate events do not change the latest view.
        self.assertEqual(
            len(load_latest_bars(self.store, timeframe="1m", trading_date=DATE, symbols=["2330"])),
            first,
        )

    def test_correction_emitted_after_close_is_recoverable_from_the_store(self):
        aggregator = OneMinuteBarAggregator(lateness_seconds=3)
        emitted = []
        emitted.extend(aggregator.on_tick(tick("09:00:10", 100.0, 1000, sequence=0)))
        emitted.extend(aggregator.on_tick(tick("09:01:05", 200.0, 1000, sequence=1)))
        emitted.extend(aggregator.on_tick(tick("09:00:59.800000", 105.0, 2000, sequence=2)))
        emitted.extend(aggregator.close_all())
        append_bars(self.store, emitted)

        events = read_bar_events(self.store, timeframe="1m", trading_date=DATE, symbol="2330")
        found = load_bar_revision(
            self.store,
            timeframe="1m",
            trading_date=DATE,
            symbol="2330",
            start_at=f"{DATE}T09:00:00",
        )

        # Both the revision the strategy saw and the correction are on disk.
        self.assertEqual(
            sorted(e.revision for e in events if e.start_at == f"{DATE}T09:00:00"), [1, 2]
        )
        self.assertEqual(found.revision, 2)
        self.assertEqual(found.status, STATUS_CORRECTED)
        self.assertEqual(found.volume, 3000)

    def test_five_minute_store_survives_a_late_correction(self):
        emitted_1m, _ = self._build_session()
        five = FiveMinuteBarAggregator()
        emitted_5m = []
        for item in emitted_1m:
            emitted_5m.extend(five.on_bar(item))
        emitted_5m.extend(five.close_all())
        append_bars(self.store, emitted_5m)

        rebuilt = load_latest_bars(self.store, timeframe="5m", trading_date=DATE, symbols=["2330"])

        self.assertEqual([b.start_at[-8:] for b in rebuilt], ["09:00:00", "09:05:00"])
        self.assertEqual(rebuilt[0].volume, 5000)


if __name__ == "__main__":
    unittest.main()
