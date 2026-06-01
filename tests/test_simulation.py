import unittest
import json
import tempfile
from contextlib import redirect_stdout
from argparse import Namespace
from io import StringIO
from pathlib import Path

from tw_day_trading_lab.cli import (
    cmd_simulate_ingest_callback,
    cmd_simulate_restart_sync,
    cmd_simulate_run,
)
from tw_day_trading_lab.ledger import PaperLedger
from tw_day_trading_lab.simulation import (
    BrokerTrade,
    DryRunSimulationBroker,
    ExecutionCallbackEvent,
    FileExecutionSyncStore,
    LedgerPosition,
    RiskDecision,
    ShioajiOrderRequest,
    ShioajiOrderRequestBroker,
    ShioajiSdkSimulationGateway,
    ShioajiSimulationAdapter,
    SignalIntent,
    build_shioaji_order_request,
    build_shioaji_custom_field,
    build_restart_sync_report,
    normalize_shioaji_order_callback,
    normalize_broker_status,
    order_intent_from_idempotency_key,
    reconcile_broker_trades,
    render_simulation_markdown,
    restore_ledger_from_positions,
)


class SimulationAdapterTest(unittest.TestCase):
    def make_signal(self) -> SignalIntent:
        return SignalIntent(
            trading_date="2026-05-28",
            strategy_id="mvp",
            symbol="2330",
            setup_id="vwap-breakout",
            side="buy",
            quantity=1000,
            price=900.0,
        )

    def test_approved_signal_creates_simulation_sample_without_expectancy_eligibility(self):
        broker = DryRunSimulationBroker()
        adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())

        result = adapter.execute(
            self.make_signal(),
            RiskDecision(approved=True, reason="risk_ok", quantity=1000),
        )

        self.assertEqual(result.status, "simulated")
        self.assertEqual(result.sample_type, "simulation")
        self.assertFalse(result.expectancy_eligible)
        self.assertEqual(result.trade.status, "filled")
        self.assertEqual(result.position.status, "open")
        self.assertEqual(broker.login_count, 1)
        self.assertEqual(broker.place_order_count, 1)
        self.assertIn("broker_trade", result.to_dict())

    def test_duplicate_intent_is_rejected_before_broker_order(self):
        broker = DryRunSimulationBroker()
        adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
        signal = self.make_signal()
        decision = RiskDecision(approved=True, reason="risk_ok", quantity=1000)

        first = adapter.execute(signal, decision)
        duplicate = adapter.execute(signal, decision)

        self.assertEqual(first.status, "simulated")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertFalse(duplicate.expectancy_eligible)
        self.assertEqual(broker.place_order_count, 1)
        self.assertIn("duplicate", duplicate.review_reason)

    def test_rejected_risk_decision_does_not_place_order(self):
        broker = DryRunSimulationBroker()
        adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())

        result = adapter.execute(
            self.make_signal(),
            RiskDecision(approved=False, reason="daily_stop_hit"),
        )

        self.assertEqual(result.status, "risk_rejected")
        self.assertIsNone(result.trade)
        self.assertFalse(result.expectancy_eligible)
        self.assertEqual(broker.place_order_count, 0)

    def test_broker_ledger_mismatch_is_needs_review_and_not_expectancy_eligible(self):
        orphan_trade = BrokerTrade(
            idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
            broker_order_id="sim-1",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=1000,
            price=900.0,
            status="filled",
            raw_status="Filled",
        )

        summary = reconcile_broker_trades([orphan_trade], PaperLedger())

        self.assertEqual(summary["checked"], 1)
        self.assertEqual(summary["needs_review"], 1)
        self.assertFalse(summary["samples"][0]["expectancy_eligible"])
        self.assertEqual(summary["samples"][0]["validity"], "needs_review")
        self.assertIn("ledger_missing_open_intent", summary["samples"][0]["review_reason"])

    def test_broker_status_normalization(self):
        self.assertEqual(normalize_broker_status("Filled"), "filled")
        self.assertEqual(normalize_broker_status("PartFilled"), "partial_filled")
        self.assertEqual(normalize_broker_status("Cancelled"), "cancelled")
        self.assertEqual(normalize_broker_status("unknown-new-status"), "needs_review")

    def test_build_shioaji_order_request_uses_idempotency_as_custom_field(self):
        signal = self.make_signal()
        intent = signal.to_order_intent()

        request = build_shioaji_order_request(
            intent,
            signal,
            RiskDecision(approved=True, reason="risk_ok", quantity=2000, price=901.5),
        )

        self.assertEqual(request.symbol, "2330")
        self.assertEqual(request.side, "buy")
        self.assertEqual(request.quantity, 2000)
        self.assertEqual(request.price, 901.5)
        self.assertEqual(request.idempotency_key, intent.idempotency_key)
        self.assertEqual(request.custom_field, build_shioaji_custom_field(intent.idempotency_key))
        self.assertLessEqual(len(request.custom_field), 6)
        self.assertRegex(request.custom_field, r"^[A-Z2-7]{6}$")

    def test_shioaji_order_request_broker_sends_custom_field_to_gateway(self):
        class RecordingGateway:
            def __init__(self) -> None:
                self.requests: list[ShioajiOrderRequest] = []

            def login(self) -> dict[str, str]:
                return {"mode": "simulation", "session_id": "fake-shioaji"}

            def place_order(
                self,
                session: dict[str, str],
                request: ShioajiOrderRequest,
            ) -> dict[str, object]:
                self.requests.append(request)
                return {
                    "broker_order_id": "broker-1",
                    "status": "Submitted",
                    "raw_status": "Submitted",
                }

        gateway = RecordingGateway()
        adapter = ShioajiSimulationAdapter(
            broker=ShioajiOrderRequestBroker(gateway),
            ledger=PaperLedger(),
        )

        result = adapter.execute(
            self.make_signal(),
            RiskDecision(approved=True, reason="risk_ok", quantity=1000),
        )

        self.assertEqual(result.status, "simulated")
        self.assertEqual(len(gateway.requests), 1)
        self.assertEqual(gateway.requests[0].idempotency_key, result.order_intent.idempotency_key)
        self.assertLessEqual(len(gateway.requests[0].custom_field), 6)
        event = normalize_shioaji_order_callback(
            "OrderState.Submitted",
            {
                "order": {
                    "id": "broker-1",
                    "custom_field": gateway.requests[0].custom_field,
                    "action": "Buy",
                    "price": gateway.requests[0].price,
                    "quantity": gateway.requests[0].quantity,
                },
                "contract": {"code": gateway.requests[0].symbol},
                "status": {"status": "Submitted"},
            },
            trading_date=gateway.requests[0].trading_date,
            custom_field_map={
                gateway.requests[0].custom_field: result.order_intent.idempotency_key,
            },
        )
        self.assertEqual(event.idempotency_key, result.order_intent.idempotency_key)
        self.assertEqual(event.review_reason, "")

    def test_unmapped_short_custom_field_requires_review(self):
        event = normalize_shioaji_order_callback(
            "OrderState.Submitted",
            {
                "order": {
                    "id": "broker-1",
                    "custom_field": "ABC123",
                    "action": "Buy",
                    "price": 900.0,
                    "quantity": 1000,
                },
                "contract": {"code": "2330"},
                "status": {"status": "Submitted"},
            },
            trading_date="2026-05-28",
        )

        self.assertEqual(event.idempotency_key, "")
        self.assertEqual(event.normalized_status, "needs_review")
        self.assertEqual(event.review_reason, "unresolved_custom_field")

    def test_shioaji_sdk_gateway_builds_sdk_order_without_real_login(self):
        class FakeContracts:
            Stocks = {"2330": {"code": "2330"}}

        class FakeApi:
            def __init__(self) -> None:
                self.Contracts = FakeContracts()
                self.stock_account = "stock-account"
                self.login_calls: list[dict[str, object]] = []
                self.orders: list[object] = []
                self.place_order_calls: list[tuple[object, object]] = []

            def login(self, **kwargs):
                self.login_calls.append(kwargs)
                return ["stock-account"]

            def Order(self, **kwargs):
                self.orders.append(kwargs)
                return kwargs

            def place_order(self, contract, order):
                self.place_order_calls.append((contract, order))
                return {
                    "order": {
                        "id": "broker-1",
                        "custom_field": order["custom_field"],
                    },
                    "status": {"status": "Submitted"},
                }

        api = FakeApi()
        gateway = ShioajiSdkSimulationGateway(
            api=api,
            api_key="test-key",
            secret_key="test-secret",
        )
        request = build_shioaji_order_request(
            self.make_signal().to_order_intent(),
            self.make_signal(),
            RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0),
        )

        session = gateway.login()
        response = gateway.place_order(session, request)

        self.assertEqual(api.login_calls[0]["api_key"], "test-key")
        self.assertTrue(api.login_calls[0]["fetch_contract"])
        self.assertEqual(api.orders[0]["custom_field"], request.custom_field)
        self.assertEqual(api.orders[0]["account"], "stock-account")
        self.assertEqual(response["custom_field"], request.custom_field)
        self.assertEqual(response["broker_order_id"], "broker-1")

    def test_markdown_report_separates_simulation_from_replay_expectancy(self):
        broker = DryRunSimulationBroker()
        adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
        result = adapter.execute(
            self.make_signal(),
            RiskDecision(approved=True, reason="risk_ok", quantity=1000),
        )

        report = render_simulation_markdown("2026-05-28", [result])

        self.assertIn("Simulation samples", report)
        self.assertIn("expectancy eligible: 0", report)
        self.assertIn("replay expectancy", report)

    def test_cli_simulation_run_writes_separate_simulation_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / "simulation-plan.json"
            output_path = tmpdir / "simulation-output.json"
            report_path = tmpdir / "simulation-report.md"
            signal = self.make_signal()
            input_path.write_text(
                json.dumps(
                    [
                        {
                            "signal": signal.__dict__,
                            "risk_decision": {
                                "approved": True,
                                "reason": "risk_ok",
                                "quantity": 1000,
                            },
                        },
                        {
                            "signal": signal.__dict__,
                            "risk_decision": {
                                "approved": True,
                                "reason": "risk_ok",
                                "quantity": 1000,
                            },
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cmd_simulate_run(
                    Namespace(
                        date="2026-05-28",
                        input=str(input_path),
                        output=str(output_path),
                        report_output=str(report_path),
                        execution_sync_store=None,
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")

            self.assertEqual(payload["summary"]["total"], 2)
            self.assertEqual(payload["summary"]["simulated"], 1)
            self.assertEqual(payload["summary"]["duplicate"], 1)
            self.assertEqual(payload["summary"]["expectancy_eligible"], 0)
            self.assertIn("Simulation samples", report)

    def test_cli_simulation_run_can_persist_execution_sync_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / "simulation-plan.json"
            output_path = tmpdir / "simulation-output.json"
            store_path = tmpdir / "execution-sync.json"
            signal = self.make_signal()
            input_path.write_text(
                json.dumps(
                    [
                        {
                            "signal": signal.__dict__,
                            "risk_decision": {
                                "approved": True,
                                "reason": "risk_ok",
                                "quantity": 1000,
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cmd_simulate_run(
                    Namespace(
                        date="2026-05-28",
                        input=str(input_path),
                        output=str(output_path),
                        report_output=None,
                        execution_sync_store=str(store_path),
                    )
                )

            snapshot = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["open_positions"]), 1)

    def test_file_execution_sync_store_persists_open_positions_and_broker_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )

            store.record_result(result)
            snapshot = store.load_snapshot()

            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["open_positions"]), 1)
            self.assertEqual(
                snapshot["open_positions"][0]["idempotency_key"],
                "2026-05-28:mvp:2330:vwap-breakout:buy",
            )
            self.assertEqual(
                snapshot["custom_field_map"][build_shioaji_custom_field(result.order_intent.idempotency_key)],
                result.order_intent.idempotency_key,
            )

    def test_restart_sync_restores_ledger_and_matches_broker_state(self):
        position = LedgerPosition(
            position_id="sim:restart-order-1",
            idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=1000,
            average_price=900.0,
            status="open",
        )
        trade = BrokerTrade(
            idempotency_key=position.idempotency_key,
            broker_order_id="restart-order-1",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=1000,
            price=900.0,
            status="filled",
            raw_status="Filled",
        )

        ledger = restore_ledger_from_positions([position])
        report = build_restart_sync_report([trade], [position], ledger)

        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["needs_review"], 0)

    def test_restart_sync_flags_ledger_position_missing_broker_trade(self):
        position = LedgerPosition(
            position_id="sim:restart-order-1",
            idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=1000,
            average_price=900.0,
            status="open",
        )

        ledger = restore_ledger_from_positions([position])
        report = build_restart_sync_report([], [position], ledger)

        self.assertEqual(report["checked"], 1)
        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["needs_review"], 1)
        self.assertEqual(report["samples"][0]["review_reason"], "ledger_missing_broker_trade")

    def test_cli_restart_sync_reads_store_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store_path = tmpdir / "execution-sync.json"
            output_path = tmpdir / "restart-sync.json"
            store = FileExecutionSyncStore(store_path)
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )
            store.record_result(result)

            with redirect_stdout(StringIO()):
                cmd_simulate_restart_sync(
                    Namespace(
                        store=str(store_path),
                        output=str(output_path),
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["matched"], 1)
            self.assertEqual(payload["needs_review"], 0)

    def test_idempotency_key_round_trip_to_order_intent(self):
        intent = order_intent_from_idempotency_key("2026-05-28:mvp:2330:vwap-breakout:buy")

        self.assertEqual(intent.trading_date, "2026-05-28")
        self.assertEqual(intent.strategy_id, "mvp")
        self.assertEqual(intent.symbol, "2330")
        self.assertEqual(intent.setup_id, "vwap-breakout")
        self.assertEqual(intent.side, "buy")

    def test_normalizes_shioaji_order_callback_dict_payload(self):
        event = normalize_shioaji_order_callback(
            "OrderState.Filled",
            {
                "order": {
                    "id": "broker-1",
                    "custom_field": "2026-05-28:mvp:2330:vwap-breakout:buy",
                    "action": "Buy",
                    "price": 900.0,
                    "quantity": 1000,
                },
                "contract": {"code": "2330"},
                "status": {"status": "Filled"},
            },
            trading_date="2026-05-28",
        )

        self.assertEqual(event.broker_order_id, "broker-1")
        self.assertEqual(event.idempotency_key, "2026-05-28:mvp:2330:vwap-breakout:buy")
        self.assertEqual(event.symbol, "2330")
        self.assertEqual(event.normalized_status, "filled")
        self.assertEqual(event.review_reason, "")

    def test_callback_event_to_broker_trade_requires_idempotency_key(self):
        event = normalize_shioaji_order_callback(
            "OrderState.Filled",
            {
                "order": {"id": "broker-1", "action": "Buy", "price": 900.0, "quantity": 1000},
                "contract": {"code": "2330"},
                "status": {"status": "Filled"},
            },
            trading_date="2026-05-28",
        )

        self.assertEqual(event.normalized_status, "needs_review")
        self.assertEqual(event.review_reason, "missing_idempotency_key")
        self.assertIsNone(event.to_broker_trade())

    def test_file_execution_sync_store_records_callback_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            event = ExecutionCallbackEvent(
                stat="OrderState.Filled",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="filled",
                raw_status="Filled",
                review_reason="",
                raw={"source": "unit-test"},
            )

            store.record_callback_event(event)
            snapshot = store.load_snapshot()

            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(snapshot["broker_trades"][0]["broker_order_id"], "broker-1")

    def test_cli_ingest_callback_normalizes_and_records_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            callback_path = tmpdir / "callback.json"
            store_path = tmpdir / "execution-sync.json"
            output_path = tmpdir / "callback-event.json"
            callback_path.write_text(
                json.dumps(
                    {
                        "stat": "OrderState.Filled",
                        "msg": {
                            "order": {
                                "id": "broker-1",
                                "custom_field": "2026-05-28:mvp:2330:vwap-breakout:buy",
                                "action": "Buy",
                                "price": 900.0,
                                "quantity": 1000,
                            },
                            "contract": {"code": "2330"},
                            "status": {"status": "Filled"},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cmd_simulate_ingest_callback(
                    Namespace(
                        date="2026-05-28",
                        input=str(callback_path),
                        store=str(store_path),
                        output=str(output_path),
                    )
                )

            event = json.loads(output_path.read_text(encoding="utf-8"))
            snapshot = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertEqual(event["normalized_status"], "filled")
            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(len(snapshot["broker_trades"]), 1)

    def test_cli_ingest_callback_resolves_short_custom_field_from_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            callback_path = tmpdir / "callback.json"
            store_path = tmpdir / "execution-sync.json"
            output_path = tmpdir / "callback-event.json"
            store = FileExecutionSyncStore(store_path)
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )
            store.record_result(result)
            short_custom_field = build_shioaji_custom_field(result.order_intent.idempotency_key)
            callback_path.write_text(
                json.dumps(
                    {
                        "stat": "OrderState.Filled",
                        "msg": {
                            "order": {
                                "id": "broker-1",
                                "custom_field": short_custom_field,
                                "action": "Buy",
                                "price": 900.0,
                                "quantity": 1000,
                            },
                            "contract": {"code": "2330"},
                            "status": {"status": "Filled"},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cmd_simulate_ingest_callback(
                    Namespace(
                        date="2026-05-28",
                        input=str(callback_path),
                        store=str(store_path),
                        output=str(output_path),
                    )
                )

            event = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(event["idempotency_key"], result.order_intent.idempotency_key)
            self.assertEqual(event["review_reason"], "")


if __name__ == "__main__":
    unittest.main()
