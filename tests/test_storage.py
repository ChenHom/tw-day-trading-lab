import sqlite3
import unittest
from pathlib import Path

from tw_day_trading_lab.candidate_engine import rank_candidates
from tw_day_trading_lab.models import CandidateInput
from tw_day_trading_lab.old_log_importer import import_trade_log_csv
from tw_day_trading_lab.storage import (
    DatabaseStorage,
    StorageError,
    create_sqlite_schema,
    persist_candidate_run,
    persist_import_samples,
    render_schema_script,
)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.storage = DatabaseStorage(self.connection, dialect="sqlite")
        create_sqlite_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_import_samples_round_trip_preserves_all_validity_classes(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))

        summary = persist_import_samples(self.storage, result.to_dict())

        self.assertEqual(summary["valid"], 1)
        self.assertEqual(summary["excluded"], 3)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(summary["reasons"]["duplicate_enter_same_symbol_timestamp"], 2)

    def test_duplicate_idempotency_samples_are_stored_as_evidence(self):
        result = import_trade_log_csv(Path("examples/old-log.sample.csv"))

        persist_import_samples(self.storage, result.to_dict())
        cursor = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM valid_samples
            WHERE exclusion_reason = 'duplicate_enter_same_symbol_timestamp'
            """
        )

        self.assertEqual(cursor.fetchone()[0], 2)

    def test_candidate_run_and_items_round_trip(self):
        candidates = rank_candidates(
            [
                CandidateInput.from_dict(
                    {
                        "symbol": "2330",
                        "name": "台積電",
                        "trading_money": 500_000_000,
                        "change_pct": 2.1,
                        "intraday_range_pct": 2.5,
                        "volume_expansion": 1.8,
                        "theme_strength": 0.8,
                        "structure_quality": 0.7,
                        "crowding_risk": 0.3,
                    }
                )
            ]
        )

        summary = persist_candidate_run(
            self.storage,
            run_id="run-2026-05-28",
            trading_date="2026-05-28",
            source="unit-test",
            status="generated",
            candidates=candidates,
        )

        self.assertEqual(summary["run_id"], "run-2026-05-28")
        self.assertEqual(summary["item_count"], 1)
        self.assertEqual(summary["actionable_count"], 1)

    def test_storage_error_is_not_swallowed(self):
        self.connection.close()

        with self.assertRaises(StorageError) as caught:
            self.storage.fetch_sample_summary()

        self.assertIn("fetch_sample_summary", str(caught.exception))

    def test_schema_rendering_rejects_unsafe_database_identifier(self):
        with self.assertRaises(ValueError):
            render_schema_script("USE tw_day_trading_lab;", "bad-name;DROP")


if __name__ == "__main__":
    unittest.main()
