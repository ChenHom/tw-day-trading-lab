import csv
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.old_log_importer import (
    TradeLogEvent,
    import_trade_log_csv,
    normalize_lifecycles,
    render_failure_replay_markdown,
    summarize_samples,
)


class OldLogImporterTest(unittest.TestCase):
    def test_complete_lifecycle_becomes_valid_sample(self):
        events = [
            TradeLogEvent.from_row(
                {
                    "id": "enter-1",
                    "position_id": "pos-1",
                    "timestamp": "2026-03-25T09:10:00",
                    "symbol": "2330",
                    "action": "ENTER",
                    "price": "600",
                    "reason": "進場訊號觸發",
                    "entry_price": "600",
                    "stop_loss": "595",
                    "position_size": "1000",
                    "context": '{"source_tag": "manual_watchlist"}',
                },
                source_file="fixture.csv",
                row_number=2,
            ),
            TradeLogEvent.from_row(
                {
                    "id": "exit-1",
                    "position_id": "pos-1",
                    "timestamp": "2026-03-25T09:20:00",
                    "symbol": "2330",
                    "action": "EXIT_STOP_LOSS",
                    "price": "594",
                    "reason": "觸發出場條件: EXIT_STOP_LOSS",
                    "entry_price": "600",
                    "stop_loss": "595",
                    "position_size": "1000",
                    "realized_r": "-1.2",
                    "context": '{"realized_r": -1.2}',
                },
                source_file="fixture.csv",
                row_number=3,
            ),
        ]

        samples = normalize_lifecycles(events)

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].validity, "valid")
        self.assertEqual(samples[0].symbol, "2330")
        self.assertEqual(samples[0].realized_r_gross, -1.2)
        self.assertEqual(samples[0].candidate_source, "manual_watchlist")

    def test_duplicate_enter_same_symbol_timestamp_is_excluded(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))
        duplicate_samples = [
            item for item in result.samples
            if item.exclusion_reason == "duplicate_enter_same_symbol_timestamp"
        ]

        self.assertEqual(len(duplicate_samples), 2)
        self.assertTrue(all(item.validity == "excluded" for item in duplicate_samples))

    def test_missing_exit_becomes_needs_review(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))
        sample = next(item for item in result.samples if item.position_id == "pos-missing")

        self.assertEqual(sample.validity, "needs_review")
        self.assertEqual(sample.exclusion_reason, "missing_exit")

    def test_after_hours_debug_sample_is_excluded(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))
        sample = next(item for item in result.samples if item.position_id == "pos-debug")

        self.assertEqual(sample.validity, "excluded")
        self.assertEqual(sample.exclusion_reason, "debug_or_forced_order")

    def test_malformed_row_becomes_needs_review_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "malformed.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["id", "position_id", "timestamp", "symbol", "action", "price"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "id": "broken",
                        "position_id": "pos-broken",
                        "timestamp": "not-a-date",
                        "symbol": "2330",
                        "action": "ENTER",
                        "price": "600",
                    }
                )

            result = import_trade_log_csv(csv_path)

        self.assertEqual(result.samples[0].validity, "needs_review")
        self.assertEqual(result.samples[0].exclusion_reason, "malformed_timestamp")

    def test_summary_and_failure_report_include_classification_counts(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))
        summary = summarize_samples(result.samples)
        report = render_failure_replay_markdown("2026-03-25", result)

        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["excluded"], 3)
        self.assertEqual(summary["needs_review"], 1)
        self.assertIn("duplicate_enter_same_symbol_timestamp", report)
        self.assertIn("valid / excluded / needs_review", report)


if __name__ == "__main__":
    unittest.main()
