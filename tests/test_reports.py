import unittest

from tw_day_trading_lab.models import CandidateScore
from tw_day_trading_lab.reports import render_markdown


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
            },
        )

        self.assertIn("## 資料來源", report)
        self.assertIn("finmind_raw_cache", report)
        self.assertIn("資料缺口檔案：1", report)


if __name__ == "__main__":
    unittest.main()
