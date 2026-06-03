from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .candidate_engine import rank_candidates
from .models import CandidateInput, CandidateScore


REQUIRED_PRICE_COLUMNS = {
    "date",
    "stock_id",
    "Trading_Volume",
    "Trading_money",
    "open",
    "max",
    "min",
    "close",
}


@dataclass(frozen=True)
class CandidateBuildResult:
    """Candidate build output plus source quality summary."""

    trading_date: str
    candidates: list[CandidateInput]
    ranked: list[CandidateScore]
    summary: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON payload shape used by CLI and reports."""
        return {
            "trading_date": self.trading_date,
            "summary": self.summary,
            "candidates": [item.to_dict() for item in self.ranked],
        }


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write JSONL rows for fixtures or raw cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL rows and skip blank lines."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_candidates_from_raw_cache(
    *,
    cache_dir: Path,
    trading_date: str,
    limit: int = 80,
    min_trading_money: float = 80_000_000,
) -> CandidateBuildResult:
    """Build ranked candidates from FinMind price raw cache."""
    price_dir = cache_dir / "finmind" / "TaiwanStockPrice" / trading_date
    stock_info_by_id = load_stock_info(cache_dir, trading_date=trading_date)
    summary: dict[str, Any] = {
        "source": "finmind_raw_cache",
        "trading_date": trading_date,
        "input_files": 0,
        "built_candidates": 0,
        "filtered_low_liquidity": 0,
        "filtered_non_common_stock": 0,
        "degraded_candidates": 0,
        "data_gap_files": 0,
        "missing_stock_info": 0,
        "missing_chip_files": 0,
        "missing_margin_files": 0,
    }
    candidates: list[CandidateInput] = []
    for path in sorted(price_dir.glob("*.jsonl")) if price_dir.exists() else []:
        summary["input_files"] += 1
        rows = read_jsonl(path)
        try:
            candidate = candidate_from_price_rows(rows, trading_date=trading_date)
        except (KeyError, TypeError, ValueError):
            summary["data_gap_files"] += 1
            continue
        if candidate.trading_money < min_trading_money:
            summary["filtered_low_liquidity"] += 1
            continue
        stock_info = stock_info_by_id.get(candidate.symbol)
        if stock_info and is_non_common_stock(stock_info):
            summary["filtered_non_common_stock"] += 1
            continue
        if not stock_info:
            summary["missing_stock_info"] += 1
        chip_rows = load_raw_dataset_rows(
            cache_dir,
            dataset="TaiwanStockInstitutionalInvestorsBuySell",
            trading_date=trading_date,
            stock_id=candidate.symbol,
        )
        margin_rows = load_raw_dataset_rows(
            cache_dir,
            dataset="TaiwanStockMarginPurchaseShortSale",
            trading_date=trading_date,
            stock_id=candidate.symbol,
        )
        if not chip_rows:
            summary["missing_chip_files"] += 1
        if not margin_rows:
            summary["missing_margin_files"] += 1
        candidate = enrich_candidate(candidate, stock_info, chip_rows, margin_rows)
        candidates.append(candidate)
        if candidate.data_quality != "ok":
            summary["degraded_candidates"] += 1

    ranked = rank_candidates(candidates, limit=limit)
    summary["built_candidates"] = len(candidates)
    summary["ranked_candidates"] = len(ranked)
    return CandidateBuildResult(
        trading_date=trading_date,
        candidates=candidates,
        ranked=ranked,
        summary=summary,
    )


def load_raw_dataset_rows(
    cache_dir: Path,
    *,
    dataset: str,
    trading_date: str,
    stock_id: str,
) -> list[dict[str, Any]]:
    """Load optional dataset rows for one stock from raw cache."""
    path = cache_dir / "finmind" / dataset / trading_date / f"{stock_id}.jsonl"
    if not path.exists():
        return []
    return read_jsonl(path)


def load_stock_info(cache_dir: Path, *, trading_date: str) -> dict[str, dict[str, Any]]:
    """Load stock info rows from the market-level raw cache."""
    info_dir = cache_dir / "finmind" / "TaiwanStockInfo" / trading_date
    rows: list[dict[str, Any]] = []
    if info_dir.exists():
        for path in sorted(info_dir.glob("*.jsonl")):
            rows.extend(read_jsonl(path))
    return {
        str(row["stock_id"]): row
        for row in rows
        if row.get("stock_id") not in {None, ""}
    }


def is_non_common_stock(stock_info: Mapping[str, Any]) -> bool:
    """Return True for ETF, ETN, warrants, and other non-common instruments."""
    text = " ".join(
        str(stock_info.get(key) or "")
        for key in ("security_type", "type", "industry_category", "stock_name", "name")
    ).lower()
    blocked_terms = (
        "etf",
        "etn",
        "權證",
        "認購",
        "認售",
        "指數投資證券",
        "受益證券",
        "存託憑證",
    )
    return any(term.lower() in text for term in blocked_terms)


def enrich_candidate(
    candidate: CandidateInput,
    stock_info: Mapping[str, Any] | None,
    chip_rows: Sequence[Mapping[str, Any]],
    margin_rows: Sequence[Mapping[str, Any]],
) -> CandidateInput:
    """Apply stock info and chip/margin hints while degrading missing data."""
    data_quality = candidate.data_quality
    if not chip_rows or not margin_rows:
        data_quality = "degraded"
    net_buy = sum(float(row.get("buy", 0) or 0) - float(row.get("sell", 0) or 0) for row in chip_rows)
    margin_delta = sum(_margin_delta(row) for row in margin_rows)
    theme_strength = clamp(candidate.theme_strength + clamp(net_buy / 5000, -0.2, 0.25))
    crowding_risk = clamp(candidate.crowding_risk + clamp(margin_delta / 10000, -0.15, 0.2))
    name = candidate.name
    if stock_info:
        name = str(
            stock_info.get("stock_name")
            or stock_info.get("name")
            or stock_info.get("stock_id")
            or candidate.name
        )
    return CandidateInput(
        symbol=candidate.symbol,
        name=name,
        trading_money=candidate.trading_money,
        change_pct=candidate.change_pct,
        intraday_range_pct=candidate.intraday_range_pct,
        volume_expansion=candidate.volume_expansion,
        theme_strength=round(theme_strength, 4),
        structure_quality=candidate.structure_quality,
        crowding_risk=round(crowding_risk, 4),
        data_quality=data_quality,
        atr_20d_pct=candidate.atr_20d_pct,
    )


def candidate_from_price_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    trading_date: str,
) -> CandidateInput:
    """Convert FinMind daily price rows into one candidate input."""
    if not rows:
        raise ValueError("empty price rows")
    sorted_rows = sorted(rows, key=lambda row: str(row.get("date", "")))
    target_rows = [row for row in sorted_rows if str(row.get("date")) == trading_date]
    current = target_rows[-1] if target_rows else sorted_rows[-1]
    missing = REQUIRED_PRICE_COLUMNS - set(current)
    if missing:
        raise KeyError(f"missing price columns: {sorted(missing)}")

    previous_rows = [row for row in sorted_rows if str(row.get("date")) < str(current["date"])]
    previous = previous_rows[-1] if previous_rows else None
    current_volume = float(current["Trading_Volume"])
    previous_volumes = [float(row.get("Trading_Volume", 0) or 0) for row in previous_rows[-5:]]
    avg_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0
    volume_expansion = current_volume / avg_volume if avg_volume > 0 else 1.0
    close = float(current["close"])
    high = float(current["max"])
    low = float(current["min"])
    previous_close = _previous_close(current, previous)
    change_pct = ((close - previous_close) / previous_close * 100) if previous_close > 0 else 0
    intraday_range_pct = ((high - low) / close * 100) if close > 0 else 0
    day_range = high - low
    close_position = ((close - low) / day_range) if day_range > 0 else 0.5
    data_quality = "ok" if previous is not None else "degraded"

    # Calculate 20-day ATR%
    atr_val = 0.0
    if len(sorted_rows) >= 2:
        true_ranges = []
        for i in range(max(1, len(sorted_rows) - 20), len(sorted_rows)):
            curr = sorted_rows[i]
            prev = sorted_rows[i - 1]
            c_high = float(curr.get("max", curr.get("high", 0)))
            c_low = float(curr.get("min", curr.get("low", 0)))
            p_close = float(prev.get("close", 0))
            tr = max(c_high - c_low, abs(c_high - p_close), abs(c_low - p_close))
            true_ranges.append(tr)
        if true_ranges:
            atr_val = sum(true_ranges) / len(true_ranges)

    atr_20d_pct = round((atr_val / close * 100), 4) if close > 0 else 0.0

    return CandidateInput(
        symbol=str(current["stock_id"]),
        name=str(current.get("name") or current["stock_id"]),
        trading_money=float(current["Trading_money"]),
        change_pct=round(change_pct, 4),
        intraday_range_pct=round(intraday_range_pct, 4),
        volume_expansion=round(volume_expansion, 4),
        theme_strength=0.5,
        structure_quality=round(close_position, 4),
        crowding_risk=round(min(abs(change_pct) / 10, 1), 4),
        data_quality=data_quality,
        atr_20d_pct=atr_20d_pct,
    )


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _margin_delta(row: Mapping[str, Any]) -> float:
    current = row.get("MarginPurchaseTodayBalance")
    previous = row.get("MarginPurchaseYesterdayBalance")
    if current in {None, ""} or previous in {None, ""}:
        return 0
    return float(current) - float(previous)


def _previous_close(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> float:
    if previous is not None and previous.get("close") not in {None, ""}:
        return float(previous["close"])
    spread = float(current.get("spread", 0) or 0)
    close = float(current["close"])
    return close - spread if close - spread > 0 else close
