import unittest
from datetime import datetime, timedelta, timezone

from tw_day_trading_lab.backfill import (
    BAR_SOURCE_KBARS,
    check_backfill_against_daily,
    normalize_shioaji_kbars,
    run_gated_shioaji_kbars_backfill,
)
from tw_day_trading_lab.bars import STATUS_CLOSED, MarketBar
from tw_day_trading_lab.rvol import (
    STATUS_INSUFFICIENT,
    STATUS_OK,
    build_volume_baseline,
    compute_rvol_series,
    time_slot,
)


def bar_5m(date, clock, volume, symbol="2330", price=100.0):
    hour, minute = (int(part) for part in clock.split(":"))
    end_minute = minute + 5
    return MarketBar(
        symbol=symbol,
        timeframe="5m",
        start_at=f"{date}T{hour:02d}:{minute:02d}:00",
        end_at=f"{date}T{hour:02d}:{end_minute:02d}:00",
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
        trade_count=1,
        status=STATUS_CLOSED,
        revision=1,
        source="local_1m_aggregated",
    )


def history(days, slots):
    """slots: {clock: [volume per day]} — one entry per day."""
    bars = []
    for index, date in enumerate(days):
        for clock, volumes in slots.items():
            if volumes[index] is not None:
                bars.append(bar_5m(date, clock, volumes[index]))
    return bars


DAYS_20 = [f"2026-07-{day:02d}" for day in range(1, 21)]


class TimeSlotTest(unittest.TestCase):
    def test_slot_is_the_time_of_day(self):
        self.assertEqual(time_slot("2026-08-17T09:30:00"), "09:30")


class BaselineTest(unittest.TestCase):
    def test_baseline_uses_the_same_time_slot_across_days(self):
        bars = history(
            DAYS_20,
            {"09:00": [5000] * 20, "09:30": [1000] * 20},
        )

        baseline = build_volume_baseline(bars)

        self.assertEqual(baseline.days, 20)
        self.assertEqual(baseline.slot_volume["09:00"].median, 5000)
        self.assertEqual(baseline.slot_volume["09:30"].median, 1000)

    def test_median_is_not_moved_by_one_exceptional_day(self):
        volumes = [1000] * 19 + [999_000]
        baseline = build_volume_baseline(history(DAYS_20, {"09:30": volumes}))

        self.assertEqual(baseline.slot_volume["09:30"].median, 1000)
        self.assertGreater(baseline.slot_volume["09:30"].mean, 1000)

    def test_a_day_without_that_slot_is_not_counted_as_zero_volume(self):
        """A quiet slot must not drag the baseline down and fake a spike."""
        volumes = [1000] * 18 + [None, None]
        baseline = build_volume_baseline(history(DAYS_20, {"09:30": volumes}))

        self.assertEqual(baseline.slot_volume["09:30"].days, 18)
        self.assertEqual(baseline.slot_volume["09:30"].median, 1000)

    def test_cumulative_baseline_accumulates_within_each_day(self):
        bars = history(
            DAYS_20,
            {"09:00": [1000] * 20, "09:05": [2000] * 20, "09:10": [3000] * 20},
        )

        baseline = build_volume_baseline(bars)

        self.assertEqual(baseline.slot_cumulative["09:00"].median, 1000)
        self.assertEqual(baseline.slot_cumulative["09:05"].median, 3000)
        self.assertEqual(baseline.slot_cumulative["09:10"].median, 6000)

    def test_only_the_lookback_window_is_used(self):
        days = [f"2026-06-{day:02d}" for day in range(1, 26)]
        volumes = [9999] * 5 + [1000] * 20
        baseline = build_volume_baseline(history(days, {"09:30": volumes}), lookback_days=20)

        self.assertEqual(baseline.days, 20)
        self.assertEqual(baseline.slot_volume["09:30"].median, 1000)

    def test_other_timeframes_are_ignored(self):
        bars = history(DAYS_20, {"09:30": [1000] * 20})
        noise = MarketBar(**{**bars[0].to_dict(), "timeframe": "1m", "volume": 99})

        baseline = build_volume_baseline([*bars, noise])

        self.assertEqual(baseline.slot_volume["09:30"].days, 20)


class RvolTest(unittest.TestCase):
    def _baseline(self, volumes=None):
        return build_volume_baseline(
            history(DAYS_20, {"09:00": [1000] * 20, "09:30": volumes or [1000] * 20})
        )

    def test_tod_rvol_is_today_over_the_slot_median(self):
        results = compute_rvol_series([bar_5m("2026-08-17", "09:30", 2000)], self._baseline())

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, STATUS_OK)
        self.assertEqual(results[0].tod_baseline, 1000)
        self.assertEqual(results[0].tod_rvol, 2.0)

    def test_cum_rvol_uses_today_volume_so_far(self):
        today = [bar_5m("2026-08-17", "09:00", 1500), bar_5m("2026-08-17", "09:30", 2500)]

        results = compute_rvol_series(today, self._baseline())

        self.assertEqual(results[1].cumulative_volume, 4000)
        # Baseline cumulative at 09:30 is 1000 + 1000.
        self.assertEqual(results[1].cum_baseline, 2000)
        self.assertEqual(results[1].cum_rvol, 2.0)

    def test_insufficient_history_withholds_the_ratio(self):
        short = build_volume_baseline(history(DAYS_20[:3], {"09:30": [1000] * 3}))

        results = compute_rvol_series([bar_5m("2026-08-17", "09:30", 5000)], short)

        self.assertEqual(results[0].status, STATUS_INSUFFICIENT)
        self.assertIsNone(results[0].tod_rvol)
        self.assertIsNone(results[0].cum_rvol)
        self.assertEqual(results[0].sample_days, 3)
        # The baseline itself is still visible for inspection.
        self.assertEqual(results[0].tod_baseline, 1000)

    def test_unknown_slot_is_insufficient_not_a_crash(self):
        results = compute_rvol_series([bar_5m("2026-08-17", "13:15", 5000)], self._baseline())

        self.assertEqual(results[0].status, STATUS_INSUFFICIENT)
        self.assertIsNone(results[0].tod_rvol)
        self.assertIsNone(results[0].tod_baseline)

    def test_zero_baseline_does_not_divide_by_zero(self):
        baseline = build_volume_baseline(history(DAYS_20, {"09:30": [0] * 20}))

        results = compute_rvol_series([bar_5m("2026-08-17", "09:30", 5000)], baseline)

        self.assertIsNone(results[0].tod_rvol)

    def test_series_is_deterministic_regardless_of_input_order(self):
        baseline = self._baseline()
        today = [bar_5m("2026-08-17", "09:30", 2500), bar_5m("2026-08-17", "09:00", 1500)]

        forward = [r.to_dict() for r in compute_rvol_series(today, baseline)]
        reversed_input = [r.to_dict() for r in compute_rvol_series(list(reversed(today)), baseline)]

        self.assertEqual(forward, reversed_input)
        self.assertEqual([r["slot"] for r in forward], ["09:00", "09:30"])


class FakeKbars:
    def __init__(self, rows):
        self.ts = [row[0] for row in rows]
        self.Open = [row[1] for row in rows]
        self.High = [row[2] for row in rows]
        self.Low = [row[3] for row in rows]
        self.Close = [row[4] for row in rows]
        self.Volume = [row[5] for row in rows]
        self.Amount = [0.0 for _ in rows]


def kbar_rows(count=3, start=datetime(2026, 8, 17, 9, 0), volume=10):
    """Shioaji encodes exchange local wall-clock time as a naive-UTC epoch."""
    rows = []
    for index in range(count):
        moment = (start + timedelta(minutes=index)).replace(tzinfo=timezone.utc)
        rows.append((int(moment.timestamp() * 1_000_000_000), 100.0, 101.0, 99.0, 100.5, volume))
    return rows


class FakeApi:
    def __init__(self, simulation=True, kbars=None, fail=False):
        self.simulation = simulation
        self.Contracts = type("C", (), {"Stocks": {"2330": "contract-2330"}})()
        self._kbars = kbars
        self._fail = fail

    def kbars(self, contract, start, end):
        if self._fail:
            raise RuntimeError("kbars unavailable")
        return self._kbars


class KbarsBackfillTest(unittest.TestCase):
    def test_columnar_kbars_become_canonical_1m_bars(self):
        bars = normalize_shioaji_kbars("2330", FakeKbars(kbar_rows(3, volume=10)))

        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].timeframe, "1m")
        self.assertEqual(bars[0].source, BAR_SOURCE_KBARS)
        self.assertEqual(bars[0].start_at, "2026-08-17T09:00:00")
        self.assertEqual(bars[0].end_at, "2026-08-17T09:01:00")
        self.assertEqual(bars[1].start_at, "2026-08-17T09:01:00")
        # Lot to share conversion, same canonical unit as the tick feed.
        self.assertEqual(bars[0].volume, 10_000)

    def test_official_example_decodes_to_exchange_local_time(self):
        """Shioaji's own 2330 sample: ts 1779094860000000000 is 09:01 local.

        Decoding with the host timezone would give 17:01 on a UTC+8 machine
        and something else elsewhere.
        """
        kbars = FakeKbars([(1779094860000000000, 2230.0, 2235.0, 2228.0, 2230.0, 2565)])

        bar = normalize_shioaji_kbars("2330", kbars)[0]

        self.assertEqual(bar.start_at, "2026-05-18T09:01:00")
        self.assertEqual(bar.end_at, "2026-05-18T09:02:00")
        # Volume as lots: 2565 x 1000 x 2230 matches the official Amount of
        # about 5.7 billion. Read as shares it would be 5.7 million.
        self.assertEqual(bar.volume, 2_565_000)
        self.assertAlmostEqual(bar.volume * bar.close / 5_708_965_000, 1.0, places=2)

    def test_share_unit_can_be_kept_when_the_provider_already_uses_shares(self):
        bars = normalize_shioaji_kbars(
            "2330", FakeKbars(kbar_rows(1, volume=10)), volume_in_lots=False
        )

        self.assertEqual(bars[0].volume, 10)

    def test_broken_rows_are_skipped_not_fatal(self):
        kbars = FakeKbars(kbar_rows(2))
        kbars.Close = [100.0]  # second row has no close

        bars = normalize_shioaji_kbars("2330", kbars)

        self.assertEqual(len(bars), 1)

    def test_daily_cross_check_catches_a_lot_share_mistake(self):
        bars = normalize_shioaji_kbars("2330", FakeKbars(kbar_rows(3, volume=10)))

        good = check_backfill_against_daily(bars, 30_000)
        bad = check_backfill_against_daily(bars, 30)

        self.assertTrue(good["unit_consistent"])
        self.assertAlmostEqual(good["ratio"], 1.0)
        self.assertFalse(bad["unit_consistent"])
        self.assertAlmostEqual(bad["ratio"], 1000.0)

    def test_backfill_is_blocked_without_the_gate(self):
        api = FakeApi(kbars=FakeKbars(kbar_rows()))

        report = run_gated_shioaji_kbars_backfill(
            api=api,
            symbols=["2330"],
            start_date="2026-07-01",
            end_date="2026-07-31",
            enabled=False,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "enable_kbars_backfill_required")
        self.assertEqual(report["side_effects"], [])
        self.assertFalse(report["checks"]["orders_allowed"])
        self.assertFalse(report["checks"]["intraday_polling"])

    def test_backfill_refuses_a_non_simulation_api(self):
        report = run_gated_shioaji_kbars_backfill(
            api=FakeApi(simulation=False, kbars=FakeKbars(kbar_rows())),
            symbols=["2330"],
            start_date="2026-07-01",
            end_date="2026-07-31",
            enabled=True,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "simulation_api_required")
        self.assertEqual(report["side_effects"], [])

    def test_enabled_backfill_reports_bars_and_session_completeness(self):
        api = FakeApi(kbars=FakeKbars(kbar_rows(3)))

        report = run_gated_shioaji_kbars_backfill(
            api=api,
            symbols=["2330"],
            start_date="2026-08-17",
            end_date="2026-08-17",
            enabled=True,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["side_effects"], ["kbars_fetch"])
        entry = report["symbols"][0]
        self.assertEqual(entry["bars_1m"], 3)
        self.assertEqual(entry["days"], ["2026-08-17"])
        # Three bars is nowhere near a full session, and that must be visible.
        self.assertEqual(entry["session_complete"]["short_days"], ["2026-08-17"])

    def test_fetch_failure_is_reported_not_raised(self):
        report = run_gated_shioaji_kbars_backfill(
            api=FakeApi(fail=True),
            symbols=["2330"],
            start_date="2026-08-17",
            end_date="2026-08-17",
            enabled=True,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["review_reason"], "kbars_fetch_failed")
        self.assertEqual(report["symbols"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
