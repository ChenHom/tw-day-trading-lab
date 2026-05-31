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
    summary: dict[str, Any] = {
        "source": "finmind_raw_cache",
        "trading_date": trading_date,
        "input_files": 0,
        "built_candidates": 0,
        "filtered_low_liquidity": 0,
        "degraded_candidates": 0,
        "data_gap_files": 0,
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
    )


def _previous_close(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> float:
    if previous is not None and previous.get("close") not in {None, ""}:
        return float(previous["close"])
    spread = float(current.get("spread", 0) or 0)
    close = float(current["close"])
    return close - spread if close - spread > 0 else close
