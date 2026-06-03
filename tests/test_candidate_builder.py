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

    def write_dataset_rows(self, dataset, stock_id, rows):
        path = self.cache_dir / "finmind" / dataset / "2026-05-28" / f"{stock_id}.jsonl"
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
        self.write_dataset_rows(
            "TaiwanStockInfo",
            "market",
            [{"stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體業"}],
        )
        self.write_dataset_rows(
            "TaiwanStockInstitutionalInvestorsBuySell",
            "2330",
            [{"date": "2026-05-28", "stock_id": "2330", "buy": 1200, "sell": 400}],
        )
        self.write_dataset_rows(
            "TaiwanStockMarginPurchaseShortSale",
            "2330",
            [
                {
                    "date": "2026-05-28",
                    "stock_id": "2330",
                    "MarginPurchaseTodayBalance": 900,
                    "MarginPurchaseYesterdayBalance": 1000,
                }
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

    def test_stock_info_excludes_etf_before_ranking(self):
        self.write_price_rows(
            "0050",
            [
                {
                    "date": "2026-05-27",
                    "stock_id": "0050",
                    "Trading_Volume": 10_000_000,
                    "Trading_money": 1_000_000_000,
                    "open": 180,
                    "max": 181,
                    "min": 178,
                    "close": 179,
                    "spread": 1,
                },
                {
                    "date": "2026-05-28",
                    "stock_id": "0050",
                    "Trading_Volume": 20_000_000,
                    "Trading_money": 2_000_000_000,
                    "open": 180,
                    "max": 185,
                    "min": 179,
                    "close": 184,
                    "spread": 5,
                },
            ],
        )
        self.write_dataset_rows(
            "TaiwanStockInfo",
            "market",
            [{"stock_id": "0050", "stock_name": "元大台灣50", "industry_category": "ETF"}],
        )

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
        )

        self.assertEqual(result.summary["filtered_non_common_stock"], 1)
        self.assertEqual(result.candidates, [])

    def test_chip_and_margin_enrichment_keep_complete_candidate_ok(self):
        self.write_price_rows(
            "2330",
            [
                {
                    "date": "2026-05-24",
                    "stock_id": "2330",
                    "Trading_Volume": 10_000_000,
                    "Trading_money": 20_000_000_000,
                    "open": 950,
                    "max": 980,
                    "min": 940,
                    "close": 960,
                    "spread": 10,
                },
                {
                    "date": "2026-05-27",
                    "stock_id": "2330",
                    "Trading_Volume": 11_000_000,
                    "Trading_money": 22_000_000_000,
                    "open": 960,
                    "max": 990,
                    "min": 955,
                    "close": 970,
                    "spread": 10,
                },
                {
                    "date": "2026-05-28",
                    "stock_id": "2330",
                    "Trading_Volume": 35_000_000,
                    "Trading_money": 75_000_000_000,
                    "open": 980,
                    "max": 1060,
                    "min": 975,
                    "close": 1050,
                    "spread": 80,
                },
            ],
        )
        self.write_dataset_rows(
            "TaiwanStockInfo",
            "market",
            [{"stock_id": "2330", "stock_name": "台積電", "industry_category": "半導體業"}],
        )
        self.write_dataset_rows(
            "TaiwanStockInstitutionalInvestorsBuySell",
            "2330",
            [{"date": "2026-05-28", "stock_id": "2330", "buy": 2000, "sell": 500}],
        )
        self.write_dataset_rows(
            "TaiwanStockMarginPurchaseShortSale",
            "2330",
            [
                {
                    "date": "2026-05-28",
                    "stock_id": "2330",
                    "MarginPurchaseTodayBalance": 9000,
                    "MarginPurchaseYesterdayBalance": 10000,
                }
            ],
        )

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
        )

        self.assertEqual(result.summary["missing_chip_files"], 0)
        self.assertEqual(result.summary["missing_margin_files"], 0)
        self.assertEqual(result.candidates[0].name, "台積電")
        self.assertEqual(result.candidates[0].data_quality, "ok")
        self.assertGreater(result.candidates[0].theme_strength, 0.5)

    def test_missing_chip_or_margin_marks_candidate_degraded_without_crashing(self):
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
        )

        self.assertEqual(result.summary["missing_chip_files"], 1)
        self.assertEqual(result.summary["missing_margin_files"], 1)
        self.assertEqual(result.candidates[0].data_quality, "degraded")


    # ------------------------------------------------------------------ Sprint 3-A

    def _make_price_row(self, date, stock_id, trading_money=100_000_000,
                        trading_volume=600_000, close=100,
                        open_=99, high=101, low=98, spread=1):
        return {
            "date": date,
            "stock_id": stock_id,
            "Trading_Volume": trading_volume,
            "Trading_money": trading_money,
            "open": open_,
            "max": high,
            "min": low,
            "close": close,
            "spread": spread,
        }

    def test_low_adv_20d_is_filtered(self):
        """Stock whose 20-day average Trading_money < 50 M is dropped."""
        # Build 21 rows: 20 historical days at 10 M each + target day at 80 M.
        # ADV-20 over the last 20 rows = (19 * 10 M + 80 M) / 20 = 13.5 M < 50 M
        rows = []
        for i in range(20, 0, -1):
            rows.append(self._make_price_row(
                f"2026-05-{i:02d}", "8888",
                trading_money=10_000_000,
                trading_volume=600_000,
                close=100,
            ))
        rows.append(self._make_price_row(
            "2026-05-28", "8888",
            trading_money=80_000_000,
            trading_volume=600_000,
            close=100,
        ))
        path = self.cache_dir / "finmind" / "TaiwanStockPrice" / "2026-05-28" / "8888.jsonl"
        write_jsonl(path, rows)

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
            min_trading_money=50_000_000,   # today's money passes
            min_adv_20d_money=50_000_000,
            min_volume_lots=0,
        )

        self.assertEqual(result.summary["filtered_low_adv"], 1)
        self.assertEqual(result.candidates, [])

    def test_low_volume_lots_is_filtered(self):
        """Stock with Trading_Volume = 400_000 shares (400 lots) is filtered (< 500 lots)."""
        rows = [
            self._make_price_row(
                "2026-05-27", "7777",
                trading_money=100_000_000, trading_volume=600_000, close=100,
            ),
            self._make_price_row(
                "2026-05-28", "7777",
                trading_money=100_000_000, trading_volume=400_000, close=100,
            ),
        ]
        path = self.cache_dir / "finmind" / "TaiwanStockPrice" / "2026-05-28" / "7777.jsonl"
        write_jsonl(path, rows)

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
            min_trading_money=50_000_000,
            min_adv_20d_money=0,
            min_volume_lots=500,
        )

        self.assertEqual(result.summary["filtered_low_volume_lots"], 1)
        self.assertEqual(result.candidates, [])

    def test_high_price_low_range_warns_in_summary(self):
        """Close > 500 with intraday_range_pct < 0.3 increments warned_high_price_spread."""
        # close=600, high=601.5, low=600 → range = 1.5, range_pct = 1.5/600*100 = 0.25 < 0.3
        rows = [
            self._make_price_row(
                "2026-05-27", "6666",
                trading_money=200_000_000, trading_volume=600_000,
                close=600, open_=600, high=601, low=600, spread=0,
            ),
            self._make_price_row(
                "2026-05-28", "6666",
                trading_money=200_000_000, trading_volume=600_000,
                close=600, open_=600, high=601, low=600, spread=0,
            ),
        ]
        path = self.cache_dir / "finmind" / "TaiwanStockPrice" / "2026-05-28" / "6666.jsonl"
        write_jsonl(path, rows)

        result = build_candidates_from_raw_cache(
            cache_dir=self.cache_dir,
            trading_date="2026-05-28",
            min_trading_money=50_000_000,
            min_adv_20d_money=0,
            min_volume_lots=0,
        )

        self.assertGreater(result.summary["warned_high_price_spread"], 0)
        self.assertGreater(len(result.candidates), 0)  # NOT filtered


if __name__ == "__main__":
    unittest.main()
