import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from tw_day_trading_lab.cli import audit_daily_ops_bundle, cmd_simulate_daily_ops, get_file_checksum, write_json, write_text


class DailyOpsAutomationTest(unittest.TestCase):
    def test_daily_ops_builds_auditable_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "daily"
            cmd_simulate_daily_ops(
                Namespace(
                    date="2026-06-04",
                    cache_dir=str(root / "raw"),
                    requests=None,
                    dataset="TaiwanStockPrice",
                    stock_id=None,
                    start_date=None,
                    market_proxy_stock_id="0050",
                    market_proxy_start_date=None,
                    token=None,
                    quota_limit=540,
                    skip_ingestion=True,
                    allow_ingestion_failure=False,
                    candidates_output=None,
                    candidate_date=None,
                    limit=80,
                    min_trading_money=80_000_000,
                    execution_sync_store=None,
                    output_dir=str(output_dir),
                    ops_output_dir=None,
                    simulation_on=False,
                    allow_outside_session=True,
                    current_time="12:00",
                    max_pending_orders=0,
                    disable_cancel_retry_plan=False,
                    close_report_output=None,
                    telegram_summary_output=None,
                    regression_output_dir=None,
                    skip_regression_import=False,
                    audit_output=None,
                    fail_on_audit=True,
                )
            )

            manifest_path = output_dir / "ops" / "ops_run_manifest.json"
            audit_path = output_dir / "daily_bundle_audit.json"
            close_report_path = output_dir / "close.md"
            regression_dir = output_dir / "regression"

            self.assertTrue(manifest_path.exists())
            self.assertTrue(audit_path.exists())
            self.assertTrue(close_report_path.exists())
            self.assertTrue(regression_dir.exists())

            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "ok")
            self.assertEqual(audit["summary"]["failed"], 0)
            step_names = {step["name"] for step in audit["steps"]}
            self.assertIn("candidate_build", step_names)
            self.assertIn("ops_run", step_names)
            self.assertIn("regression_import", step_names)
            self.assertIn("bundle_audit", step_names)

    def test_daily_ops_generates_date_scoped_ingestion_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "daily"
            with patch("tw_day_trading_lab.cli.cmd_ingest_finmind") as ingest_mock:
                cmd_simulate_daily_ops(
                    Namespace(
                        date="2026-06-04",
                        cache_dir=str(root / "raw"),
                        requests=None,
                        dataset="TaiwanStockPrice",
                        stock_id=None,
                        start_date=None,
                        market_proxy_stock_id="0050",
                        market_proxy_start_date="2026-05-01",
                        token=None,
                        quota_limit=540,
                        skip_ingestion=False,
                        allow_ingestion_failure=False,
                        candidates_output=None,
                        candidate_date=None,
                        limit=80,
                        min_trading_money=80_000_000,
                        execution_sync_store=None,
                        output_dir=str(output_dir),
                        ops_output_dir=None,
                        simulation_on=False,
                        allow_outside_session=True,
                        current_time="12:00",
                        max_pending_orders=0,
                        disable_cancel_retry_plan=False,
                        close_report_output=None,
                        telegram_summary_output=None,
                        regression_output_dir=None,
                        skip_regression_import=True,
                        audit_output=None,
                        fail_on_audit=True,
                        send_alerts=False,
                    )
                )

            ingest_args = ingest_mock.call_args.args[0]
            request_path = Path(ingest_args.requests)
            request_payload = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertTrue(all(item["trading_date"] == "2026-06-04" for item in request_payload))
            proxy_request = [item for item in request_payload if item["stock_id"] == "0050"][0]
            self.assertEqual(proxy_request["start_date"], "2026-05-01")

    def test_bundle_audit_detects_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input_plan.json"
            alerts_path = root / "alerts.json"
            readiness_path = root / "readiness_report.json"
            simulation_path = root / "simulation_output.json"
            restart_path = root / "restart_sync.json"
            callback_path = root / "callback_store.json"
            close_path = root / "close.md"
            missing_path = root / "missing.json"

            write_json(input_path, [])
            write_json(alerts_path, [])
            write_json(readiness_path, {"status": "blocked", "summary": {}})
            write_json(simulation_path, {"summary": {}, "results": []})
            write_json(restart_path, {})
            write_json(callback_path, {})
            write_text(close_path, "# close\n")

            manifest_path = root / "ops_run_manifest.json"
            manifest = {
                "run_id": "run-test",
                "trading_date": "2026-06-04",
                "input_artifacts": [
                    {"path": str(input_path), "checksum": get_file_checksum(input_path)},
                ],
                "output_artifacts": [
                    {"path": str(simulation_path), "checksum": get_file_checksum(simulation_path)},
                    {"path": str(restart_path), "checksum": get_file_checksum(restart_path)},
                    {"path": str(readiness_path), "checksum": get_file_checksum(readiness_path)},
                    {"path": str(alerts_path), "checksum": get_file_checksum(alerts_path)},
                    {"path": str(callback_path), "checksum": get_file_checksum(callback_path)},
                    {"path": str(missing_path), "checksum": "missing"},
                ],
            }
            write_json(manifest_path, manifest)

            audit = audit_daily_ops_bundle(
                manifest_path=manifest_path,
                close_report_path=close_path,
                regression_dir=None,
            )

            self.assertEqual(audit["status"], "failed")
            failed_names = {check["name"] for check in audit["checks"] if not check["ok"]}
            self.assertIn("artifact_exists:missing.json", failed_names)


if __name__ == "__main__":
    unittest.main()
