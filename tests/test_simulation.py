import unittest
import json
import tempfile
import threading
from contextlib import redirect_stdout
from argparse import Namespace
from io import StringIO
from pathlib import Path

from tw_day_trading_lab.cli import (
    cmd_simulate_callback_smoke,
    cmd_simulate_ingest_callback,
    cmd_simulate_production_readiness,
    cmd_simulate_restart_sync,
    cmd_simulate_run,
    cmd_simulate_shioaji_smoke,
)
from tw_day_trading_lab.ledger import PaperLedger
from tw_day_trading_lab.models import CandidateScore
from tw_day_trading_lab.simulation import (
    BrokerTrade,
    DryRunSimulationBroker,
    ExecutionCallbackEvent,
    ExecutionLifecycleDecision,
    FileExecutionSyncStore,
    LedgerPosition,
    ProductionReadinessPolicy,
    RiskDecision,
    ShioajiOrderRequest,
    ShioajiOrderRequestBroker,
    ShioajiCallbackStream,
    ShioajiSdkSimulationGateway,
    ShioajiSimulationAdapter,
    SignalIntent,
    SimulationResult,
    build_shioaji_order_request,
    build_shioaji_custom_field,
    build_production_readiness_report,
    build_restart_sync_report,
    classify_execution_lifecycle,
    normalize_shioaji_order_callback,
    normalize_broker_status,
    order_intent_from_idempotency_key,
    reconcile_broker_trades,
    render_production_readiness_markdown,
    render_simulation_markdown,
    restore_ledger_from_positions,
    run_callback_sequence_smoke,
    run_gated_shioaji_callback_stream_smoke,
    run_gated_shioaji_cancel_smoke,
    run_gated_shioaji_order_request_smoke,
    run_gated_shioaji_simulation_login_smoke,
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

    def test_submitted_order_does_not_create_open_position(self):
        broker = DryRunSimulationBroker(raw_status="Status.PendingSubmit")
        adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())

        result = adapter.execute(
            self.make_signal(),
            RiskDecision(approved=True, reason="risk_ok", quantity=1000),
        )

        self.assertEqual(result.status, "submitted")
        self.assertEqual(result.trade.status, "submitted")
        self.assertIsNone(result.position)

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

    def test_submitted_trade_does_not_require_open_ledger_position(self):
        submitted_trade = BrokerTrade(
            idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
            broker_order_id="sim-1",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=1000,
            price=900.0,
            status="submitted",
            raw_status="Status.PendingSubmit",
        )

        summary = reconcile_broker_trades([submitted_trade], PaperLedger())

        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["needs_review"], 0)
        self.assertEqual(summary["samples"][0]["status"], "submitted")

    def test_broker_status_normalization(self):
        self.assertEqual(normalize_broker_status("Filled"), "filled")
        self.assertEqual(normalize_broker_status("PartFilled"), "partial_filled")
        self.assertEqual(normalize_broker_status("Status.PendingSubmit"), "submitted")
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

        self.assertEqual(result.status, "submitted")
        self.assertIsNone(result.position)
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
            simulation = True

            def __init__(self) -> None:
                self.Contracts = FakeContracts()
                self.stock_account = "stock-account"
                self.login_calls: list[dict[str, object]] = []
                self.orders: list[object] = []
                self.place_order_calls: list[tuple[object, object]] = []
                self.cancel_order_calls: list[object] = []

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

            def cancel_order(self, trade):
                self.cancel_order_calls.append(trade)
                return {"status": "CancelRequested"}

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
        cancel = gateway.cancel_order(session, "broker-1")
        self.assertEqual(cancel["status"], "CancelRequested")
        self.assertEqual(api.cancel_order_calls[0]["order"]["id"], "broker-1")

    def test_shioaji_sdk_gateway_rejects_non_simulation_api(self):
        class LiveLikeApi:
            simulation = False

        with self.assertRaisesRegex(ValueError, "simulation=True"):
            ShioajiSdkSimulationGateway(
                api=LiveLikeApi(),
                api_key="test-key",
                secret_key="test-secret",
            )

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

    def test_normalizes_real_shioaji_callback_payload_with_operation_dict(self):
        event = normalize_shioaji_order_callback(
            {"op_type": "New", "op_code": "00"},
            {
                "operation": {"op_type": "New", "op_code": "00", "op_msg": ""},
                "order": {
                    "id": "broker-1",
                    "custom_field": "ABC123",
                    "action": "Buy",
                    "price": 2355.0,
                    "quantity": 1000,
                },
                "contract": {"code": "2330"},
                "status": {"status": "PendingSubmit"},
            },
            trading_date="2026-06-02",
            custom_field_map={"ABC123": "2026-06-02:mvp:2330:vwap-breakout:buy"},
        )

        self.assertEqual(event.idempotency_key, "2026-06-02:mvp:2330:vwap-breakout:buy")
        self.assertEqual(event.normalized_status, "submitted")
        self.assertEqual(event.review_reason, "")

    def test_normalizes_real_shioaji_cancel_callback_from_operation_type(self):
        event = normalize_shioaji_order_callback(
            "OrderState.StockOrder",
            {
                "operation": {"op_type": "Cancel", "op_code": "00", "op_msg": ""},
                "order": {
                    "id": "broker-1",
                    "custom_field": "ABC123",
                    "action": "Buy",
                    "price": 2120.0,
                    "quantity": 1000,
                },
                "contract": {"code": "2330"},
                "status": {"cancel_quantity": 1000},
            },
            trading_date="2026-06-02",
            custom_field_map={"ABC123": "2026-06-02:mvp:2330:vwap-breakout:buy"},
        )

        self.assertEqual(event.idempotency_key, "2026-06-02:mvp:2330:vwap-breakout:buy")
        self.assertEqual(event.normalized_status, "cancelled")
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
            self.assertEqual(len(snapshot["lifecycle_decisions"]), 1)
            self.assertEqual(snapshot["lifecycle_decisions"][0]["ledger_effect"], "open_position")

    def test_file_execution_sync_store_deduplicates_identical_callback_event(self):
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

            self.assertTrue(store.record_callback_event(event))
            self.assertFalse(store.record_callback_event(event))
            snapshot = store.load_snapshot()

            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["lifecycle_decisions"]), 1)
            self.assertEqual(len(snapshot["callback_event_keys"]), 1)

    def test_file_execution_sync_store_skips_stale_callback_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            filled = ExecutionCallbackEvent(
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
            submitted = ExecutionCallbackEvent(
                stat="OrderState.Submitted",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="submitted",
                raw_status="Submitted",
                review_reason="",
                raw={"source": "unit-test"},
            )

            self.assertTrue(store.record_callback_event(filled))
            self.assertFalse(store.record_callback_event(submitted))
            snapshot = store.load_snapshot()

            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(snapshot["callback_events"][0]["normalized_status"], "filled")
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["lifecycle_decisions"]), 1)
            self.assertEqual(
                snapshot["callback_status_by_order"][
                    "2026-05-28|broker-1|2026-05-28:mvp:2330:vwap-breakout:buy"
                ],
                "filled",
            )
            self.assertEqual(len(snapshot["callback_ordering_issues"]), 1)
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["reason"],
                "stale_callback_status",
            )
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["incoming_status"],
                "submitted",
            )
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["current_status"],
                "filled",
            )

    def test_file_execution_sync_store_accepts_later_callback_status_progression(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            submitted = ExecutionCallbackEvent(
                stat="OrderState.Submitted",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="submitted",
                raw_status="Submitted",
                review_reason="",
                raw={"source": "unit-test"},
            )
            filled = ExecutionCallbackEvent(
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

            self.assertTrue(store.record_callback_event(submitted))
            self.assertTrue(store.record_callback_event(filled))
            snapshot = store.load_snapshot()

            self.assertEqual(
                [event["normalized_status"] for event in snapshot["callback_events"]],
                ["submitted", "filled"],
            )
            self.assertEqual(len(snapshot["callback_ordering_issues"]), 0)
            self.assertEqual(
                snapshot["callback_status_by_order"][
                    "2026-05-28|broker-1|2026-05-28:mvp:2330:vwap-breakout:buy"
                ],
                "filled",
            )

    def test_file_execution_sync_store_flags_terminal_state_conflict_after_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            filled = ExecutionCallbackEvent(
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
            cancelled = ExecutionCallbackEvent(
                stat="OrderState.Cancelled",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="cancelled",
                raw_status="Cancelled",
                review_reason="",
                raw={"source": "unit-test"},
            )

            self.assertTrue(store.record_callback_event(filled))
            self.assertFalse(store.record_callback_event(cancelled))
            snapshot = store.load_snapshot()

            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(snapshot["callback_events"][0]["normalized_status"], "filled")
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["lifecycle_decisions"]), 1)
            self.assertEqual(len(snapshot["callback_ordering_issues"]), 1)
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["reason"],
                "terminal_state_conflict",
            )
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["incoming_status"],
                "cancelled",
            )
            self.assertEqual(
                snapshot["callback_ordering_issues"][0]["current_status"],
                "filled",
            )
            self.assertEqual(
                snapshot["callback_status_by_order"][
                    "2026-05-28|broker-1|2026-05-28:mvp:2330:vwap-breakout:buy"
                ],
                "filled",
            )

    def test_file_execution_sync_store_preserves_concurrent_callback_writes(self):
        callback_count = 30
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "execution-sync.json"
            start = threading.Barrier(callback_count)
            errors: list[Exception] = []

            def record(index: int) -> None:
                event = ExecutionCallbackEvent(
                    stat="OrderState.Filled",
                    broker_order_id=f"broker-{index}",
                    idempotency_key=f"2026-05-28:mvp:2330:setup-{index}:buy",
                    trading_date="2026-05-28",
                    symbol="2330",
                    side="buy",
                    quantity=1000,
                    price=900.0 + index,
                    normalized_status="filled",
                    raw_status="Filled",
                    review_reason="",
                    raw={"source": "unit-test", "index": index},
                )
                try:
                    start.wait()
                    FileExecutionSyncStore(store_path).record_callback_event(event)
                except Exception as error:
                    errors.append(error)

            threads = [
                threading.Thread(target=record, args=(index,))
                for index in range(callback_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            snapshot = FileExecutionSyncStore(store_path).load_snapshot()

            self.assertEqual(errors, [])
            self.assertEqual(len(snapshot["callback_events"]), callback_count)
            self.assertEqual(len(snapshot["broker_trades"]), callback_count)
            self.assertEqual(len(snapshot["lifecycle_decisions"]), callback_count)

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

    def test_callback_sequence_smoke_reports_accepted_and_skipped_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "execution-sync.json"
            store = FileExecutionSyncStore(store_path)
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )
            store.record_result(result)
            short_custom_field = build_shioaji_custom_field(result.order_intent.idempotency_key)
            callbacks = [
                {
                    "stat": "OrderState.Submitted",
                    "msg": {
                        "order": {
                            "id": "broker-1",
                            "custom_field": short_custom_field,
                            "action": "Buy",
                            "price": 901.0,
                            "quantity": 1000,
                        },
                        "contract": {"code": "2330"},
                        "status": {"status": "Submitted"},
                    },
                },
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
                {
                    "stat": "OrderState.Submitted",
                    "msg": {
                        "order": {
                            "id": "broker-1",
                            "custom_field": short_custom_field,
                            "action": "Buy",
                            "price": 900.0,
                            "quantity": 1000,
                        },
                        "contract": {"code": "2330"},
                        "status": {"status": "Submitted"},
                    },
                },
                {
                    "stat": "OrderState.Cancelled",
                    "msg": {
                        "order": {
                            "id": "broker-1",
                            "custom_field": short_custom_field,
                            "action": "Buy",
                            "price": 900.0,
                            "quantity": 1000,
                        },
                        "contract": {"code": "2330"},
                        "status": {"status": "Cancelled"},
                    },
                },
            ]

            report = run_callback_sequence_smoke(
                trading_date="2026-05-28",
                callbacks=callbacks,
                store=store,
            )

            self.assertEqual(report["summary"]["total"], 4)
            self.assertEqual(report["summary"]["accepted"], 2)
            self.assertEqual(report["summary"]["skipped"], 2)
            self.assertEqual(
                [event["accepted"] for event in report["events"]],
                [True, True, False, False],
            )
            self.assertEqual(
                [issue["reason"] for issue in report["ordering_issues"]],
                ["stale_callback_status", "terminal_state_conflict"],
            )
            self.assertEqual(report["store_summary"]["callback_events"], 2)

    def test_cli_callback_smoke_writes_sequence_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / "callback-smoke.json"
            output_path = tmpdir / "callback-smoke-output.json"
            store_path = tmpdir / "execution-sync.json"
            store = FileExecutionSyncStore(store_path)
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )
            store.record_result(result)
            short_custom_field = build_shioaji_custom_field(result.order_intent.idempotency_key)
            callback = {
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
            }
            input_path.write_text(
                json.dumps({"callbacks": [callback]}, ensure_ascii=False),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                cmd_simulate_callback_smoke(
                    Namespace(
                        date="2026-05-28",
                        input=str(input_path),
                        store=str(store_path),
                        output=str(output_path),
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["total"], 1)
            self.assertEqual(payload["summary"]["accepted"], 1)
            self.assertEqual(payload["store_summary"]["callback_events"], 1)

    def test_gated_shioaji_login_smoke_blocks_without_explicit_gate(self):
        calls: list[bool] = []

        def api_factory(*, simulation):
            calls.append(simulation)
            return object()

        report = run_gated_shioaji_simulation_login_smoke(
            api_factory=api_factory,
            api_key="test-key",
            secret_key="test-secret",
            enabled=False,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["review_reason"], "enable_login_smoke_required")
        self.assertEqual(calls, [])

    def test_gated_shioaji_login_smoke_uses_simulation_api_and_login_flags(self):
        class FakeApi:
            simulation = True

            def __init__(self) -> None:
                self.stock_account = "stock-account"
                self.login_calls: list[dict[str, object]] = []

            def login(self, **kwargs):
                self.login_calls.append(kwargs)
                return ["stock-account"]

        api = FakeApi()

        report = run_gated_shioaji_simulation_login_smoke(
            api_factory=lambda *, simulation: api,
            api_key="test-key",
            secret_key="test-secret",
            enabled=True,
            fetch_contract=False,
            subscribe_trade=False,
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["session"]["mode"], "simulation")
        self.assertEqual(api.login_calls[0]["api_key"], "test-key")
        self.assertFalse(api.login_calls[0]["fetch_contract"])
        self.assertFalse(api.login_calls[0]["subscribe_trade"])

    def test_gated_callback_stream_smoke_registers_without_order(self):
        class FakeCallbackApi:
            simulation = True

            def __init__(self) -> None:
                self.callback = None

            def set_order_callback(self, callback):
                self.callback = callback

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            api = FakeCallbackApi()

            report = run_gated_shioaji_callback_stream_smoke(
                api=api,
                store=store,
                trading_date="2026-05-28",
                enabled=True,
            )

            self.assertEqual(report["status"], "registered")
            self.assertIsNotNone(api.callback)
            self.assertEqual(report["callback_count"], 0)
            self.assertEqual(report["store_summary"]["callback_events"], 0)

    def test_gated_order_request_smoke_blocks_without_explicit_gate(self):
        class FakeGateway:
            def login(self):
                raise AssertionError("login must not be called")

            def place_order(self, session, request):
                raise AssertionError("place_order must not be called")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_gated_shioaji_order_request_smoke(
                gateway=FakeGateway(),
                store=FileExecutionSyncStore(Path(tmp) / "execution-sync.json"),
                signal=self.make_signal(),
                decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000),
                enabled=False,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["review_reason"], "enable_order_smoke_required")
            self.assertEqual(report["side_effects"], [])

    def test_gated_order_request_smoke_rejects_market_order(self):
        class FakeGateway:
            def login(self):
                raise AssertionError("login must not be called")

            def place_order(self, session, request):
                raise AssertionError("place_order must not be called")

        signal = SignalIntent(
            trading_date="2026-05-28",
            strategy_id="mvp",
            symbol="2330",
            setup_id="vwap-breakout",
            side="buy",
            quantity=1000,
            price=None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_gated_shioaji_order_request_smoke(
                gateway=FakeGateway(),
                store=FileExecutionSyncStore(Path(tmp) / "execution-sync.json"),
                signal=signal,
                decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000),
                enabled=True,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["review_reason"], "limit_price_required")

    def test_gated_order_request_smoke_places_fake_simulation_order_and_reconciles(self):
        class FakeGateway:
            def __init__(self) -> None:
                self.login_count = 0
                self.requests: list[ShioajiOrderRequest] = []

            def login(self):
                self.login_count += 1
                return {"mode": "simulation", "session_id": "fake-session"}

            def place_order(self, session, request):
                self.requests.append(request)
                return {
                    "broker_order_id": "broker-1",
                    "idempotency_key": request.idempotency_key,
                    "status": "Filled",
                    "raw_status": "Filled",
                    "raw": {"trade": "trade-handle-1"},
                }

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            gateway = FakeGateway()

            report = run_gated_shioaji_order_request_smoke(
                gateway=gateway,
                store=store,
                signal=self.make_signal(),
                decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0),
                enabled=True,
                current_time="09:30",
            )

            snapshot = store.load_snapshot()
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["result"]["status"], "simulated")
            self.assertEqual(report["restart_sync"]["matched"], 1)
            self.assertEqual(report["restart_sync"]["needs_review"], 0)
            self.assertEqual(gateway.login_count, 1)
            self.assertEqual(gateway.requests[0].custom_field, build_shioaji_custom_field(report["result"]["idempotency_key"]))
            self.assertEqual(len(snapshot["broker_trades"]), 1)
            self.assertEqual(len(snapshot["open_positions"]), 1)
            self.assertEqual(report["store_summary"]["shioaji_order_handles"], 1)
            self.assertEqual(
                snapshot["shioaji_order_handles"]["broker-1"]["raw"],
                {"trade": "trade-handle-1"},
            )

    def test_gated_order_request_smoke_rejects_outside_regular_session(self):
        class FakeGateway:
            def login(self):
                raise AssertionError("login must not be called")

            def place_order(self, session, request):
                raise AssertionError("place_order must not be called")

        with tempfile.TemporaryDirectory() as tmp:
            report = run_gated_shioaji_order_request_smoke(
                gateway=FakeGateway(),
                store=FileExecutionSyncStore(Path(tmp) / "execution-sync.json"),
                signal=self.make_signal(),
                decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0),
                enabled=True,
                current_time="13:31",
            )

            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["review_reason"], "outside_regular_session")

    def test_gated_order_request_smoke_can_allow_outside_session_for_fake_only(self):
        class FakeGateway:
            def login(self):
                return {"mode": "simulation", "session_id": "fake-session"}

            def place_order(self, session, request):
                return {
                    "broker_order_id": "broker-1",
                    "idempotency_key": request.idempotency_key,
                    "status": "Filled",
                    "raw_status": "Filled",
                }

        with tempfile.TemporaryDirectory() as tmp:
            report = run_gated_shioaji_order_request_smoke(
                gateway=FakeGateway(),
                store=FileExecutionSyncStore(Path(tmp) / "execution-sync.json"),
                signal=self.make_signal(),
                decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0),
                enabled=True,
                current_time="13:31",
                allow_outside_session=True,
            )

            self.assertEqual(report["status"], "ok")
            self.assertTrue(report["checks"]["allow_outside_session"])

    def test_gated_cancel_smoke_blocks_without_gate_or_order_id(self):
        class FakeCancelGateway:
            def login(self):
                raise AssertionError("login must not be called")

            def cancel_order(self, session, broker_order_id, order_handle=None):
                raise AssertionError("cancel_order must not be called")

        blocked = run_gated_shioaji_cancel_smoke(
            gateway=FakeCancelGateway(),
            broker_order_id="broker-1",
            enabled=False,
        )
        missing = run_gated_shioaji_cancel_smoke(
            gateway=FakeCancelGateway(),
            broker_order_id="",
            enabled=True,
        )

        self.assertEqual(blocked["review_reason"], "enable_cancel_smoke_required")
        self.assertEqual(missing["review_reason"], "broker_order_id_required")

    def test_gated_cancel_smoke_calls_gateway_cancel(self):
        class FakeCancelGateway:
            def __init__(self) -> None:
                self.cancelled: list[str] = []
                self.handles: list[dict[str, object] | None] = []

            def login(self):
                return {"mode": "simulation", "session_id": "fake-session"}

            def cancel_order(self, session, broker_order_id, order_handle=None):
                self.cancelled.append(broker_order_id)
                self.handles.append(order_handle)
                return {"broker_order_id": broker_order_id, "status": "cancel_requested"}

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            store.record_order_handle(
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                response={"raw": {"trade": "trade-handle-1"}},
            )
            gateway = FakeCancelGateway()

            report = run_gated_shioaji_cancel_smoke(
                gateway=gateway,
                broker_order_id="broker-1",
                enabled=True,
                store=store,
            )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["result"]["status"], "cancel_requested")
        self.assertTrue(report["result"]["used_order_handle"])
        self.assertEqual(gateway.cancelled, ["broker-1"])
        self.assertEqual(gateway.handles[0]["raw"], {"trade": "trade-handle-1"})
        self.assertEqual(report["store_summary"]["cancel_results"], 1)

    def test_order_handle_persistence_serializes_sdk_like_objects(self):
        class SdkLikeTrade:
            def __init__(self) -> None:
                self.ordno = "broker-1"

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")

            store.record_order_handle(
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                response={"raw": SdkLikeTrade()},
            )

            snapshot = store.load_snapshot()
            self.assertEqual(snapshot["shioaji_order_handles"]["broker-1"]["raw"]["ordno"], "broker-1")

    def test_cli_shioaji_smoke_defaults_to_blocked_report_without_import_or_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "shioaji-smoke.json"

            with redirect_stdout(StringIO()):
                cmd_simulate_shioaji_smoke(
                    Namespace(
                        date="2026-05-28",
                        output=str(output_path),
                        store=None,
                        input=None,
                        api_key_env="SHIOAJI_API_KEY",
                        secret_key_env="SHIOAJI_SECRET_KEY",
                        enable_login_smoke=False,
                        enable_callback_stream=False,
                        enable_order_smoke=False,
                        enable_cancel_smoke=False,
                        cancel_broker_order_id=None,
                        max_order_quantity=1000,
                        current_time=None,
                        allow_outside_session=False,
                        fetch_contract=False,
                        subscribe_trade=False,
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["blocked"], 4)
            self.assertEqual(payload["reports"][0]["review_reason"], "enable_login_smoke_required")
            self.assertEqual(
                payload["reports"][1]["review_reason"],
                "enable_callback_stream_required",
            )
            self.assertEqual(
                payload["reports"][2]["review_reason"],
                "enable_order_smoke_required",
            )
            self.assertEqual(
                payload["reports"][3]["review_reason"],
                "enable_cancel_smoke_required",
            )

    def test_shioaji_callback_stream_records_events_from_registered_callback(self):
        class FakeCallbackApi:
            simulation = True

            def __init__(self) -> None:
                self.callback = None

            def set_order_callback(self, callback):
                self.callback = callback

            def emit(self, stat, msg):
                self.callback(stat, msg)

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            broker = DryRunSimulationBroker()
            adapter = ShioajiSimulationAdapter(broker=broker, ledger=PaperLedger())
            result = adapter.execute(
                self.make_signal(),
                RiskDecision(approved=True, reason="risk_ok", quantity=1000),
            )
            store.record_result(result)
            api = FakeCallbackApi()
            stream = ShioajiCallbackStream(
                api=api,
                store=store,
                trading_date="2026-05-28",
            )

            stream.start()
            api.emit(
                "OrderState.Filled",
                {
                    "order": {
                        "id": "broker-1",
                        "custom_field": build_shioaji_custom_field(result.order_intent.idempotency_key),
                        "action": "Buy",
                        "price": 900.0,
                        "quantity": 1000,
                    },
                    "contract": {"code": "2330"},
                    "status": {"status": "Filled"},
                },
            )

            snapshot = store.load_snapshot()
            self.assertEqual(stream.callback_count, 1)
            self.assertEqual(len(snapshot["callback_events"]), 1)
            self.assertEqual(snapshot["callback_events"][0]["idempotency_key"], result.order_intent.idempotency_key)
            self.assertEqual(len(snapshot["broker_trades"]), 2)

    def test_shioaji_callback_stream_rejects_non_simulation_api(self):
        class LiveLikeApi:
            simulation = False

        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            with self.assertRaisesRegex(ValueError, "simulation=True"):
                ShioajiCallbackStream(
                    api=LiveLikeApi(),
                    store=store,
                    trading_date="2026-05-28",
                )

    def test_execution_lifecycle_policy_for_filled_callback(self):
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

        decision = classify_execution_lifecycle(event)

        self.assertIsInstance(decision, ExecutionLifecycleDecision)
        self.assertEqual(decision.ledger_effect, "open_position")
        self.assertEqual(decision.action, "confirm_open_position")
        self.assertFalse(decision.needs_review)

    def test_execution_lifecycle_policy_marks_partial_fill_for_review(self):
        event = ExecutionCallbackEvent(
            stat="OrderState.PartFilled",
            broker_order_id="broker-1",
            idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
            trading_date="2026-05-28",
            symbol="2330",
            side="buy",
            quantity=500,
            price=900.0,
            normalized_status="partial_filled",
            raw_status="PartFilled",
            review_reason="",
            raw={"source": "unit-test"},
        )

        decision = classify_execution_lifecycle(event)

        self.assertEqual(decision.ledger_effect, "hold_for_review")
        self.assertEqual(decision.action, "partial_fill_manual_reconciliation")
        self.assertTrue(decision.needs_review)
        self.assertEqual(decision.review_reason, "partial_fill_requires_policy")

    def test_execution_lifecycle_policy_for_cancelled_or_rejected_callback(self):
        for status in ("cancelled", "rejected"):
            with self.subTest(status=status):
                event = ExecutionCallbackEvent(
                    stat=f"OrderState.{status}",
                    broker_order_id="broker-1",
                    idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                    trading_date="2026-05-28",
                    symbol="2330",
                    side="buy",
                    quantity=1000,
                    price=900.0,
                    normalized_status=status,
                    raw_status=status,
                    review_reason="",
                    raw={"source": "unit-test"},
                )

                decision = classify_execution_lifecycle(event)

                self.assertEqual(decision.ledger_effect, "close_intent")
                self.assertEqual(decision.action, f"{status}_release_intent")
                self.assertFalse(decision.needs_review)

    def test_production_readiness_blocks_live_execution_without_manual_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")

            report = build_production_readiness_report(
                store.load_snapshot(),
                ProductionReadinessPolicy(
                    trading_date="2026-05-28",
                    current_time="09:30",
                    allow_live_trading=False,
                    manual_approval_token="",
                    expected_manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                ),
            )

            self.assertEqual(report["status"], "blocked")
            self.assertFalse(report["live_execution_allowed"])
            self.assertEqual(report["checks"][0]["name"], "formal_live_gate")
            self.assertEqual(report["checks"][0]["status"], "blocked")
            self.assertIn("Keep live trading disabled", report["manual_actions"][0])

    def test_production_readiness_flags_pending_partial_and_ordering_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            partial = ExecutionCallbackEvent(
                stat="OrderState.PartFilled",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=500,
                price=900.0,
                normalized_status="partial_filled",
                raw_status="PartFilled",
                review_reason="",
                raw={"source": "unit-test"},
            )
            submitted = ExecutionCallbackEvent(
                stat="OrderState.Submitted",
                broker_order_id="broker-2",
                idempotency_key="2026-05-28:mvp:2317:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2317",
                side="buy",
                quantity=1000,
                price=150.0,
                normalized_status="submitted",
                raw_status="Submitted",
                review_reason="",
                raw={"source": "unit-test"},
            )
            filled = ExecutionCallbackEvent(
                stat="OrderState.Filled",
                broker_order_id="broker-3",
                idempotency_key="2026-05-28:mvp:2454:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2454",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="filled",
                raw_status="Filled",
                review_reason="",
                raw={"source": "unit-test"},
            )
            stale = ExecutionCallbackEvent(
                stat="OrderState.Submitted",
                broker_order_id="broker-3",
                idempotency_key="2026-05-28:mvp:2454:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2454",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="submitted",
                raw_status="Submitted",
                review_reason="",
                raw={"source": "unit-test"},
            )
            store.record_callback_event(partial)
            store.record_callback_event(submitted)
            store.record_callback_event(filled)
            store.record_callback_event(stale)

            report = build_production_readiness_report(
                store.load_snapshot(),
                ProductionReadinessPolicy(
                    trading_date="2026-05-28",
                    current_time="09:30",
                    allow_live_trading=True,
                    manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                    expected_manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                ),
            )

            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["summary"]["pending_orders"], 1)
            self.assertEqual(report["summary"]["partial_fills"], 1)
            self.assertEqual(report["summary"]["ordering_issues"], 1)
            self.assertIn("partial_fill_policy", [alert["name"] for alert in report["alerts"]])
            self.assertIn("cancel_retry_plan", [alert["name"] for alert in report["alerts"]])

    def test_production_readiness_can_be_ready_with_clean_state_and_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            cancelled = ExecutionCallbackEvent(
                stat="OrderState.Cancelled",
                broker_order_id="broker-1",
                idempotency_key="2026-05-28:mvp:2330:vwap-breakout:buy",
                trading_date="2026-05-28",
                symbol="2330",
                side="buy",
                quantity=1000,
                price=900.0,
                normalized_status="cancelled",
                raw_status="Cancelled",
                review_reason="",
                raw={"source": "unit-test"},
            )
            store.record_callback_event(cancelled)

            report = build_production_readiness_report(
                store.load_snapshot(),
                ProductionReadinessPolicy(
                    trading_date="2026-05-28",
                    current_time="09:30",
                    allow_live_trading=True,
                    manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                    expected_manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                ),
            )

            self.assertEqual(report["status"], "ready")
            self.assertTrue(report["live_execution_allowed"])
            self.assertEqual(report["summary"]["blocked"], 0)
            self.assertEqual(report["alerts"], [])
            self.assertIn("live execution allowed: true", render_production_readiness_markdown(report))

    def test_production_readiness_accepts_pending_submit_cancel_result_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileExecutionSyncStore(Path(tmp) / "execution-sync.json")
            store.record_cancel_result(
                broker_order_id="broker-1",
                response={
                    "status": "trade object string",
                    "raw": {"status": {"status": "PendingSubmit"}},
                },
            )

            report = build_production_readiness_report(
                store.load_snapshot(),
                ProductionReadinessPolicy(
                    trading_date="2026-05-28",
                    current_time="09:30",
                    allow_live_trading=True,
                    manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                    expected_manual_approval_token="2026-05-28:LIVE-TRADING-APPROVED",
                ),
            )

            self.assertEqual(report["status"], "ready")
            self.assertNotIn("cancel_retry_plan", [alert["name"] for alert in report["alerts"]])

    def test_cli_production_readiness_writes_json_and_markdown_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store_path = tmpdir / "execution-sync.json"
            output_path = tmpdir / "readiness.json"
            report_path = tmpdir / "readiness.md"
            FileExecutionSyncStore(store_path)

            with redirect_stdout(StringIO()):
                cmd_simulate_production_readiness(
                    Namespace(
                        date="2026-05-28",
                        store=str(store_path),
                        output=str(output_path),
                        report_output=str(report_path),
                        current_time="13:30",
                        allow_live_trading=False,
                        manual_approval_token=None,
                        expected_manual_approval_token=None,
                        max_pending_orders=0,
                        disable_cancel_retry_plan=False,
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            markdown = report_path.read_text(encoding="utf-8")
            self.assertEqual(payload["mode"], "production_readiness")
            self.assertEqual(payload["status"], "blocked")
            self.assertIn("outside_regular_session", markdown)

    def test_check_pre_order_gates_validation(self):
        from tw_day_trading_lab.simulation import check_pre_order_gates
        broker = DryRunSimulationBroker()

        # 1. Weekend check ("2026-05-30" is Saturday)
        sig_weekend = SignalIntent(
            trading_date="2026-05-30", strategy_id="mvp", symbol="2330", setup_id="breakout", side="buy", quantity=1000, price=900.0
        )
        dec = RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0)
        res = check_pre_order_gates(broker, sig_weekend, dec, current_time="10:00")
        self.assertFalse(res["approved"])
        self.assertIn("not_a_trading_day", res["blocked_reasons"])

        # 2. Outside session check
        sig_weekday = SignalIntent(
            trading_date="2026-05-28", strategy_id="mvp", symbol="2330", setup_id="breakout", side="buy", quantity=1000, price=900.0
        )
        res = check_pre_order_gates(broker, sig_weekday, dec, current_time="14:00")
        self.assertFalse(res["approved"])
        self.assertIn("outside_regular_session", res["blocked_reasons"])

        # 3. Quantity cap check
        dec_large = RiskDecision(approved=True, reason="risk_ok", quantity=1500, price=900.0)
        res = check_pre_order_gates(broker, sig_weekday, dec_large, current_time="10:00")
        self.assertFalse(res["approved"])
        self.assertIn("quantity_exceeds_cap", res["blocked_reasons"])

        # 4. Price out of limits check
        # DryRunSimulationBroker defaults to reference=900, limit_up=990, limit_down=810
        dec_high = RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=1000.0)
        res = check_pre_order_gates(broker, sig_weekday, dec_high, current_time="10:00")
        self.assertFalse(res["approved"])
        self.assertIn("price_above_limit_up", res["blocked_reasons"])

        # 5. All ok
        res = check_pre_order_gates(broker, sig_weekday, dec, current_time="10:00")
        self.assertTrue(res["approved"])

    def test_build_simulation_plan_contract(self):
        from tw_day_trading_lab.simulation import build_simulation_plan
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # Setup mock candidate price files in cache
            price_dir = tmpdir / "finmind" / "TaiwanStockPrice" / "2026-05-27"
            price_dir.mkdir(parents=True, exist_ok=True)
            (price_dir / "2330.jsonl").write_text(
                json.dumps({"date": "2026-05-27", "close": 900.0}) + "\n",
                encoding="utf-8"
            )

            cand = CandidateScore(
                symbol="2330", name="TSMC", rank=1, archetype="breakout_continuation",
                total_score=80.0, liquidity_score=90.0, event_score=80.0,
                structure_score=70.0, continuity_score=80.0, crowding_penalty=10.0,
                next_day_actionable=True, reasons=("liquid_enough",), downgrade_reasons=()
            )

            plan = build_simulation_plan(
                trading_date="2026-05-28",
                candidate_date="2026-05-27",
                candidates=[cand],
                cache_dir=tmpdir,
                candidate_source="mock_candidates.json"
            )

            self.assertEqual(len(plan), 1)
            item = plan[0]
            self.assertEqual(item.signal.symbol, "2330")
            self.assertEqual(item.signal.price, 900.0)
            self.assertTrue(item.risk_decision.approved)
            self.assertEqual(item.candidate_source, "mock_candidates.json")
            self.assertEqual(item.limit_price_source, "close_price")
            self.assertEqual(item.quantity_source, "fixed_size_1000")
            self.assertIsNone(item.blocked_reason)

    def test_cmd_simulate_ops_run_subcommand(self):
        from tw_day_trading_lab.cli import cmd_simulate_ops_run
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # 1. Setup mock candidates file
            candidates_path = tmpdir / "candidates.json"
            candidates_path.write_text(
                json.dumps({
                    "trading_date": "2026-05-27",
                    "candidates": [
                        {
                            "symbol": "2330",
                            "name": "TSMC",
                            "rank": 1,
                            "archetype": "breakout_continuation",
                            "total_score": 80.0,
                            "liquidity_score": 90.0,
                            "event_score": 80.0,
                            "structure_score": 70.0,
                            "continuity_score": 80.0,
                            "crowding_penalty": 10.0,
                            "next_day_actionable": True,
                            "reasons": ["liquid_enough"],
                            "downgrade_reasons": []
                        }
                    ]
                }),
                encoding="utf-8"
            )

            # 2. Setup mock price file in cache
            price_dir = tmpdir / "raw" / "finmind" / "TaiwanStockPrice" / "2026-05-27"
            price_dir.mkdir(parents=True, exist_ok=True)
            (price_dir / "2330.jsonl").write_text(
                json.dumps({"date": "2026-05-27", "close": 900.0}) + "\n",
                encoding="utf-8"
            )

            output_dir = tmpdir / "ops_output"
            sync_store_path = tmpdir / "execution-sync.json"

            # 3. Run the subcommand
            cmd_simulate_ops_run(
                Namespace(
                    date="2026-05-28",
                    candidates_input=str(candidates_path),
                    candidate_date=None,
                    cache_dir=str(tmpdir / "raw"),
                    execution_sync_store=str(sync_store_path),
                    output_dir=str(output_dir),
                    simulation_on=False,
                    allow_outside_session=True,
                    current_time="10:00",
                    allow_live_trading=False,
                    manual_approval_token=None,
                    expected_manual_approval_token=None,
                    max_pending_orders=0,
                    disable_cancel_retry_plan=False
                )
            )

            # 4. Verify created artifacts
            self.assertTrue((output_dir / "input_plan.json").exists())
            self.assertTrue((output_dir / "simulation_output.json").exists())
            self.assertTrue((output_dir / "callback_store.json").exists())
            self.assertTrue((output_dir / "restart_sync.json").exists())
            self.assertTrue((output_dir / "readiness_report.json").exists())
            self.assertTrue((output_dir / "readiness_report.md").exists())
            self.assertTrue((output_dir / "ops_run_manifest.json").exists())

            # 5. Verify manifest content
            manifest = json.loads((output_dir / "ops_run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["trading_date"], "2026-05-28")
            self.assertTrue(manifest["simulation_only"])
            self.assertGreater(len(manifest["input_artifacts"]), 0)
            self.assertGreater(len(manifest["output_artifacts"]), 0)
            self.assertIn("place_order_2330", manifest["side_effects"])

    def test_generate_alerts_from_run(self):
        from tw_day_trading_lab.simulation import generate_alerts_from_run, Alert

        candidates = [
            CandidateScore(
                symbol="2330", name="TSMC", rank=1, archetype="theme_follower",
                total_score=50.0, liquidity_score=50.0, event_score=50.0,
                structure_score=50.0, continuity_score=50.0, crowding_penalty=0.0,
                next_day_actionable=True, reasons=(), downgrade_reasons=("low_trading_money",)
            )
        ]

        sim_results = [
            SimulationResult(
                status="gate_blocked", sample_type="simulation", expectancy_eligible=False,
                signal=SignalIntent(trading_date="2026-05-28", strategy_id="mvp", symbol="2330", setup_id="breakout", side="buy", quantity=1000, price=900.0),
                risk_decision=RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0),
                review_reason="outside_regular_session"
            )
        ]

        readiness_report = {
            "alerts": [
                {"severity": "blocker", "name": "formal_live_gate", "review_reason": "live_trading_requires_explicit_manual_approval", "detail": {}}
            ]
        }

        alerts = generate_alerts_from_run("run-test-123", candidates, sim_results, readiness_report)
        self.assertEqual(len(alerts), 3)
        self.assertEqual(alerts[0].category, "candidate_quality")
        self.assertEqual(alerts[1].category, "execution_health")
        self.assertEqual(alerts[2].category, "readiness")

    def test_regression_import_and_run_tdd_cycle(self):
        from tw_day_trading_lab.cli import cmd_simulate_regression_import, cmd_simulate_regression_run
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            alerts_path = tmpdir / "alerts.json"
            alerts_path.write_text(
                json.dumps([
                    {
                        "alert_id": "alert-run-test-readiness-formal_live_gate",
                        "run_id": "run-test",
                        "severity": "error",
                        "category": "readiness",
                        "dedupe_key": "readiness_formal_live_gate_run-test",
                        "owner": "execution_operator",
                        "manual_action": "Resolve formal live gate issue",
                        "send_gate": False
                    }
                ]),
                encoding="utf-8"
            )

            manifest_path = tmpdir / "ops_run_manifest.json"
            manifest_path.write_text(
                json.dumps({
                    "run_id": "run-test",
                    "trading_date": "2026-05-28",
                    "output_artifacts": [
                        {"path": str(alerts_path), "checksum": "abc"}
                    ]
                }),
                encoding="utf-8"
            )

            output_dir = tmpdir / "regression"

            cmd_simulate_regression_import(
                Namespace(
                    manifest=str(manifest_path),
                    output_dir=str(output_dir)
                )
            )

            case_file = output_dir / "case-run-test-readiness-formal_live_gate.json"
            self.assertTrue(case_file.exists())

            with self.assertRaises(SystemExit):
                cmd_simulate_regression_run(
                    Namespace(
                        case=str(case_file),
                        mock_fix=False
                    )
                )

            cmd_simulate_regression_run(
                Namespace(
                    case=str(case_file),
                    mock_fix=True
                )
            )

            case_data = json.loads(case_file.read_text(encoding="utf-8"))
            self.assertEqual(case_data["status"], "fixed")

    def test_live_broker_adapter_strict_gates(self):
        from tw_day_trading_lab.live import LiveShioajiBrokerAdapter

        class MockLiveApi:
            simulation = False

        api = MockLiveApi()

        import hashlib
        token = "secure_token_value_at_least_32_chars_long"
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        adapter = LiveShioajiBrokerAdapter(api, token_hash)

        # 1. Blocked if allow_live_trading=False
        res = adapter.check_live_execution_gate(
            allow_live_trading=False,
            manual_approval_token=token,
            regression_dir=Path("non_existent_path")
        )
        self.assertFalse(res.allowed)
        self.assertIn("allow_live_trading_is_false", res.blocked_reasons)

        # 2. Blocked if token is too short (predictable/date-based)
        res = adapter.check_live_execution_gate(
            allow_live_trading=True,
            manual_approval_token="short_token",
            regression_dir=Path("non_existent_path")
        )
        self.assertFalse(res.allowed)
        self.assertIn("manual_approval_token_is_too_weak_must_be_at_least_32_chars", res.blocked_reasons)

        # 3. Blocked if token hash mismatches
        res = adapter.check_live_execution_gate(
            allow_live_trading=True,
            manual_approval_token="another_secure_token_value_at_least_32_chars",
            regression_dir=Path("non_existent_path")
        )
        self.assertFalse(res.allowed)
        self.assertIn("manual_approval_token_hash_mismatch", res.blocked_reasons)

        # 4. Blocked if there are open regression cases
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "case_1.json").write_text(json.dumps({"case_id": "case_1", "status": "open"}), encoding="utf-8")

            res = adapter.check_live_execution_gate(
                allow_live_trading=True,
                manual_approval_token=token,
                regression_dir=tmpdir
            )
            self.assertFalse(res.allowed)
            self.assertIn("open_regression_case_present_case_1", res.blocked_reasons)

        # 5. Approved if all conditions match
        res = adapter.check_live_execution_gate(
            allow_live_trading=True,
            manual_approval_token=token,
            regression_dir=Path("non_existent_path")
        )
        self.assertTrue(res.allowed)

        sig = SignalIntent(trading_date="2026-05-28", strategy_id="mvp", symbol="2330", setup_id="breakout", side="buy", quantity=1000, price=900.0)
        dec = RiskDecision(approved=True, reason="risk_ok", quantity=1000, price=900.0)
        trade = adapter.place_live_order(sig, dec, res)
        self.assertEqual(trade.source, "live")
        self.assertEqual(trade.status, "submitted")

        class MockSimApi:
            simulation = True
        with self.assertRaises(ValueError):
            LiveShioajiBrokerAdapter(MockSimApi(), token_hash)


if __name__ == "__main__":
    unittest.main()
