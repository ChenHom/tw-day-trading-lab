import json
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.candidate_builder import build_candidates_from_raw_cache, write_jsonl


class CandidateBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def write_price_rows(self, stock_id, rows):
        path = self.cache_dir / "finmind" / "TaiwanStockPrice" / "2026-05-28" / f"{stock_id}.jsonl"
        write_jsonl(path, rows)

    def test_builds_ranked_candidates_from_price_cache(self):
        self.write_price_rows(
            "2330",
            [
                {
                    "date": "2026-05-27",
                    "stock_id": "2330",
                    "Trading_Volume": 10_000_000,
                    "Trading_money": 20_000_000_000,
                    "open": 980,
                    "max": 1000,
                    "min": 970,
                    "close": 990,
                    "spread": 10,
                },
                {
                    "date": "2026-05-28",
                    "stock_id": "2330",
                    "Trading_Volume": 28_000_000,
                    "Trading_money": 58_000_000_000,
                    "open": 1000,
                    "max": 1070,
                    "min": 995,
                    "close": 1060,
                    "spread": 70,
                },
            ],
        )

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
            limit=10,
        )

        self.assertEqual(result.summary["input_files"], 1)
        self.assertEqual(result.summary["built_candidates"], 1)
        self.assertEqual(result.candidates[0].symbol, "2330")
        self.assertEqual(result.candidates[0].data_quality, "ok")
        self.assertGreater(result.ranked[0].total_score, 55)

    def test_low_liquidity_rows_are_filtered_before_ranking(self):
        self.write_price_rows(
            "9999",
            [
                {
                    "date": "2026-05-28",
                    "stock_id": "9999",
                    "Trading_Volume": 1_000,
                    "Trading_money": 1_500_000,
                    "open": 10,
                    "max": 10.5,
                    "min": 9.8,
                    "close": 10.2,
                    "spread": 0.2,
                }
            ],
        )

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
            min_trading_money=80_000_000,
        )

        self.assertEqual(result.summary["built_candidates"], 0)
        self.assertEqual(result.summary["filtered_low_liquidity"], 1)
        self.assertEqual(result.ranked, [])

    def test_single_day_price_cache_is_degraded_but_not_crashing(self):
        self.write_price_rows(
            "2330",
            [
                {
                    "date": "2026-05-28",
                    "stock_id": "2330",
                    "Trading_Volume": 42_000_000,
                    "Trading_money": 98_000_000_000,
                    "open": 2350,
                    "max": 2360,
                    "min": 2270,
                    "close": 2295,
                    "spread": -5,
                }
            ],
        )

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
        )

        self.assertEqual(result.summary["degraded_candidates"], 1)
        self.assertEqual(result.candidates[0].data_quality, "degraded")
        self.assertIn("data_quality_degraded", result.ranked[0].downgrade_reasons)

    def test_malformed_rows_are_reported_as_data_gaps(self):
        path = self.cache_dir / "finmind" / "TaiwanStockPrice" / "2026-05-28" / "0000.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": "2026-05-28", "stock_id": "0000"}) + "\n", encoding="utf-8")

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
        )

        self.assertEqual(result.summary["data_gap_files"], 1)
        self.assertEqual(result.candidates, [])


if __name__ == "__main__":
    unittest.main()
