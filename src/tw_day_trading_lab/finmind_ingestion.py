from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .storage import FetchLedgerRepository


class FinMindIngestionClientError(RuntimeError):
    """Raised when the FinMind adapter cannot fetch a requested dataset."""


class FinMindClient(Protocol):
    """Port for FinMind data fetches; tests use a fake client."""

    def fetch_dataset(self, request: "FetchRequest") -> list[Mapping[str, Any]]:
        """Fetch one dataset request from FinMind and return row dictionaries."""


@dataclass(frozen=True)
class FetchRequest:
    """One FinMind dataset/date/stock request."""

    dataset: str
    trading_date: str
    stock_id: str
    source: str = "finmind"


def raw_cache_path(cache_dir: Path, request: FetchRequest) -> Path:
    """Return the JSONL raw cache path for a FinMind fetch request."""
    safe_stock_id = request.stock_id or "market"
    return cache_dir / request.source / request.dataset / request.trading_date / f"{safe_stock_id}.jsonl"


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write raw API rows as UTF-8 JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_request_file(path: Path, *, default_source: str = "finmind") -> list[FetchRequest]:
    """Load ingestion requests from a JSON list for CLI usage."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("FinMind request file must be a JSON list")
    return [
        FetchRequest(
            dataset=str(item["dataset"]),
            trading_date=str(item["trading_date"]),
            stock_id=str(item.get("stock_id") or "market"),
            source=str(item.get("source") or default_source),
        )
        for item in raw
    ]


def build_single_request(
    *,
    dataset: str,
    trading_date: str,
    stock_id: str,
    source: str = "finmind",
) -> FetchRequest:
    """Build a single CLI request without exposing dataclass details to argparse."""
    return FetchRequest(
        dataset=dataset,
        trading_date=trading_date,
        stock_id=stock_id or "market",
        source=source,
    )


def ingest_finmind_requests(
    *,
    repository: FetchLedgerRepository,
    cache_dir: Path,
    client: FinMindClient,
    requests: Sequence[FetchRequest],
    token: str | None,
    quota_limit: int = 540,
) -> dict[str, Any]:
    """Fetch FinMind data with ledger-backed API cache and quota-safe planning."""
    summary = {
        "source": "finmind",
        "status": "ok",
        "planned": 0,
        "actual": 0,
        "skipped": 0,
        "failed": 0,
        "refetched_missing_cache": 0,
        "errors": [],
    }
    requests_to_fetch: list[tuple[FetchRequest, bool]] = []
    for request in requests:
        record = repository.fetch_fetch_record(
            dataset=request.dataset,
            trading_date=request.trading_date,
            stock_id=request.stock_id,
            source=request.source,
        )
        cache_path = raw_cache_path(cache_dir, request)
        if record and record.get("status") == "success" and cache_path.exists():
            summary["skipped"] += 1
            continue
        missing_cache_refetch = bool(record and record.get("status") == "success")
        requests_to_fetch.append((request, missing_cache_refetch))

    summary["planned"] = len(requests_to_fetch)
    if summary["planned"] > quota_limit:
        summary["status"] = "quota_blocked"
        return summary

    if requests_to_fetch and not token:
        summary["status"] = "auth_missing"
        summary["failed"] = len(requests_to_fetch)
        summary["errors"].append("FinMind token is required for uncached requests")
        return summary

    for request, missing_cache_refetch in requests_to_fetch:
        try:
            rows = client.fetch_dataset(request)
            write_jsonl(raw_cache_path(cache_dir, request), rows)
            repository.save_fetch_record(
                dataset=request.dataset,
                trading_date=request.trading_date,
                stock_id=request.stock_id,
                source=request.source,
                status="success",
                request_count=1,
            )
            summary["actual"] += 1
            if missing_cache_refetch:
                summary["refetched_missing_cache"] += 1
        except Exception as exc:
            repository.save_fetch_record(
                dataset=request.dataset,
                trading_date=request.trading_date,
                stock_id=request.stock_id,
                source=request.source,
                status="failed",
                request_count=1,
                error_message=str(exc),
            )
            summary["failed"] += 1
            summary["errors"].append(
                {
                    "dataset": request.dataset,
                    "trading_date": request.trading_date,
                    "stock_id": request.stock_id,
                    "error": str(exc),
                }
            )
    if summary["failed"]:
        summary["status"] = "partial_failed" if summary["actual"] else "failed"
    return summary


class FinMindDataLoaderClient:
    """Thin FinMind SDK adapter kept at the composition boundary."""

    DATASET_METHODS = {
        "TaiwanStockPrice": "taiwan_stock_daily",
        "TaiwanStockInstitutionalInvestorsBuySell": "taiwan_stock_institutional_investors",
        "TaiwanStockMarginPurchaseShortSale": "taiwan_stock_margin_purchase_short_sale",
        "TaiwanStockInfo": "taiwan_stock_info",
    }

    def __init__(self, token: str) -> None:
        try:
            from FinMind.data import DataLoader
        except ModuleNotFoundError as exc:
            raise FinMindIngestionClientError("FinMind package is required") from exc
        self.loader = DataLoader()
        self.loader.login_by_token(api_token=token)

    def fetch_dataset(self, request: FetchRequest) -> list[Mapping[str, Any]]:
        """Fetch one supported FinMind dataset and normalize records to dictionaries."""
        method_name = self.DATASET_METHODS.get(request.dataset)
        if method_name is None:
            raise FinMindIngestionClientError(f"unsupported FinMind dataset: {request.dataset}")
        method = getattr(self.loader, method_name)
        kwargs: dict[str, Any] = {}
        if request.stock_id != "market" and request.dataset != "TaiwanStockInfo":
            kwargs["stock_id"] = request.stock_id
        if request.dataset != "TaiwanStockInfo":
            kwargs["start_date"] = request.trading_date
            kwargs["end_date"] = request.trading_date
        frame = method(**kwargs)
        if hasattr(frame, "to_dict"):
            return list(frame.to_dict("records"))
        return [dict(row) for row in frame]
