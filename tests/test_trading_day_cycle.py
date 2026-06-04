import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tw_day_trading_lab.cli import cmd_simulate_trading_day_cycle, resolve_trading_day_cycle_stage, write_json, write_text


def trading_day_cycle_args(root: Path, **overrides: object) -> Namespace:
    args = {
        "date": "2026-06-04",
        "cache_dir": str(root / "raw"),
        "trading_data_input": None,
        "market_proxy_stock_id": "0050",
        "candidates_input": None,
        "output_dir": str(root / "cycle"),
        "state_output": None,
        "run_id": None,
        "start_policy": "09:05",
        "hard_stop_time": "13:20",
        "close_buffer_end_time": "14:00",
        "report_time": "15:00",
        "next_candidate_time": "17:30",
        "current_time": "09:05",
        "max_retries": 2,
        "run_all_stages": False,
    }
    args.update(overrides)
    return Namespace(**args)


class TradingDayCycleTest(unittest.TestCase):
    def test_trading_day_cycle_full_dry_run_uses_api_data_availability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "raw" / "finmind" / "TaiwanStockPrice" / "2026-06-04" / "0050.jsonl"
            write_text(
                data_path,
                json.dumps({"date": "2026-06-04", "stock_id": "0050", "close": 188.0}) + "\n",
            )
            candidates_path = root / "candidates.json"
            write_json(candidates_path, {"trading_date": "2026-06-04", "candidates": []})

            cmd_simulate_trading_day_cycle(
                trading_day_cycle_args(
                    root,
                    candidates_input=str(candidates_path),
                    run_all_stages=True,
                )
            )

            state_path = root / "cycle" / "trading_day_run_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["calendar_status"], "trading_day")
            self.assertEqual(state["calendar_rule"], "api_data_availability_only")
            self.assertEqual(state["stage"], "complete")
            self.assertEqual(state["trading_data_probe"]["source"], str(data_path))
            self.assertTrue(state["candidate_artifact"]["exists"])
            self.assertIsNotNone(state["candidate_artifact"]["checksum"])
            self.assertEqual(state["side_effects"], [])
            self.assertEqual(
                [item["stage"] for item in state["stage_history"]],
                [
                    "candidate_ready",
                    "intraday_waiting",
                    "intraday_running",
                    "force_exit",
                    "close_buffer",
                    "reporting",
                    "next_candidates",
                    "complete",
                ],
            )

    def test_trading_day_cycle_no_api_rows_marks_non_trading_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            cmd_simulate_trading_day_cycle(trading_day_cycle_args(root))

            state = json.loads((root / "cycle" / "trading_day_run_state.json").read_text(encoding="utf-8"))

            self.assertEqual(state["calendar_status"], "non_trading_day")
            self.assertEqual(state["stage"], "blocked")
            self.assertIn("trading_data_unavailable", state["blocked_reasons"])
            self.assertEqual(state["trading_data_probe"]["reason"], "trading_data_unavailable")

    def test_clock_policy_resolves_intraday_and_reporting_stages(self) -> None:
        self.assertEqual(
            resolve_trading_day_cycle_stage(
                current_time="10:00",
                start_policy="10:00",
                hard_stop_time="13:20",
                close_buffer_end_time="14:00",
                report_time="15:00",
                next_candidate_time="17:30",
            ),
            "intraday_running",
        )
        self.assertEqual(
            resolve_trading_day_cycle_stage(
                current_time="13:25",
                start_policy="09:05",
                hard_stop_time="13:20",
                close_buffer_end_time="14:00",
                report_time="15:00",
                next_candidate_time="17:30",
            ),
            "force_exit",
        )
        self.assertEqual(
            resolve_trading_day_cycle_stage(
                current_time="15:00",
                start_policy="09:05",
                hard_stop_time="13:20",
                close_buffer_end_time="14:00",
                report_time="15:00",
                next_candidate_time="17:30",
            ),
            "reporting",
        )


if __name__ == "__main__":
    unittest.main()
