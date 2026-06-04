import sqlite3
import tempfile
import unittest
from pathlib import Path

from tw_day_trading_lab.finmind_ingestion import (
    FetchRequest,
    FinMindDataLoaderClient,
    FinMindIngestionClientError,
    ingest_finmind_requests,
    raw_cache_path,
)
from tw_day_trading_lab.storage import DatabaseStorage, create_sqlite_schema


class FakeFinMindClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else [{"date": "2026-05-28", "stock_id": "2330"}]
        self.error = error
        self.calls = []

    def fetch_dataset(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return list(self.rows)


class FakeFinMindLoader:
    def __init__(self):
        self.kbar_calls = []

    def taiwan_stock_kbar(self, **kwargs):
        self.kbar_calls.append(kwargs)
        return [
            {
                "date": kwargs["date"],
                "minute": "09:01",
                "stock_id": kwargs["stock_id"],
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 10,
            }
        ]


class FinMindIngestionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.storage = DatabaseStorage(self.connection, dialect="sqlite")
        create_sqlite_schema(self.connection)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()
        self.connection.close()

    def test_no_token_reports_auth_gap_without_crashing(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
        )
        client = FakeFinMindClient()

        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=[request],
            token=None,
        )

        self.assertEqual(summary["planned"], 1)
        self.assertEqual(summary["actual"], 0)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["status"], "auth_missing")
        self.assertEqual(client.calls, [])

    def test_second_run_skips_api_when_ledger_success_and_raw_cache_exists(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
        )
        first_client = FakeFinMindClient()

        first_summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=first_client,
            requests=[request],
            token="token",
        )
        second_client = FakeFinMindClient()
        second_summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=second_client,
            requests=[request],
            token="token",
        )

        self.assertEqual(first_summary["actual"], 1)
        self.assertEqual(second_summary["planned"], 0)
        self.assertEqual(second_summary["actual"], 0)
        self.assertEqual(second_summary["skipped"], 1)
        self.assertEqual(second_client.calls, [])

    def test_success_ledger_with_missing_raw_cache_refetches(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
        )
        cache_path = raw_cache_path(self.cache_dir, request)
        self.storage.save_fetch_record(
            dataset=request.dataset,
            trading_date=request.trading_date,
            stock_id=request.stock_id,
            source=request.source,
            status="success",
            request_count=1,
        )

        client = FakeFinMindClient()
        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=[request],
            token="token",
        )

        self.assertEqual(summary["planned"], 1)
        self.assertEqual(summary["actual"], 1)
        self.assertEqual(summary["refetched_missing_cache"], 1)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(cache_path.exists())

    def test_planned_calls_above_limit_are_blocked_before_api_calls(self):
        requests = [
            FetchRequest(
                dataset="TaiwanStockPrice",
                trading_date="2026-05-28",
                stock_id=str(2300 + index),
            )
            for index in range(3)
        ]
        client = FakeFinMindClient()

        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=requests,
            token="token",
            quota_limit=2,
        )

        self.assertEqual(summary["status"], "quota_blocked")
        self.assertEqual(summary["planned"], 3)
        self.assertEqual(summary["actual"], 0)
        self.assertEqual(client.calls, [])

    def test_client_error_is_recorded_as_failed_ledger_row(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
        )
        client = FakeFinMindClient(error=FinMindIngestionClientError("quota exceeded"))

        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=[request],
            token="token",
        )
        record = self.storage.fetch_fetch_record(
            dataset=request.dataset,
            trading_date=request.trading_date,
            stock_id=request.stock_id,
            source=request.source,
        )

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(record["status"], "failed")
        self.assertIn("quota exceeded", record["error_message"])

    def test_window_request_keeps_start_date_for_client_fetch(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
            start_date="2026-04-08",
        )
        client = FakeFinMindClient()

        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=[request],
            token="token",
        )

        self.assertEqual(summary["actual"], 1)
        self.assertEqual(client.calls[0].start_date, "2026-04-08")

    def test_window_request_refetches_when_existing_cache_is_too_short(self):
        request = FetchRequest(
            dataset="TaiwanStockPrice",
            trading_date="2026-05-28",
            stock_id="2330",
            start_date="2026-04-08",
        )
        cache_path = raw_cache_path(self.cache_dir, request)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"date":"2026-05-28","stock_id":"2330"}\n', encoding="utf-8")
        self.storage.save_fetch_record(
            dataset=request.dataset,
            trading_date=request.trading_date,
            stock_id=request.stock_id,
            source=request.source,
            status="success",
            request_count=1,
        )
        client = FakeFinMindClient(rows=[{"date": "2026-04-08", "stock_id": "2330"}])

        summary = ingest_finmind_requests(
            repository=self.storage,
            cache_dir=self.cache_dir,
            client=client,
            requests=[request],
            token="token",
        )

        self.assertEqual(summary["actual"], 1)
        self.assertEqual(summary["refetched_incomplete_window"], 1)
        self.assertEqual(len(client.calls), 1)

    def test_price_minute_dataset_uses_kbar_loader(self):
        request = FetchRequest(
            dataset="TaiwanStockPriceMinute",
            trading_date="2026-06-03",
            stock_id="2330",
        )
        client = FinMindDataLoaderClient.__new__(FinMindDataLoaderClient)
        loader = FakeFinMindLoader()
        client.loader = loader

        rows = client.fetch_dataset(request)

        self.assertEqual(loader.kbar_calls, [{"stock_id": "2330", "date": "2026-06-03"}])
        self.assertEqual(rows[0]["minute"], "09:01")


if __name__ == "__main__":
    unittest.main()
