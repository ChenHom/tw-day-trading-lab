import unittest
import json
import tempfile
from contextlib import redirect_stdout
from argparse import Namespace
from io import StringIO
from pathlib import Path

from tw_day_trading_lab.cli import cmd_simulate_run
from tw_day_trading_lab.ledger import PaperLedger
from tw_day_trading_lab.simulation import (
    BrokerTrade,
    DryRunSimulationBroker,
    RiskDecision,
    ShioajiSimulationAdapter,
    SignalIntent,
    normalize_broker_status,
    reconcile_broker_trades,
    render_simulation_markdown,
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
                    )
                )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")

            self.assertEqual(payload["summary"]["total"], 2)
            self.assertEqual(payload["summary"]["simulated"], 1)
            self.assertEqual(payload["summary"]["duplicate"], 1)
            self.assertEqual(payload["summary"]["expectancy_eligible"], 0)
            self.assertIn("Simulation samples", report)


if __name__ == "__main__":
    unittest.main()
