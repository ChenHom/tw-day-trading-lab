import unittest

from tw_day_trading_lab.models import CandidateScore
from tw_day_trading_lab.reports import render_close_report_markdown, render_markdown


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


if __name__ == "__main__":
    unittest.main()
