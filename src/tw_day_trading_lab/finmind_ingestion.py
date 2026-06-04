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
    start_date: str | None = None
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
            start_date=str(item["start_date"]) if item.get("start_date") else None,
            source=str(item.get("source") or default_source),
        )
        for item in raw
    ]


def build_single_request(
    *,
    dataset: str,
    trading_date: str,
    stock_id: str,
    start_date: str | None = None,
    source: str = "finmind",
) -> FetchRequest:
    """Build a single CLI request without exposing dataclass details to argparse."""
    return FetchRequest(
        dataset=dataset,
        trading_date=trading_date,
        stock_id=stock_id or "market",
        start_date=start_date,
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
        "refetched_incomplete_window": 0,
        "errors": [],
    }
    requests_to_fetch: list[tuple[FetchRequest, str | None]] = []
    for request in requests:
        record = repository.fetch_fetch_record(
            dataset=request.dataset,
            trading_date=request.trading_date,
            stock_id=request.stock_id,
            source=request.source,
        )
        cache_path = raw_cache_path(cache_dir, request)
        cache_exists = cache_path.exists()
        cache_complete = cache_satisfies_request(cache_path, request) if cache_exists else False
        if record and record.get("status") == "success" and cache_complete:
            summary["skipped"] += 1
            continue
        refetch_reason = None
        if record and record.get("status") == "success":
            refetch_reason = "missing_cache" if not cache_exists else "incomplete_window"
        requests_to_fetch.append((request, refetch_reason))

    summary["planned"] = len(requests_to_fetch)
    if summary["planned"] > quota_limit:
        summary["status"] = "quota_blocked"
        return summary

    if requests_to_fetch and not token:
        summary["status"] = "auth_missing"
        summary["failed"] = len(requests_to_fetch)
        summary["errors"].append("FinMind token is required for uncached requests")
        return summary

    for request, refetch_reason in requests_to_fetch:
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
            if refetch_reason == "missing_cache":
                summary["refetched_missing_cache"] += 1
            if refetch_reason == "incomplete_window":
                summary["refetched_incomplete_window"] += 1
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


def cache_satisfies_request(path: Path, request: FetchRequest) -> bool:
    """Return whether an existing raw cache file covers the requested date window."""
    if not path.exists():
        return False
    if not request.start_date:
        return True
    dates: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("date"):
                dates.append(str(row["date"]))
    except (OSError, json.JSONDecodeError):
        return False
    if not dates:
        return False
    return min(dates) <= request.start_date and max(dates) >= request.trading_date


class FinMindDataLoaderClient:
    """Thin FinMind SDK adapter kept at the composition boundary."""

    DATASET_METHODS = {
        "TaiwanStockPrice": "taiwan_stock_daily",
        "TaiwanStockPriceMinute": "taiwan_stock_kbar",
        "TaiwanStockKBar": "taiwan_stock_kbar",
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
        if method_name == "taiwan_stock_kbar":
            frame = method(
                stock_id=request.stock_id if request.stock_id != "market" else "",
                date=request.trading_date,
            )
            if hasattr(frame, "to_dict"):
                return list(frame.to_dict("records"))
            return [dict(row) for row in frame]
        kwargs: dict[str, Any] = {}
        if request.stock_id != "market" and request.dataset != "TaiwanStockInfo":
            kwargs["stock_id"] = request.stock_id
        if request.dataset != "TaiwanStockInfo":
            kwargs["start_date"] = request.start_date or request.trading_date
            kwargs["end_date"] = request.trading_date
        frame = method(**kwargs)
        if hasattr(frame, "to_dict"):
            return list(frame.to_dict("records"))
        return [dict(row) for row in frame]
