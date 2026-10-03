import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.sector_flow import (
    build_large_holder_proxy,
    build_sector_flow_report,
    load_taxonomy,
)
from tw_day_trading_lab.sector_flow_sources import HoldingDistributionRow


FIXTURES = Path(__file__).parents[1] / "fixtures" / "sector-flow"


class SectorFlowAggregationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)
        self._copy("twse-t86.json", "twse/T86/2026-09-24/market.json")
        self._copy("twse-mi-index.json", "twse/MI_INDEX/2026-09-24/market.json")
        self._copy("tpex-institutional.json", "tpex/institutional/2026-09-24/market.json")
        self._copy("tpex-daily-close.json", "tpex/daily_close/2026-09-24/market.json")
        self._copy("tdcc-holding-distribution.json", "tdcc/holding_distribution/2026-09-24/market.json")
        self._write_taxonomy("2026-09-23", "舊半導體")
        self._write_taxonomy("2026-09-30", "半導體業")
        self._write_taxonomy("2026-10-02", "未來分類")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _copy(self, fixture, relative):
        target = self.cache_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / fixture, target)

    def _write_taxonomy(self, snapshot_date, category):
        path = self.cache_dir / "finmind" / "TaiwanStockInfo" / snapshot_date / "market.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"date": "1962-02-09", "stock_id": "2330", "stock_name": "台積電", "industry_category": category, "type": "twse"},
            {"date": "2015-09-25", "stock_id": "6488", "stock_name": "環球晶", "industry_category": category, "type": "tpex"},
        ]
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def test_taxonomy_uses_latest_snapshot_not_after_end_date(self):
        taxonomy = load_taxonomy(self.cache_dir, end_date="2026-10-01")
        self.assertEqual(taxonomy["2330"].categories, ("半導體業",))
        self.assertEqual(taxonomy["2330"].snapshot_date, "2026-09-30")

    def test_daily_category_sums_exact_shares_and_estimated_amounts(self):
        payload = build_sector_flow_report(cache_dir=self.cache_dir, start_date="2026-09-24", end_date="2026-10-01")
        category = payload["daily"][0]["categories"][0]
        self.assertEqual(category["institutional_net_shares"], 2_225_000)
        self.assertEqual(category["estimated_institutional_net_amount_twd"], 1_463_750_000.0)
        self.assertEqual(category["amount_method"], "net_shares_times_close")
        self.assertEqual(payload["observed_trading_dates"], ["2026-09-24"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source_status"]["2026-09-24"]["twse_institutional"]["embedded_date"], "2026-09-24")

    def test_top_contributors_keep_market_and_symbol(self):
        payload = build_sector_flow_report(cache_dir=self.cache_dir, start_date="2026-09-24", end_date="2026-10-01")
        positive = payload["daily"][0]["categories"][0]["top_positive_contributors"]
        self.assertEqual(positive[0]["symbol"], "2330")
        self.assertEqual({row["market"] for row in positive}, {"twse", "tpex"})

    def test_estimated_amounts_are_rounded_to_two_decimal_places(self):
        close_path = self.cache_dir / "tpex/daily_close/2026-09-24/market.json"
        close_payload = json.loads(close_path.read_text(encoding="utf-8"))
        close_payload["tables"][0]["data"][0][2] = "0.1"
        close_path.write_text(json.dumps(close_payload, ensure_ascii=False), encoding="utf-8")
        flow_path = self.cache_dir / "tpex/institutional/2026-09-24/market.json"
        flow_payload = json.loads(flow_path.read_text(encoding="utf-8"))
        row = flow_payload["tables"][0]["data"][0]
        for index, value in {4: "3", 7: "0", 10: "3", 13: "0", 16: "0", 19: "0", 22: "0", 23: "3"}.items():
            row[index] = value
        flow_path.write_text(json.dumps(flow_payload, ensure_ascii=False), encoding="utf-8")
        taxonomy_path = self.cache_dir / "finmind/TaiwanStockInfo/2026-09-23/market.jsonl"
        taxonomy_rows = [json.loads(line) for line in taxonomy_path.read_text(encoding="utf-8").splitlines()]
        taxonomy_rows[1]["industry_category"] = "測試族群"
        taxonomy_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in taxonomy_rows) + "\n", encoding="utf-8")

        report = build_sector_flow_report(cache_dir=self.cache_dir, start_date="2026-09-24", end_date="2026-09-24")
        amount = next(row for row in report["period_summary"] if row["category"] == "測試族群")["estimated_institutional_net_amount_twd"]

        self.assertEqual(amount, 0.3)

    def test_missing_close_keeps_shares_and_degrades_amount_ranking(self):
        (self.cache_dir / "tpex/daily_close/2026-09-24/market.json").unlink()
        payload = build_sector_flow_report(cache_dir=self.cache_dir, start_date="2026-09-24", end_date="2026-09-24")
        category = payload["daily"][0]["categories"][0]
        self.assertEqual(category["institutional_net_shares"], 2_225_000)
        self.assertEqual(category["missing_price_count"], 1)
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["ranking_method"], "net_shares")

    def test_report_does_not_depend_on_how_cache_dir_is_spelled(self):
        absolute = build_sector_flow_report(cache_dir=self.cache_dir.resolve(), start_date="2026-09-24", end_date="2026-09-24")
        relative = build_sector_flow_report(
            cache_dir=Path(os.path.relpath(self.cache_dir)), start_date="2026-09-24", end_date="2026-09-24"
        )
        self.assertEqual(json.dumps(absolute, sort_keys=True), json.dumps(relative, sort_keys=True))
        self.assertEqual(
            absolute["source_status"]["2026-09-24"]["twse_institutional"]["cache_path"],
            "twse/T86/2026-09-24/market.json",
        )

    def test_no_valid_institutional_rows_marks_report_blocked(self):
        empty = Path(self.tmpdir.name) / "empty"
        payload = build_sector_flow_report(cache_dir=empty, start_date="2026-09-24", end_date="2026-09-24")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["daily"], [])

    # ---- helpers for multi-category / degrade tests ----
    def _write_taxonomy_rows(self, pairs):
        types = {"2330": "twse", "6488": "tpex"}
        rows = [{"date": "2000-01-01", "stock_id": sid, "stock_name": sid, "industry_category": cat, "type": types[sid]} for sid, cat in pairs]
        for snapshot in ("2026-09-23", "2026-09-30"):
            path = self.cache_dir / "finmind" / "TaiwanStockInfo" / snapshot / "market.jsonl"
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def _report(self, start="2026-09-24", end="2026-09-24"):
        return build_sector_flow_report(cache_dir=self.cache_dir, start_date=start, end_date=end)

    def _copy_day(self, trading_date):
        compact = trading_date.replace("-", "")
        for fixture, relative in (
            ("twse-t86.json", "twse/T86"), ("twse-mi-index.json", "twse/MI_INDEX"),
            ("tpex-institutional.json", "tpex/institutional"), ("tpex-daily-close.json", "tpex/daily_close"),
        ):
            target = self.cache_dir / relative / trading_date / "market.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((FIXTURES / fixture).read_text(encoding="utf-8").replace("20260924", compact).replace("115/09/24", f"115/09/{trading_date[-2:]}"), encoding="utf-8")

    def _rows(self, report):
        return {row["category"]: row for row in report["period_summary"]}

    def test_multi_category_symbol_counts_in_every_category_in_either_row_order(self):
        results = []
        for pairs in (
            [("2330", "半導體業"), ("2330", "電子工業"), ("6488", "半導體業")],
            [("6488", "半導體業"), ("2330", "電子工業"), ("2330", "半導體業")],
        ):
            self._write_taxonomy_rows(pairs)
            report = self._report()
            results.append(json.dumps(report, sort_keys=True, ensure_ascii=False))
            rows = self._rows(report)
            self.assertEqual(rows["半導體業"]["institutional_net_shares"], 2_225_000)
            self.assertEqual(rows["電子工業"]["institutional_net_shares"], 1_250_000)
            self.assertTrue(rows["電子工業"]["is_broad"])
            self.assertFalse(rows["半導體業"]["is_broad"])
            self.assertTrue(report["category_overlap"])
            self.assertIn("category_overlap_note", report)
        self.assertEqual(results[0], results[1])

    def test_tpex_and_twse_synonym_names_merge_into_one_category(self):
        self._write_taxonomy_rows([("2330", "其他電子業"), ("6488", "其他電子類")])
        report = self._report()
        self.assertEqual([row["category"] for row in report["period_summary"]], ["其他電子業"])
        self.assertEqual(report["period_summary"][0]["institutional_net_shares"], 2_225_000)

    def test_board_labels_and_catch_all_other_are_dropped(self):
        self._write_taxonomy_rows([("2330", "創新板股票"), ("2330", "其他"), ("2330", "半導體業"), ("6488", "其他"), ("6488", "創新版股票")])
        taxonomy = load_taxonomy(self.cache_dir, end_date="2026-10-01")
        self.assertEqual(taxonomy["2330"].categories, ("半導體業",))
        self.assertEqual(taxonomy["6488"].categories, ("其他",))
        self._write_taxonomy_rows([("2330", "半導體業"), ("6488", "創新板股票")])
        report = self._report()
        self.assertEqual(self._rows(report)["未分類"]["institutional_net_shares"], 975_000)
        self.assertLess(report["taxonomy"]["coverage"], 1.0)
        self.assertEqual(report["status"], "degraded")

    def test_trading_day_with_missing_institutional_files_degrades_report(self):
        (self.cache_dir / "twse/T86/2026-09-24/market.json").unlink()
        (self.cache_dir / "tpex/institutional/2026-09-24/market.json").unlink()
        self._copy_day("2026-09-25")
        report = self._report(end="2026-09-25")
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any("incomplete sources on 2026-09-24" in w and "twse_institutional=missing" in w for w in report["warnings"]), report["warnings"])

    def test_day_with_no_source_is_listed_not_degraded(self):
        self._copy_day("2026-09-25")
        report = self._report(end="2026-09-26")
        self.assertEqual(report["dates_without_data"], ["2026-09-26"])
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["warnings"], [])

    def test_day_where_every_source_is_corrupt_degrades_not_no_data(self):
        self._copy_day("2026-09-25")
        for relative in ("twse/T86", "twse/MI_INDEX", "tpex/institutional", "tpex/daily_close"):
            (self.cache_dir / relative / "2026-09-25" / "market.json").write_text("{}", encoding="utf-8")
        report = self._report(end="2026-09-25")
        self.assertEqual(report["dates_without_data"], [])
        self.assertEqual(report["status"], "degraded")
        self.assertTrue(any("incomplete sources on 2026-09-25" in w and "schema_error" in w for w in report["warnings"]), report["warnings"])

    def test_amount_mode_contributors_follow_estimated_amount(self):
        close_path = self.cache_dir / "tpex/daily_close/2026-09-24/market.json"
        payload = json.loads(close_path.read_text(encoding="utf-8"))
        payload["tables"][0]["data"][0][2] = "5000"
        close_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        report = self._report()
        self.assertEqual(report["ranking_method"], "estimated_amount")
        for rows in (report["daily"][0]["categories"], report["period_summary"]):
            self.assertEqual([c["symbol"] for c in rows[0]["top_positive_contributors"]], ["6488", "2330"])

    def test_period_counts_are_distinct_symbols_not_stock_days(self):
        self._copy_day("2026-09-25")
        report = self._report(end="2026-09-30")
        row = self._rows(report)["半導體業"]
        self.assertEqual(row["covered_symbol_count"], 2)
        self.assertEqual(row["missing_price_count"], 0)
        self.assertEqual(report["daily"][0]["categories"][0]["covered_symbol_count"], 2)
        self.assertEqual(len(report["daily"]), 2)
        for day in ("2026-09-24", "2026-09-25"):
            (self.cache_dir / "tpex/daily_close" / day / "market.json").unlink()
        row = self._rows(self._report(end="2026-09-30"))["半導體業"]
        self.assertEqual(row["missing_price_count"], 1)
        self.assertEqual(row["covered_symbol_count"], 1)


class SectorFlowLargeHolderTest(unittest.TestCase):
    def test_two_snapshots_use_levels_12_through_15_only(self):
        holdings = {
            "2026-09-18": [
                HoldingDistributionRow("2026-09-18", "2330", 12, 10, 100_000, 1.0),
                HoldingDistributionRow("2026-09-18", "2330", 17, 100, 1_000_000, 100.0),
            ],
            "2026-09-24": [
                HoldingDistributionRow("2026-09-24", "2330", 12, 11, 150_000, 1.5),
                HoldingDistributionRow("2026-09-24", "2330", 17, 100, 1_100_000, 100.0),
            ],
        }
        taxonomy = {"2330": type("Entry", (), {"categories": ("半導體業",), "name": "台積電"})()}
        result = build_large_holder_proxy(holdings_by_date=holdings, taxonomy=taxonomy, closes={"2330": 820.0}, end_date="2026-10-01")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "holding_change_proxy")
        self.assertEqual(result["categories"][0]["large_holder_share_delta"], 50_000)
        self.assertEqual(result["categories"][0]["sum_stock_percent_point_delta"], 0.5)
        self.assertNotIn("large_holder_percent_delta", result["categories"][0])
        self.assertEqual(result["categories"][0]["estimated_change_twd"], 41_000_000.0)

    def test_one_snapshot_is_explicitly_insufficient(self):
        result = build_large_holder_proxy(holdings_by_date={"2026-09-24": []}, taxonomy={}, closes={}, end_date="2026-10-01")
        self.assertEqual(result, {"status": "insufficient_data", "reason": "fewer_than_two_eligible_snapshots", "latest_as_of_date": "2026-09-24"})

    def test_snapshot_after_requested_end_date_is_not_used(self):
        result = build_large_holder_proxy(holdings_by_date={"2026-10-02": []}, taxonomy={}, closes={}, end_date="2026-10-01")
        self.assertEqual(result["latest_as_of_date"], None)


if __name__ == "__main__":
    unittest.main()
