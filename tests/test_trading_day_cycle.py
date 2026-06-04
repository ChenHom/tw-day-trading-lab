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
        "intraday_bars_input": None,
        "position_state_input": None,
        "output_dir": str(root / "cycle"),
        "state_output": None,
        "watch_events_output": None,
        "run_id": None,
        "start_policy": "09:05",
        "hard_stop_time": "13:20",
        "close_buffer_end_time": "14:00",
        "report_time": "15:00",
        "next_candidate_time": "17:30",
        "current_time": "09:05",
        "max_retries": 2,
        "run_all_stages": False,
        "strategy_observation_minutes": 15,
        "strategy_volume_surge_ratio": 1.5,
    }
    args.update(overrides)
    return Namespace(**args)


class TradingDayCycleTest(unittest.TestCase):
    def write_trading_day_probe(self, root: Path) -> Path:
        data_path = root / "raw" / "finmind" / "TaiwanStockPrice" / "2026-06-04" / "0050.jsonl"
        write_text(
            data_path,
            json.dumps({"date": "2026-06-04", "stock_id": "0050", "close": 188.0}) + "\n",
        )
        return data_path

    def write_candidates(self, root: Path, symbols: list[str]) -> Path:
        candidates = []
        for index, symbol in enumerate(symbols, start=1):
            candidates.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "rank": index,
                    "archetype": "momentum",
                    "total_score": 80 - index,
                    "liquidity_score": 20,
                    "event_score": 20,
                    "structure_score": 20,
                    "continuity_score": 20,
                    "crowding_penalty": 0,
                    "next_day_actionable": True,
                    "reasons": ["fixture"],
                    "downgrade_reasons": [],
                }
            )
        path = root / "candidates.json"
        write_json(path, {"trading_date": "2026-06-04", "candidates": candidates})
        return path

    def breakout_bars(self, symbol: str) -> list[dict[str, object]]:
        bars = [
            {"symbol": symbol, "time": f"09:{minute:02d}", "open": 100, "high": 100, "low": 99, "close": 100, "volume": 100}
            for minute in range(15)
        ]
        bars.append({"symbol": symbol, "time": "09:15", "open": 100, "high": 103, "low": 100, "close": 102, "volume": 200})
        return bars

    def test_trading_day_cycle_full_dry_run_uses_api_data_availability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = self.write_trading_day_probe(root)
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

    def test_intraday_watch_loop_emits_entry_and_no_action_for_candidates_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_trading_day_probe(root)
            candidates_path = self.write_candidates(root, ["2330", "2317"])
            bars_path = root / "intraday_bars.json"
            write_json(
                bars_path,
                {
                    "2330": self.breakout_bars("2330"),
                    "2317": [
                        {"time": f"09:{minute:02d}", "open": 80, "high": 80, "low": 79, "close": 79.5, "volume": 100}
                        for minute in range(16)
                    ],
                    "9999": self.breakout_bars("9999"),
                },
            )

            cmd_simulate_trading_day_cycle(
                trading_day_cycle_args(
                    root,
                    candidates_input=str(candidates_path),
                    intraday_bars_input=str(bars_path),
                    current_time="09:20",
                )
            )

            watch_events = json.loads((root / "cycle" / "watch_events.json").read_text(encoding="utf-8"))["events"]
            state = json.loads((root / "cycle" / "trading_day_run_state.json").read_text(encoding="utf-8"))

            self.assertEqual([event["symbol"] for event in watch_events], ["2330", "2317"])
            self.assertEqual(watch_events[0]["action"], "entry_approved")
            self.assertEqual(watch_events[0]["entry_signal"]["reason"], "opening_range_breakout_with_volume_surge")
            self.assertEqual(watch_events[1]["action"], "no_action")
            self.assertEqual(state["watch_events_artifact"]["summary"]["entry_approved"], 1)
            self.assertEqual(state["watch_events_artifact"]["summary"]["no_action"], 1)

    def test_open_position_exit_is_evaluated_before_new_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_trading_day_probe(root)
            candidates_path = self.write_candidates(root, ["2330"])
            bars_path = root / "intraday_bars.json"
            position_path = root / "position_state.json"
            write_json(bars_path, {"2330": self.breakout_bars("2330")})
            write_json(
                position_path,
                {
                    "open_positions": [
                        {
                            "symbol": "2330",
                            "entry_price": 100,
                            "stop_price": 98,
                            "target_price": 101,
                            "quantity": 1000,
                        }
                    ]
                },
            )

            cmd_simulate_trading_day_cycle(
                trading_day_cycle_args(
                    root,
                    candidates_input=str(candidates_path),
                    intraday_bars_input=str(bars_path),
                    position_state_input=str(position_path),
                    current_time="09:20",
                )
            )

            event = json.loads((root / "cycle" / "watch_events.json").read_text(encoding="utf-8"))["events"][0]

            self.assertEqual(event["action"], "exit_approved")
            self.assertEqual(event["exit_signal"]["reason"], "take_profit")
            self.assertIsNone(event["entry_signal"])
            self.assertIn(":exit:", event["order_intent_id"])

    def test_after_hard_stop_rejects_new_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_trading_day_probe(root)
            candidates_path = self.write_candidates(root, ["2330"])
            bars_path = root / "intraday_bars.json"
            write_json(bars_path, {"2330": self.breakout_bars("2330")})

            cmd_simulate_trading_day_cycle(
                trading_day_cycle_args(
                    root,
                    candidates_input=str(candidates_path),
                    intraday_bars_input=str(bars_path),
                    current_time="13:25",
                )
            )

            event = json.loads((root / "cycle" / "watch_events.json").read_text(encoding="utf-8"))["events"][0]

            self.assertEqual(event["action"], "entry_rejected")
            self.assertEqual(event["reason"], "after_hard_stop")
            self.assertFalse(event["risk_decision"]["approved"])


if __name__ == "__main__":
    unittest.main()
