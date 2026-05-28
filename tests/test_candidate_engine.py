import unittest

from tw_day_trading_lab.candidate_engine import rank_candidates
from tw_day_trading_lab.models import CandidateInput


class CandidateEngineTest(unittest.TestCase):
    def test_ranks_actionable_candidates_first(self):
        weak = CandidateInput(
            symbol="9999",
            name="weak",
            trading_money=12_000_000,
            change_pct=9.8,
            intraday_range_pct=8.2,
            volume_expansion=4.5,
            theme_strength=0.4,
            structure_quality=0.3,
            crowding_risk=0.9,
        )
        strong = CandidateInput(
            symbol="2330",
            name="strong",
            trading_money=18_500_000_000,
            change_pct=2.1,
            intraday_range_pct=2.6,
            volume_expansion=1.4,
            theme_strength=0.9,
            structure_quality=0.82,
            crowding_risk=0.42,
        )

        ranked = rank_candidates([weak, strong])

        self.assertEqual(ranked[0].symbol, "2330")
        self.assertTrue(ranked[0].next_day_actionable)
        self.assertFalse(ranked[1].next_day_actionable)
        self.assertIn("low_trading_money", ranked[1].downgrade_reasons)


if __name__ == "__main__":
    unittest.main()

