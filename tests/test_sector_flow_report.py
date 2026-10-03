import argparse
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tw_day_trading_lab.cli import build_parser, cmd_ingest_sector_flow, cmd_report_sector_flow
from tw_day_trading_lab.sector_flow_report import render_sector_flow_markdown


def sample_payload():
    category = {
        "category": "半導體業",
        "institutional_net_shares": 2_225_000,
        "foreign_net_shares": 1_900_000,
        "investment_trust_net_shares": 300_000,
        "dealer_net_shares": 25_000,
        "estimated_institutional_net_amount_twd": 1_463_750_000.0,
        "estimated_foreign_net_amount_twd": 1_225_000_000.0,
        "estimated_investment_trust_net_amount_twd": 225_000_000.0,
        "estimated_dealer_net_amount_twd": 13_750_000.0,
        "top_positive_contributors": [{"market": "twse", "symbol": "2330", "name": "台積電", "institutional_net_shares": 1_250_000, "estimated_net_amount_twd": 1_025_000_000.0}],
        "top_negative_contributors": [],
    }
    return {
        "requested_period": {"start_date": "2026-09-24", "end_date": "2026-10-01"},
        "observed_trading_dates": ["2026-09-24"],
        "status": "ok",
        "ranking_method": "estimated_amount",
        "price_coverage": 1.0,
        "taxonomy": {"snapshot_date": "2026-09-30", "coverage": 1.0},
        "period_summary": [category],
        "daily": [{"trading_date": "2026-09-24", "price_coverage": 1.0, "categories": [category]}],
        "large_holder": {"status": "insufficient_data", "reason": "fewer_than_two_eligible_snapshots", "latest_as_of_date": "2026-09-24"},
        "warnings": [],
        "exclusions": {"symbol_rule": "^[1-9][0-9]{3}$"},
    }


class SectorFlowReportTest(unittest.TestCase):
    def test_parser_accepts_sector_flow_commands(self):
        ingest = build_parser().parse_args(["ingest", "sector-flow", "--start-date", "2026-09-24", "--end-date", "2026-10-01"])
        report = build_parser().parse_args([
            "report", "sector-flow", "--start-date", "2026-09-24", "--end-date", "2026-10-01",
            "--output", "out.json", "--report-output", "out.md",
        ])
        self.assertEqual(ingest.func.__name__, "cmd_ingest_sector_flow")
        self.assertEqual(report.func.__name__, "cmd_report_sector_flow")

    def test_report_command_writes_outputs_without_close_report_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                start_date="2026-09-24",
                end_date="2026-09-24",
                cache_dir=str(root / "cache"),
                output=str(root / "report.json"),
                report_output=str(root / "report.md"),
            )

            with self.assertRaises(SystemExit) as raised:
                cmd_report_sector_flow(args)

            self.assertEqual(raised.exception.code, 1)  # empty cache is blocked
            self.assertTrue(Path(args.output).exists())
            self.assertTrue(Path(args.report_output).exists())

    def test_degraded_report_exits_zero_and_ingest_failure_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(start_date="2026-09-24", end_date="2026-09-24", cache_dir=str(root), output=str(root / "r.json"), report_output=str(root / "r.md"))
            with patch("tw_day_trading_lab.cli.build_sector_flow_report", return_value={**sample_payload(), "status": "degraded"}):
                cmd_report_sector_flow(args)
            ingest_args = argparse.Namespace(start_date="2026-09-24", end_date="2026-09-24", cache_dir=str(root))
            with patch("tw_day_trading_lab.cli.ingest_sector_flow", return_value={"failed": 0}):
                cmd_ingest_sector_flow(ingest_args)
            with patch("tw_day_trading_lab.cli.ingest_sector_flow", return_value={"failed": 1}):
                with self.assertRaises(SystemExit) as raised:
                    cmd_ingest_sector_flow(ingest_args)
            self.assertEqual(raised.exception.code, 1)

    def test_amount_ranking_explains_opposite_share_and_amount_signs_only_in_amount_mode(self):
        note = "排名依估算金額"
        self.assertIn(note, render_sector_flow_markdown(sample_payload()))
        payload = sample_payload()
        payload["ranking_method"] = "net_shares"
        self.assertNotIn(note, render_sector_flow_markdown(payload))

    def test_daily_section_lists_largest_outflows_by_ranking_metric(self):
        payload = sample_payload()
        template = payload["period_summary"][0]
        rows = [dict(template, category=f"族群{i:02d}", institutional_net_shares=-i, estimated_institutional_net_amount_twd=float(i if i % 2 else -i) * 1000) for i in range(1, 13)]
        payload["daily"][0]["categories"] = sorted(rows, key=lambda row: row["estimated_institutional_net_amount_twd"], reverse=True)
        daily = render_sector_flow_markdown(payload).split("## 每日族群排行", 1)[1].split("## 主要個股貢獻", 1)[0]
        outflow = daily.split("最大流出", 1)[1]
        self.assertEqual(len(re.findall(r"族群\d\d", outflow)), 6)  # only the 6 negative-amount categories, not the net-share signs
        self.assertLess(outflow.index("族群12"), outflow.index("族群02"))

    def test_report_distinguishes_exact_shares_from_estimated_amount(self):
        markdown = render_sector_flow_markdown(sample_payload())
        self.assertIn("精確淨買賣股數", markdown)
        self.assertIn("估算金額（淨股數 × 收盤價）", markdown)
        self.assertNotIn("精確淨流入金額", markdown)

    def test_report_shows_insufficient_large_holder_data(self):
        markdown = render_sector_flow_markdown(sample_payload())
        self.assertIn("資料不足", markdown)
        self.assertIn("2026-09-24", markdown)
        self.assertIn("不可解讀為大戶淨流入", markdown)

    def test_report_contains_period_daily_and_contributor_sections(self):
        markdown = render_sector_flow_markdown(sample_payload())
        self.assertIn("## 區間法人流入排行", markdown)
        self.assertIn("## 區間法人流出排行", markdown)
        self.assertIn("## 每日族群排行", markdown)
        self.assertIn("台積電", markdown)

    def test_report_states_overlap_marks_broad_and_lists_dates_without_data(self):
        payload = sample_payload()
        payload["period_summary"][0]["is_broad"] = True
        payload["dates_without_data"] = ["2026-09-26"]
        markdown = render_sector_flow_markdown(payload)
        self.assertIn("一檔股票可能同時計入多個族群（例如大類「電子工業」與細類「半導體業」），族群之間互有重疊，不可加總。", markdown)
        self.assertIn("半導體業（大類）", markdown)
        self.assertIn("無資料日期（休市或未抓取，無法區分）：2026-09-26", markdown)

    def test_degraded_report_never_prints_no_extra_warning(self):
        payload = sample_payload()
        payload["status"] = "degraded"
        self.assertNotIn("無額外警告", render_sector_flow_markdown(payload))

    def test_same_payload_renders_identically(self):
        payload = sample_payload()
        self.assertEqual(render_sector_flow_markdown(payload), render_sector_flow_markdown(payload))

    def test_estimated_amount_ranking_uses_amount_sign_for_inflow_and_outflow(self):
        payload = sample_payload()
        contradictory = dict(payload["period_summary"][0])
        contradictory.update({
            "category": "股數正但金額負",
            "institutional_net_shares": 9_000_000,
            "estimated_institutional_net_amount_twd": -900_000_000.0,
        })
        payload["period_summary"] = [payload["period_summary"][0], contradictory]

        markdown = render_sector_flow_markdown(payload)
        inflow = markdown.split("## 區間法人流入排行", 1)[1].split("## 區間法人流出排行", 1)[0]
        outflow = markdown.split("## 區間法人流出排行", 1)[1].split("## 區間外資流入排行", 1)[0]

        self.assertNotIn("股數正但金額負", inflow)
        self.assertIn("股數正但金額負", outflow)


if __name__ == "__main__":
    unittest.main()
