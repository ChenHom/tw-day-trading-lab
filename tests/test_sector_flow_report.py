import unittest

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

    def test_same_payload_renders_identically(self):
        payload = sample_payload()
        self.assertEqual(render_sector_flow_markdown(payload), render_sector_flow_markdown(payload))


if __name__ == "__main__":
    unittest.main()
