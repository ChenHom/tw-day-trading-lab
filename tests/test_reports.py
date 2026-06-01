import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tw_day_trading_lab.cli import cmd_report_close
from tw_day_trading_lab.models import CandidateScore
from tw_day_trading_lab.reports import (
    render_close_report_markdown,
    render_close_report_telegram_summary,
    render_markdown,
)


class ReportTest(unittest.TestCase):
    def test_markdown_report_contains_actionable_count(self):
        report = render_markdown(
            "2026-05-28",
            [
                CandidateScore(
                    symbol="2330",
                    name="台積電",
                    rank=1,
                    archetype="expansion_from_base",
                    total_score=82.0,
                    liquidity_score=100.0,
                    event_score=72.0,
                    structure_score=82.0,
                    continuity_score=60.0,
                    crowding_penalty=42.0,
                    next_day_actionable=True,
                    reasons=("liquid_enough",),
                    downgrade_reasons=(),
                )
            ],
        )

        self.assertIn("`next_day_actionable`：1", report)
        self.assertIn("2330", report)

    def test_markdown_report_contains_data_source_summary(self):
        report = render_markdown(
            "2026-05-28",
            [],
            source_summary={
                "source": "finmind_raw_cache",
                "input_files": 3,
                "built_candidates": 2,
                "degraded_candidates": 1,
                "data_gap_files": 1,
                "filtered_low_liquidity": 1,
                "filtered_non_common_stock": 1,
                "missing_chip_files": 2,
                "missing_margin_files": 3,
            },
        )

        self.assertIn("## 資料來源", report)
        self.assertIn("finmind_raw_cache", report)
        self.assertIn("資料缺口檔案：1", report)
        self.assertIn("非普通股過濾：1", report)
        self.assertIn("缺法人資料：2", report)
        self.assertIn("缺融資融券資料：3", report)

    def test_close_report_keeps_replay_and_simulation_separate(self):
        report = render_close_report_markdown(
            "2026-05-28",
            candidates=[
                CandidateScore(
                    symbol="2330",
                    name="台積電",
                    rank=1,
                    archetype="theme_follower",
                    total_score=49.66,
                    liquidity_score=100.0,
                    event_score=38.84,
                    structure_score=27.78,
                    continuity_score=41.51,
                    crowding_penalty=22.17,
                    next_day_actionable=False,
                    reasons=("liquid_enough",),
                    downgrade_reasons=("weak_structure",),
                )
            ],
            candidate_source_summary={
                "source": "finmind_raw_cache",
                "built_candidates": 1,
                "degraded_candidates": 0,
                "data_gap_files": 0,
            },
            replay_summary={
                "total_samples": 5,
                "replayed": 1,
                "skipped_excluded": 3,
                "skipped_needs_review": 1,
                "expectancy_gross_r": -1.2,
                "average_cost_r": 0.1,
                "expectancy_net_r": -1.3,
            },
            simulation_summary={
                "total": 3,
                "expectancy_eligible": 0,
                "simulated": 1,
                "duplicate": 1,
                "needs_review": 1,
            },
        )

        self.assertIn("## Candidate Close", report)
        self.assertIn("finmind_raw_cache", report)
        self.assertIn("## Strategy Replay", report)
        self.assertIn("expectancy scope: `validity = valid` only", report)
        self.assertIn("expectancy Net R：-1.3000", report)
        self.assertIn("## Execution Simulation", report)
        self.assertIn("simulation 不納入 replay expectancy", report)
        self.assertIn("needs_review：1", report)

    def test_close_report_lists_needs_review_details(self):
        report = render_close_report_markdown(
            "2026-05-28",
            candidates=[],
            simulation_summary={
                "total": 1,
                "expectancy_eligible": 0,
                "needs_review": 1,
            },
            simulation_results=[
                {
                    "status": "needs_review",
                    "signal": {"symbol": "2330"},
                    "order_intent": {
                        "trading_date": "2026-05-28",
                        "strategy_id": "mvp",
                        "symbol": "2330",
                        "setup_id": "vwap-breakout",
                        "side": "buy",
                    },
                    "review_reason": "broker_status_needs_review",
                }
            ],
        )

        self.assertIn("## Needs Review Details", report)
        self.assertIn("2330", report)
        self.assertIn("2026-05-28:mvp:2330:vwap-breakout:buy", report)
        self.assertIn("broker_status_needs_review", report)

    def test_telegram_summary_is_structured_not_first_lines(self):
        summary = render_close_report_telegram_summary(
            "2026-05-28",
            candidates=[
                CandidateScore(
                    symbol="2330",
                    name="台積電",
                    rank=1,
                    archetype="theme_follower",
                    total_score=49.66,
                    liquidity_score=100.0,
                    event_score=38.84,
                    structure_score=27.78,
                    continuity_score=41.51,
                    crowding_penalty=22.17,
                    next_day_actionable=False,
                    reasons=("liquid_enough",),
                    downgrade_reasons=("weak_structure",),
                )
            ],
            replay_summary={"expectancy_net_r": -1.3, "replayed": 1},
            simulation_summary={"total": 2, "simulated": 1, "duplicate": 1, "needs_review": 1},
        )

        self.assertIn("台股當沖 Close Summary 2026-05-28", summary)
        self.assertIn("candidates/actionable：1 / 0", summary)
        self.assertIn("replay net R：-1.3000", summary)
        self.assertIn("simulation：simulated 1 / needs_review 1", summary)
        self.assertIn("action：先處理 needs_review，再進下一步", summary)

    def test_close_report_cli_rejects_replay_payload_without_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            candidates_path = tmpdir / "candidates.json"
            replay_path = tmpdir / "replay.json"
            output_path = tmpdir / "close.md"
            candidates_path.write_text(
                json.dumps({"candidates": []}, ensure_ascii=False),
                encoding="utf-8",
            )
            replay_path.write_text(json.dumps({"trades": []}, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "replay payload must contain a summary object"):
                with redirect_stdout(StringIO()):
                    cmd_report_close(
                        Namespace(
                            date="2026-05-28",
                            candidates=str(candidates_path),
                            replay=str(replay_path),
                            simulation=None,
                            output=str(output_path),
                            telegram_summary_output=None,
                        )
                    )


if __name__ == "__main__":
    unittest.main()
