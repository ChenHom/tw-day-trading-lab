import datetime as dt
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from decimal import Decimal
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

from tw_day_trading_lab.cli import cmd_simulate_shioaji_tick_smoke
from tw_day_trading_lab.market_data import (
    DEGRADED,
    FAILED,
    HEALTHY,
    MarketTick,
    ShioajiTickStream,
    append_raw_ticks,
    evaluate_live_validation,
    normalize_shioaji_tick,
    raw_tick_path,
    run_gated_shioaji_tick_stream_smoke,
)

TSE = SimpleNamespace(value="TSE")
OTC = SimpleNamespace(value="OTC")


class FakeTick:
    """Stand-in for shioaji.stream_data_type.TickSTKv1."""

    def __init__(self, **overrides):
        fields = {
            "code": "2330",
            "datetime": dt.datetime(2026, 8, 14, 9, 1, 30, 123456),
            "close": Decimal("1050.5"),
            "volume": 3,
            "total_volume": 120,
            "suspend": False,
            "simtrade": False,
            "intraday_odd": False,
        }
        fields.update(overrides)
        for key, value in fields.items():
            setattr(self, key, value)


class FakeQuote:
    def __init__(self, fail_on=None):
        self.callback = None
        self.subscribed = []
        self.unsubscribed = []
        self._fail_on = fail_on

    def set_on_tick_stk_v1_callback(self, func, bind=False):
        self.callback = func

    def subscribe(self, contract, **kwargs):
        if contract == self._fail_on:
            raise RuntimeError(f"subscribe failed: {contract}")
        self.subscribed.append(contract)

    def unsubscribe(self, contract, **kwargs):
        self.unsubscribed.append(contract)


class FakeApi:
    """Shioaji 1.3-shaped api: quote methods live on api.quote."""

    def __init__(self, symbols=("2330",), simulation=True, fail_subscribe_on=None):
        self.simulation = simulation
        self.quote = FakeQuote(fail_on=fail_subscribe_on)
        self.Contracts = SimpleNamespace(
            Stocks={symbol: f"contract-{symbol}" for symbol in symbols}
        )


class FakeFlatApi:
    """Newer Shioaji shape: subscribe / callback setter on the api itself."""

    def __init__(self, symbols=("2330",)):
        self.simulation = True
        self.callback = None
        self.subscribed = []
        self.unsubscribed = []
        self.Contracts = SimpleNamespace(
            Stocks={symbol: f"contract-{symbol}" for symbol in symbols}
        )

    def set_on_tick_stk_v1_callback(self, func, bind=False):
        self.callback = func

    def subscribe(self, contract, **kwargs):
        self.subscribed.append(contract)

    def unsubscribe(self, contract, **kwargs):
        self.unsubscribed.append(contract)


def build_stream(api=None, symbols=("2330",), **kwargs):
    return ShioajiTickStream(
        api if api is not None else FakeApi(symbols=symbols),
        trading_date="2026-08-14",
        symbols=list(symbols),
        **kwargs,
    )


class NormalizeShioajiTickTest(unittest.TestCase):
    def test_regular_tick_is_normalized_with_share_volumes(self):
        tick, reason = normalize_shioaji_tick(TSE, FakeTick(), sequence=7)

        self.assertEqual(reason, "")
        self.assertEqual(
            tick,
            MarketTick(
                symbol="2330",
                exchange="TSE",
                timestamp="2026-08-14T09:01:30.123456",
                price=1050.5,
                trade_volume=3000,
                cumulative_volume=120000,
                source="shioaji_tick",
                sequence=7,
            ),
        )

    def test_simtrade_tick_is_rejected(self):
        tick, reason = normalize_shioaji_tick(TSE, FakeTick(simtrade=True), sequence=0)

        self.assertIsNone(tick)
        self.assertEqual(reason, "simtrade")

    def test_intraday_odd_tick_is_rejected(self):
        tick, reason = normalize_shioaji_tick(TSE, FakeTick(intraday_odd=True), sequence=0)

        self.assertIsNone(tick)
        self.assertEqual(reason, "intraday_odd")

    def test_suspended_tick_is_rejected(self):
        tick, reason = normalize_shioaji_tick(TSE, FakeTick(suspend=True), sequence=0)

        self.assertIsNone(tick)
        self.assertEqual(reason, "suspend")

    def test_normalized_tick_is_json_serializable(self):
        tick, _ = normalize_shioaji_tick(TSE, FakeTick(), sequence=0)

        payload = json.loads(json.dumps(tick.to_dict()))

        self.assertEqual(payload["price"], 1050.5)
        self.assertNotIsInstance(payload["price"], Decimal)

    def test_exchange_enum_is_stored_as_text(self):
        tse, _ = normalize_shioaji_tick(TSE, FakeTick(), sequence=0)
        otc, _ = normalize_shioaji_tick(OTC, FakeTick(code="6180"), sequence=0)

        self.assertEqual(tse.exchange, "TSE")
        self.assertEqual(otc.exchange, "OTC")

    def test_missing_required_fields_need_review(self):
        no_code, code_reason = normalize_shioaji_tick(TSE, FakeTick(code=""), sequence=0)
        no_time, time_reason = normalize_shioaji_tick(TSE, FakeTick(datetime=None), sequence=0)
        no_price, price_reason = normalize_shioaji_tick(TSE, FakeTick(close=None), sequence=0)

        self.assertEqual((no_code, code_reason), (None, "needs_review"))
        self.assertEqual((no_time, time_reason), (None, "needs_review"))
        self.assertEqual((no_price, price_reason), (None, "needs_review"))

    def test_broken_volume_is_never_reported_as_zero_volume(self):
        """A missing or unparseable volume must not look like a quiet minute."""
        for overrides in (
            {"volume": None},
            {"volume": "xxx"},
            {"volume": -1},
            {"total_volume": None},
            {"total_volume": "xxx"},
            {"total_volume": -5},
        ):
            with self.subTest(**overrides):
                tick, reason = normalize_shioaji_tick(
                    TSE, FakeTick(**overrides), sequence=0
                )

                self.assertIsNone(tick)
                self.assertEqual(reason, "needs_review")

    def test_zero_volume_tick_is_still_valid_data(self):
        tick, reason = normalize_shioaji_tick(TSE, FakeTick(volume=0), sequence=0)

        self.assertEqual(reason, "")
        self.assertEqual(tick.trade_volume, 0)


class ShioajiTickStreamTest(unittest.TestCase):
    def test_stream_rejects_non_simulation_api(self):
        with self.assertRaises(ValueError):
            build_stream(FakeApi(simulation=False))

    def test_start_subscribes_only_candidate_symbols(self):
        api = FakeApi(symbols=("2330", "2317", "6180", "1101"))
        stream = build_stream(api, symbols=("2330", "2317", "6180"))

        stream.start()
        self.addCleanup(stream.stop)

        self.assertEqual(
            api.quote.subscribed,
            ["contract-2330", "contract-2317", "contract-6180"],
        )
        self.assertEqual(api.quote.callback, stream.handle_tick)

    def test_flat_api_shape_is_supported(self):
        api = FakeFlatApi(symbols=("2330",))
        stream = build_stream(api)

        stream.start()
        stream.stop()

        self.assertEqual(api.subscribed, ["contract-2330"])
        self.assertEqual(api.unsubscribed, ["contract-2330"])
        self.assertEqual(api.callback, stream.handle_tick)

    def test_unknown_symbol_is_recorded_and_does_not_stop_subscription(self):
        api = FakeApi(symbols=("2330",))
        stream = build_stream(api, symbols=("9999", "2330"))

        stream.start()
        self.addCleanup(stream.stop)

        self.assertEqual(api.quote.subscribed, ["contract-2330"])
        self.assertEqual(stream.rejected["contract_not_found"], 1)

    def test_failed_subscribe_rolls_back_earlier_subscriptions(self):
        api = FakeApi(symbols=("2330", "2317"), fail_subscribe_on="contract-2317")
        stream = build_stream(api, symbols=("2330", "2317"))

        with self.assertRaises(RuntimeError):
            stream.start()

        self.assertEqual(api.quote.unsubscribed, ["contract-2330"])

    def test_callback_does_no_disk_io(self):
        """The provider callback must only enqueue; writing happens on drain."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            stream = build_stream(cache_dir=cache_dir)
            path = raw_tick_path(cache_dir, "2026-08-14", "2330")

            stream.handle_tick(TSE, FakeTick())
            self.assertFalse(path.exists())

            self.assertEqual(stream.drain(), 1)
            self.assertTrue(path.exists())

    def test_sequence_increments_per_symbol(self):
        emitted = []
        stream = build_stream(symbols=("2330", "2317"), sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(code="2330", total_volume=10))
        stream.handle_tick(TSE, FakeTick(code="2317", total_volume=20))
        stream.handle_tick(TSE, FakeTick(code="2330", total_volume=30))
        stream.drain()

        self.assertEqual(
            [(tick.symbol, tick.sequence) for tick in emitted],
            [("2330", 0), ("2317", 0), ("2330", 1)],
        )

    def test_rejected_ticks_do_not_consume_a_sequence_number(self):
        emitted = []
        stream = build_stream(sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(simtrade=True))
        stream.handle_tick(TSE, FakeTick())
        stream.drain()

        self.assertEqual([tick.sequence for tick in emitted], [0])
        self.assertEqual(stream.rejected["simtrade"], 1)

    def test_ticks_outside_the_candidate_list_never_reach_the_sink(self):
        """One api object shares its quote callback across subscribers."""
        emitted = []
        stream = build_stream(symbols=("2330",), sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(code="2603"))
        stream.handle_tick(TSE, FakeTick(code="2330"))
        stream.drain()

        self.assertEqual([tick.symbol for tick in emitted], ["2330"])
        self.assertEqual(stream.rejected["outside_candidate_scope"], 1)

    def test_duplicate_tick_is_dropped_and_keeps_the_sequence_intact(self):
        emitted = []
        stream = build_stream(sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(total_volume=100))
        stream.handle_tick(TSE, FakeTick(total_volume=100))
        stream.handle_tick(TSE, FakeTick(total_volume=101))
        stream.drain()

        self.assertEqual(
            [(tick.cumulative_volume, tick.sequence) for tick in emitted],
            [(100000, 0), (101000, 1)],
        )
        self.assertEqual(stream.rejected["duplicate"], 1)

    def test_same_cumulative_volume_at_a_later_timestamp_is_not_a_duplicate(self):
        emitted = []
        stream = build_stream(sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(total_volume=100))
        stream.handle_tick(
            TSE,
            FakeTick(total_volume=100, datetime=dt.datetime(2026, 8, 14, 9, 2, 0)),
        )
        stream.drain()

        self.assertEqual(len(emitted), 2)
        self.assertEqual(stream.rejected["duplicate"], 0)

    def test_out_of_order_cumulative_volume_is_counted_but_still_emitted(self):
        emitted = []
        stream = build_stream(sink=emitted.append)

        stream.handle_tick(TSE, FakeTick(total_volume=100))
        stream.handle_tick(TSE, FakeTick(total_volume=90))
        stream.handle_tick(TSE, FakeTick(total_volume=110))
        stream.drain()

        self.assertEqual(stream.out_of_order, 1)
        self.assertEqual(
            [tick.cumulative_volume for tick in emitted], [100000, 90000, 110000]
        )

    def test_stop_unsubscribes_every_subscribed_contract(self):
        api = FakeApi(symbols=("2330", "2317"))
        stream = build_stream(api, symbols=("2330", "2317"))
        stream.start()

        stream.stop()

        self.assertEqual(api.quote.unsubscribed, ["contract-2330", "contract-2317"])

    def test_stop_flushes_queued_ticks(self):
        emitted = []
        stream = build_stream(sink=emitted.append)
        stream.start()

        stream.handle_tick(TSE, FakeTick())
        stream.stop()

        self.assertEqual(len(emitted), 1)

    def test_raw_ticks_keep_simtrade_rows_that_market_ticks_drop(self):
        emitted = []
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            stream = build_stream(sink=emitted.append, cache_dir=cache_dir)

            stream.handle_tick(TSE, FakeTick(simtrade=True))
            stream.handle_tick(TSE, FakeTick())
            stream.drain()

            path = raw_tick_path(cache_dir, "2026-08-14", "2330")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["simtrade"] for row in rows], [True, False])
        self.assertEqual(rows[0]["exchange"], "TSE")
        self.assertEqual(len(emitted), 1)

    def test_tick_without_a_code_is_still_kept_for_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            stream = build_stream(cache_dir=cache_dir)

            stream.handle_tick(TSE, FakeTick(code=""))
            stream.drain()

            path = raw_tick_path(cache_dir, "2026-08-14", "_unknown")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "")
        self.assertEqual(stream.rejected["needs_review"], 1)


class MarketDataHealthTest(unittest.TestCase):
    """Every way the pipeline can lose a tick has to be observable."""

    def test_clean_run_is_healthy(self):
        stream = build_stream(sink=lambda tick: None)

        stream.handle_tick(TSE, FakeTick())
        stream.drain()

        self.assertEqual(stream.health(), HEALTHY)
        self.assertTrue(stream.is_healthy)

    def test_queue_overflow_degrades_health(self):
        stream = build_stream(queue_maxsize=1)

        for _ in range(3):
            stream.handle_tick(TSE, FakeTick())

        self.assertEqual(stream.dropped_queue_full, 2)
        self.assertEqual(stream.summary()["dropped_queue_full"], 2)
        self.assertEqual(stream.health(), DEGRADED)

    def test_raising_sink_is_counted_and_degrades_health(self):
        def explode(_tick):
            raise RuntimeError("consumer down")

        stream = build_stream(sink=explode)

        stream.handle_tick(TSE, FakeTick())
        stream.drain()

        self.assertEqual(stream.sink_errors, 1)
        self.assertEqual(stream.health(), DEGRADED)
        self.assertIn("consumer down", stream.last_error)

    def test_raw_write_failure_is_counted_and_degrades_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            emitted = []
            stream = build_stream(sink=emitted.append, cache_dir=blocked)

            stream.handle_tick(TSE, FakeTick())
            stream.drain()

        self.assertEqual(stream.raw_write_errors, 1)
        self.assertEqual(stream.health(), DEGRADED)
        # The tick itself was fine, so it still reaches the consumer.
        self.assertEqual(len(emitted), 1)

    def test_unexpected_processing_error_keeps_the_worker_alive(self):
        stream = build_stream()
        stream._remember = lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))

        stream.handle_tick(TSE, FakeTick())
        stream.handle_tick(TSE, FakeTick(total_volume=200))
        processed = stream.drain()

        self.assertEqual(processed, 2)
        self.assertEqual(stream.worker_errors, 2)
        self.assertEqual(stream.health(), DEGRADED)

    def test_dead_worker_is_reported_as_failed(self):
        class ExplodingQueue:
            def get(self, timeout=None):
                raise RuntimeError("queue broke")

            def get_nowait(self):
                raise Empty

            def qsize(self):
                return 0

        stream = build_stream()
        stream._queue = ExplodingQueue()

        stream.start()
        stream._worker.join(timeout=5)

        self.assertTrue(stream.worker_failed)
        self.assertEqual(stream.health(), FAILED)
        self.assertFalse(stream.is_healthy)

    def test_worker_that_will_not_join_fails_closed_without_draining(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_sink(_tick):
            entered.set()
            release.wait(timeout=5)

        stream = build_stream(sink=blocking_sink)
        stream.start()
        stream.handle_tick(TSE, FakeTick())
        self.assertTrue(entered.wait(timeout=5))

        stream.stop(join_timeout=0.1)

        self.assertTrue(stream.worker_stop_timeout)
        self.assertEqual(stream.health(), FAILED)
        self.assertEqual(stream.state, "stopped")

    def test_drain_refuses_to_run_beside_a_live_worker(self):
        stream = build_stream()
        stream.start()
        self.addCleanup(stream.stop)

        with self.assertRaises(RuntimeError):
            stream.drain()


class VolumeSanityTest(unittest.TestCase):
    def test_cumulative_delta_matches_trade_volume_after_the_first_tick(self):
        stream = build_stream()

        stream.handle_tick(TSE, FakeTick(volume=3, total_volume=100))
        stream.handle_tick(TSE, FakeTick(volume=5, total_volume=105))
        stream.handle_tick(TSE, FakeTick(volume=7, total_volume=112))
        stream.drain()

        check = stream.volume_checks()[0]
        self.assertEqual(check["symbol"], "2330")
        self.assertEqual(check["ticks"], 3)
        self.assertEqual(check["cumulative_delta"], 12000)
        self.assertEqual(check["trade_volume_after_first"], 12000)
        self.assertTrue(check["consistent"])

    def test_missing_ticks_break_the_volume_invariant(self):
        stream = build_stream()

        stream.handle_tick(TSE, FakeTick(volume=3, total_volume=100))
        stream.handle_tick(TSE, FakeTick(volume=5, total_volume=120))
        stream.drain()

        check = stream.volume_checks()[0]
        self.assertEqual(check["cumulative_delta"], 20000)
        self.assertEqual(check["trade_volume_after_first"], 5000)
        self.assertFalse(check["consistent"])


class RawTickStoreTest(unittest.TestCase):
    def test_append_raw_ticks_uses_provider_scoped_path_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)

            append_raw_ticks(cache_dir, "2026-08-14", "2330", [{"seq": 1}])
            path = append_raw_ticks(cache_dir, "2026-08-14", "2330", [{"seq": 2}, {"seq": 3}])

            self.assertEqual(
                path, cache_dir / "shioaji" / "ticks" / "2026-08-14" / "2330.jsonl"
            )
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([row["seq"] for row in rows], [1, 2, 3])


class GatedTickStreamSmokeTest(unittest.TestCase):
    def test_smoke_is_blocked_without_the_gate(self):
        api = FakeApi()

        report = run_gated_shioaji_tick_stream_smoke(
            api=api, trading_date="2026-08-14", symbols=["2330"], enabled=False
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "enable_tick_stream_required")
        self.assertEqual(report["side_effects"], [])
        self.assertEqual(api.quote.subscribed, [])

    def test_smoke_requires_candidate_symbols(self):
        report = run_gated_shioaji_tick_stream_smoke(
            api=FakeApi(), trading_date="2026-08-14", symbols=[], enabled=True
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "candidate_symbols_required")
        self.assertEqual(report["side_effects"], [])

    def test_enabled_smoke_subscribes_and_reports_summary(self):
        api = FakeApi(symbols=("2330", "2317"))
        slept = []

        report = run_gated_shioaji_tick_stream_smoke(
            api=api,
            trading_date="2026-08-14",
            symbols=["2330", "2317"],
            enabled=True,
            duration_seconds=5,
            sleep=slept.append,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["health"], HEALTHY)
        self.assertEqual(report["side_effects"], ["quote_subscribe"])
        self.assertFalse(report["checks"]["orders_allowed"])
        self.assertTrue(report["checks"]["candidate_scoped"])
        self.assertEqual(report["summary"]["subscribed"], 2)
        self.assertEqual(report["summary"]["queue_backlog"], 0)
        self.assertEqual(report["summary"]["state"], "stopped")
        self.assertEqual(slept, [5])
        self.assertEqual(len(api.quote.unsubscribed), 2)

    def test_ticks_received_during_the_window_are_flushed_before_the_report(self):
        api = FakeApi(symbols=("2330",))

        def feed(_seconds):
            api.quote.callback(TSE, FakeTick(volume=3, total_volume=100))
            api.quote.callback(TSE, FakeTick(volume=5, total_volume=105))

        report = run_gated_shioaji_tick_stream_smoke(
            api=api,
            trading_date="2026-08-14",
            symbols=["2330"],
            enabled=True,
            duration_seconds=1,
            sleep=feed,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["summary"]["market_ticks"], 2)
        self.assertEqual(report["summary"]["queue_backlog"], 0)
        self.assertTrue(report["summary"]["volume_checks"][0]["consistent"])

    def test_smoke_fails_closed_when_market_data_is_degraded(self):
        api = FakeApi(symbols=("2330",))

        def feed(_seconds):
            api.quote.callback(TSE, FakeTick())

        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")

            report = run_gated_shioaji_tick_stream_smoke(
                api=api,
                trading_date="2026-08-14",
                symbols=["2330"],
                enabled=True,
                cache_dir=blocked,
                duration_seconds=1,
                sleep=feed,
            )

        self.assertEqual(report["status"], "degraded")
        self.assertEqual(report["health"], DEGRADED)
        self.assertEqual(report["review_reason"], "market_data_degraded")
        self.assertEqual(report["summary"]["raw_write_errors"], 1)

    def test_observation_failure_still_releases_subscriptions(self):
        api = FakeApi(symbols=("2330",))

        def explode(_seconds):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_gated_shioaji_tick_stream_smoke(
                api=api,
                trading_date="2026-08-14",
                symbols=["2330"],
                enabled=True,
                duration_seconds=5,
                sleep=explode,
            )

        self.assertEqual(api.quote.unsubscribed, ["contract-2330"])


class LiveValidationTest(unittest.TestCase):
    """P1_LIVE_VALIDATED is a separate bar from a healthy pipeline."""

    def _summary(self, **overrides):
        summary = {
            "health": HEALTHY,
            "subscribed": 1,
            "raw_ticks": 120,
            "market_ticks": 118,
            "dropped_queue_full": 0,
            "worker_errors": 0,
            "queue_backlog": 0,
            "volume_checks": [{"symbol": "2330", "consistent": True}],
        }
        summary.update(overrides)
        return summary

    def test_real_tick_run_passes(self):
        result = evaluate_live_validation(self._summary())

        self.assertTrue(result["passed"])
        self.assertEqual(result["failed"], [])

    def test_healthy_run_without_ticks_is_not_live_validated(self):
        """Outside trading hours the pipeline is fine but proves nothing."""
        result = evaluate_live_validation(
            self._summary(raw_ticks=0, market_ticks=0, volume_checks=[])
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["failed"],
            ["market_ticks_emitted", "raw_ticks_received", "volume_consistent"],
        )

    def test_dropped_ticks_block_live_validation(self):
        result = evaluate_live_validation(
            self._summary(health=DEGRADED, dropped_queue_full=4)
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["failed"], ["health_healthy", "no_dropped_ticks"])

    def test_inconsistent_volume_blocks_live_validation(self):
        result = evaluate_live_validation(
            self._summary(volume_checks=[{"symbol": "2330", "consistent": False}])
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["failed"], ["volume_consistent"])

    def test_gated_smoke_reports_live_validation(self):
        api = FakeApi(symbols=("2330",))

        def feed(_seconds):
            api.quote.callback(TSE, FakeTick(volume=3, total_volume=100))
            api.quote.callback(TSE, FakeTick(volume=5, total_volume=105))

        report = run_gated_shioaji_tick_stream_smoke(
            api=api,
            trading_date="2026-08-14",
            symbols=["2330"],
            enabled=True,
            duration_seconds=1,
            sleep=feed,
        )

        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["live_validation"]["passed"])

    def test_smoke_without_ticks_is_ok_but_not_live_validated(self):
        report = run_gated_shioaji_tick_stream_smoke(
            api=FakeApi(symbols=("2330",)),
            trading_date="2026-08-14",
            symbols=["2330"],
            enabled=True,
        )

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["live_validation"]["passed"])
        self.assertIn("raw_ticks_received", report["live_validation"]["failed"])


class TickSmokeCliTest(unittest.TestCase):
    def _run(self, tmp, **overrides):
        args = Namespace(
            date="2026-08-14",
            candidates_input=None,
            symbols="2330",
            cache_dir=str(Path(tmp) / "raw"),
            duration_seconds=0,
            api_key_env="TW_DAYTRADE_TEST_MISSING_KEY",
            secret_key_env="TW_DAYTRADE_TEST_MISSING_SECRET",
            enable_tick_stream=False,
            output=str(Path(tmp) / "tick-smoke.json"),
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        cmd_simulate_shioaji_tick_smoke(args)
        return json.loads(Path(args.output).read_text(encoding="utf-8"))

    def test_cli_defaults_to_blocked_without_any_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "enable_tick_stream_required")
        self.assertEqual(report["side_effects"], [])
        # No api object was ever built, so no login could have happened.
        self.assertFalse(report["checks"]["simulation_api"])

    def test_cli_blocks_when_credentials_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._run(tmp, enable_tick_stream=True)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "shioaji_credentials_required")
        self.assertEqual(report["side_effects"], [])
        self.assertFalse(report["checks"]["simulation_api"])


if __name__ == "__main__":
    unittest.main()
