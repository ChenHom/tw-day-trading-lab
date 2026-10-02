from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .sector_flow_sources import (
    ClosePriceRow,
    HoldingDistributionRow,
    InstitutionalFlowRow,
    ProviderSchemaError,
    parse_tdcc_holdings,
    parse_tpex_closes,
    parse_tpex_institutional,
    parse_twse_closes,
    parse_twse_institutional,
    read_json,
)


@dataclass(frozen=True)
class TaxonomyEntry:
    symbol: str
    name: str
    category: str
    market: str
    snapshot_date: str


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value}") from exc


def _dates(start_date: str, end_date: str) -> list[str]:
    start = _iso_date(start_date)
    end = _iso_date(end_date)
    if start > end:
        raise ValueError("start date must not exceed end date")
    if (end - start).days > 30:
        raise ValueError("sector-flow range must not exceed 31 calendar days")
    result = []
    cursor = start
    while cursor <= end:
        result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def load_taxonomy(cache_dir: Path, *, end_date: str) -> dict[str, TaxonomyEntry]:
    """Load the newest eligible FinMind stock-info cache snapshot."""
    root = cache_dir / "finmind" / "TaiwanStockInfo"
    eligible: list[tuple[str, Path]] = []
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                snapshot = date.fromisoformat(child.name)
            except ValueError:
                continue
            if snapshot <= _iso_date(end_date):
                eligible.append((child.name, child))
    if not eligible:
        return {}
    snapshot_date, snapshot_dir = max(eligible, key=lambda item: item[0])
    result: dict[str, TaxonomyEntry] = {}
    for path in sorted(snapshot_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            symbol = str(row.get("stock_id", "")).strip()
            if not symbol:
                continue
            result[symbol] = TaxonomyEntry(
                symbol=symbol,
                name=str(row.get("stock_name") or row.get("name") or symbol).strip(),
                category=str(row.get("industry_category") or "未分類").strip(),
                market=str(row.get("type") or "").strip().lower(),
                snapshot_date=snapshot_date,
            )
    return result


def _source_path(cache_dir: Path, provider: str, dataset: str, trading_date: str) -> Path:
    return cache_dir / provider / dataset / trading_date / "market.json"


def _load_source(
    cache_dir: Path,
    provider: str,
    dataset: str,
    parser: Any,
    trading_date: str,
) -> tuple[list[Any], dict[str, Any]]:
    path = _source_path(cache_dir, provider, dataset, trading_date)
    # Relative to cache_dir, so the report is identical however --cache-dir is spelled.
    status: dict[str, Any] = {"requested_date": trading_date, "cache_path": path.relative_to(cache_dir).as_posix()}
    if not path.exists():
        status.update({"state": "missing", "row_count": 0})
        return [], status
    try:
        rows = parser(read_json(path), trading_date)
        status.update({"state": "ok" if rows else "no_data", "row_count": len(rows), "embedded_date": trading_date})
        return rows, status
    except (OSError, json.JSONDecodeError, ProviderSchemaError) as exc:
        status.update({"state": "schema_error", "row_count": 0, "error": str(exc)})
        return [], status


def _blank_category(category: str) -> dict[str, Any]:
    return {
        "category": category,
        "foreign_net_shares": 0,
        "investment_trust_net_shares": 0,
        "dealer_net_shares": 0,
        "institutional_net_shares": 0,
        "estimated_foreign_net_amount_twd": 0.0,
        "estimated_investment_trust_net_amount_twd": 0.0,
        "estimated_dealer_net_amount_twd": 0.0,
        "estimated_institutional_net_amount_twd": 0.0,
        "covered_symbol_count": 0,
        "missing_price_count": 0,
        "amount_method": "net_shares_times_close",
        "_contributors": [],
    }


def _finalize_categories(categories: Mapping[str, dict[str, Any]], ranking_method: str) -> list[dict[str, Any]]:
    result = []
    for value in categories.values():
        item = dict(value)
        contributors = item.pop("_contributors")
        for field in (
            "estimated_foreign_net_amount_twd",
            "estimated_investment_trust_net_amount_twd",
            "estimated_dealer_net_amount_twd",
            "estimated_institutional_net_amount_twd",
        ):
            item[field] = round(float(item[field]), 2)
        for contributor in contributors:
            if contributor["estimated_net_amount_twd"] is not None:
                contributor["estimated_net_amount_twd"] = round(float(contributor["estimated_net_amount_twd"]), 2)
        positives = sorted((row for row in contributors if row["institutional_net_shares"] > 0), key=lambda row: (row["institutional_net_shares"], row["symbol"]), reverse=True)
        negatives = sorted((row for row in contributors if row["institutional_net_shares"] < 0), key=lambda row: (row["institutional_net_shares"], row["symbol"]))
        item["top_positive_contributors"] = positives[:5]
        item["top_negative_contributors"] = negatives[:5]
        result.append(item)
    key = "estimated_institutional_net_amount_twd" if ranking_method == "estimated_amount" else "institutional_net_shares"
    return sorted(result, key=lambda item: (item[key], item["category"]), reverse=True)


def _aggregate_day(
    flows: Sequence[InstitutionalFlowRow],
    closes: Mapping[tuple[str, str], float],
    taxonomy: Mapping[str, TaxonomyEntry],
    ranking_method: str,
) -> list[dict[str, Any]]:
    categories: dict[str, dict[str, Any]] = {}
    for row in flows:
        metadata = taxonomy.get(row.symbol)
        category = metadata.category if metadata else "未分類"
        item = categories.setdefault(category, _blank_category(category))
        for field in ("foreign_net_shares", "investment_trust_net_shares", "dealer_net_shares", "institutional_net_shares"):
            item[field] += getattr(row, field)
        close = closes.get((row.market, row.symbol))
        if close is None:
            item["missing_price_count"] += 1
            amount = None
        else:
            item["covered_symbol_count"] += 1
            item["estimated_foreign_net_amount_twd"] += row.foreign_net_shares * close
            item["estimated_investment_trust_net_amount_twd"] += row.investment_trust_net_shares * close
            item["estimated_dealer_net_amount_twd"] += row.dealer_net_shares * close
            item["estimated_institutional_net_amount_twd"] += row.institutional_net_shares * close
            amount = row.institutional_net_shares * close
        item["_contributors"].append({
            "market": row.market,
            "symbol": row.symbol,
            "name": metadata.name if metadata else row.name,
            "institutional_net_shares": row.institutional_net_shares,
            "estimated_net_amount_twd": amount,
        })
    return _finalize_categories(categories, ranking_method)


def _aggregate_period(
    day_inputs: Sequence[tuple[str, list[InstitutionalFlowRow], dict[tuple[str, str], float]]],
    taxonomy: Mapping[str, TaxonomyEntry],
    ranking_method: str,
) -> list[dict[str, Any]]:
    categories: dict[str, dict[str, Any]] = {}
    contributors: dict[tuple[str, str, str], dict[str, Any]] = {}
    for _trading_date, flows, closes in day_inputs:
        for row in flows:
            metadata = taxonomy.get(row.symbol)
            category = metadata.category if metadata else "未分類"
            item = categories.setdefault(category, _blank_category(category))
            for field in ("foreign_net_shares", "investment_trust_net_shares", "dealer_net_shares", "institutional_net_shares"):
                item[field] += getattr(row, field)
            close = closes.get((row.market, row.symbol))
            if close is None:
                item["missing_price_count"] += 1
                amount = None
            else:
                item["covered_symbol_count"] += 1
                item["estimated_foreign_net_amount_twd"] += row.foreign_net_shares * close
                item["estimated_investment_trust_net_amount_twd"] += row.investment_trust_net_shares * close
                item["estimated_dealer_net_amount_twd"] += row.dealer_net_shares * close
                item["estimated_institutional_net_amount_twd"] += row.institutional_net_shares * close
                amount = row.institutional_net_shares * close
            key = (category, row.market, row.symbol)
            contributor = contributors.setdefault(key, {
                "market": row.market,
                "symbol": row.symbol,
                "name": metadata.name if metadata else row.name,
                "institutional_net_shares": 0,
                "estimated_net_amount_twd": 0.0 if amount is not None else None,
            })
            contributor["institutional_net_shares"] += row.institutional_net_shares
            if amount is None:
                contributor["estimated_net_amount_twd"] = None
            elif contributor["estimated_net_amount_twd"] is not None:
                contributor["estimated_net_amount_twd"] += amount
    for (category, _market, _symbol), contributor in contributors.items():
        categories[category]["_contributors"].append(contributor)
    return _finalize_categories(categories, ranking_method)


def _load_holdings(cache_dir: Path, end_date: str) -> dict[str, list[HoldingDistributionRow]]:
    root = cache_dir / "tdcc" / "holding_distribution"
    result: dict[str, list[HoldingDistributionRow]] = {}
    if not root.exists():
        return result
    for path in sorted(root.glob("*/market.json")):
        try:
            rows = parse_tdcc_holdings(read_json(path))
        except (OSError, json.JSONDecodeError, ProviderSchemaError):
            continue
        if rows and rows[0].as_of_date <= end_date:
            result[rows[0].as_of_date] = rows
    return result


def build_large_holder_proxy(
    *,
    holdings_by_date: Mapping[str, Sequence[HoldingDistributionRow]],
    taxonomy: Mapping[str, TaxonomyEntry],
    closes: Mapping[str, float],
    end_date: str,
) -> dict[str, Any]:
    eligible = sorted(key for key in holdings_by_date if key <= end_date)
    latest = eligible[-1] if eligible else None
    if len(eligible) < 2:
        return {"status": "insufficient_data", "reason": "fewer_than_two_eligible_snapshots", "latest_as_of_date": latest}
    prior = eligible[-2]

    def by_symbol(rows: Sequence[HoldingDistributionRow]) -> dict[str, tuple[int, float]]:
        shares: dict[str, int] = defaultdict(int)
        percents: dict[str, float] = defaultdict(float)
        for row in rows:
            if 12 <= row.level <= 15:
                shares[row.symbol] += row.shares
                percents[row.symbol] += row.percent
        return {symbol: (shares[symbol], percents[symbol]) for symbol in shares}

    old = by_symbol(holdings_by_date[prior])
    new = by_symbol(holdings_by_date[latest])
    categories: dict[str, dict[str, Any]] = {}
    for symbol in sorted(set(old) | set(new)):
        category = taxonomy[symbol].category if symbol in taxonomy else "未分類"
        item = categories.setdefault(category, {"category": category, "large_holder_share_delta": 0, "sum_stock_percent_point_delta": 0.0, "estimated_change_twd": 0.0, "missing_price_count": 0})
        share_delta = new.get(symbol, (0, 0.0))[0] - old.get(symbol, (0, 0.0))[0]
        percent_delta = new.get(symbol, (0, 0.0))[1] - old.get(symbol, (0, 0.0))[1]
        item["large_holder_share_delta"] += share_delta
        item["sum_stock_percent_point_delta"] += percent_delta
        if symbol in closes:
            item["estimated_change_twd"] += share_delta * closes[symbol]
        else:
            item["missing_price_count"] += 1
    for item in categories.values():
        item["sum_stock_percent_point_delta"] = round(float(item["sum_stock_percent_point_delta"]), 6)
        item["estimated_change_twd"] = round(float(item["estimated_change_twd"]), 2)
    return {
        "status": "ok",
        "method": "holding_change_proxy",
        "prior_as_of_date": prior,
        "latest_as_of_date": latest,
        "levels": [12, 13, 14, 15],
        "categories": sorted(categories.values(), key=lambda row: (row["large_holder_share_delta"], row["category"]), reverse=True),
    }


def build_sector_flow_report(*, cache_dir: Path, start_date: str, end_date: str) -> dict[str, Any]:
    requested_dates = _dates(start_date, end_date)
    taxonomy = load_taxonomy(cache_dir, end_date=end_date)
    source_status: dict[str, dict[str, Any]] = {}
    day_inputs: list[tuple[str, list[InstitutionalFlowRow], dict[tuple[str, str], float]]] = []
    all_closes_by_symbol: dict[str, float] = {}
    total_flow_rows = 0
    total_priced_rows = 0
    mapped_rows = 0
    incomplete_source = False

    for trading_date in requested_dates:
        source_status[trading_date] = {}
        twse_flow, status = _load_source(cache_dir, "twse", "T86", parse_twse_institutional, trading_date)
        source_status[trading_date]["twse_institutional"] = status
        tpex_flow, status = _load_source(cache_dir, "tpex", "institutional", parse_tpex_institutional, trading_date)
        source_status[trading_date]["tpex_institutional"] = status
        twse_close, status = _load_source(cache_dir, "twse", "MI_INDEX", parse_twse_closes, trading_date)
        source_status[trading_date]["twse_close"] = status
        tpex_close, status = _load_source(cache_dir, "tpex", "daily_close", parse_tpex_closes, trading_date)
        source_status[trading_date]["tpex_close"] = status
        flows = list(twse_flow) + list(tpex_flow)
        if not flows:
            continue
        closes = {(row.market, row.symbol): row.close for row in list(twse_close) + list(tpex_close)}
        for row in list(twse_close) + list(tpex_close):
            all_closes_by_symbol[row.symbol] = row.close
        total_flow_rows += len(flows)
        total_priced_rows += sum(1 for row in flows if (row.market, row.symbol) in closes)
        mapped_rows += sum(1 for row in flows if row.symbol in taxonomy)
        states = source_status[trading_date]
        if any(states[key]["state"] != "ok" for key in states):
            incomplete_source = True
        day_inputs.append((trading_date, flows, closes))

    price_coverage = total_priced_rows / total_flow_rows if total_flow_rows else 0.0
    taxonomy_coverage = mapped_rows / total_flow_rows if total_flow_rows else 0.0
    daily_coverages = [sum(1 for row in flows if (row.market, row.symbol) in closes) / len(flows) for _, flows, closes in day_inputs]
    ranking_method = "estimated_amount" if daily_coverages and min(daily_coverages) >= 0.9 else "net_shares"
    daily = [
        {
            "trading_date": trading_date,
            "price_coverage": sum(1 for row in flows if (row.market, row.symbol) in closes) / len(flows),
            "categories": _aggregate_day(flows, closes, taxonomy, ranking_method),
        }
        for trading_date, flows, closes in day_inputs
    ]
    if not total_flow_rows:
        status = "blocked"
    elif incomplete_source or price_coverage < 0.9 or taxonomy_coverage < 1.0:
        status = "degraded"
    else:
        status = "ok"
    warnings = []
    if price_coverage < 0.9 and total_flow_rows:
        warnings.append("price coverage below 90%; rankings use exact net shares")
    if taxonomy_coverage < 1.0 and total_flow_rows:
        warnings.append("taxonomy mapping incomplete; unmatched symbols are 未分類")
    holdings = _load_holdings(cache_dir, end_date)
    return {
        "schema_version": 1,
        "requested_period": {"start_date": start_date, "end_date": end_date},
        "observed_trading_dates": [item[0] for item in day_inputs],
        "status": status,
        "ranking_method": ranking_method,
        "source_status": source_status,
        "taxonomy": {"snapshot_date": next(iter(taxonomy.values())).snapshot_date if taxonomy else None, "mapped_rows": mapped_rows, "total_rows": total_flow_rows, "coverage": taxonomy_coverage},
        "price_coverage": price_coverage,
        "daily": daily,
        "period_summary": _aggregate_period(day_inputs, taxonomy, ranking_method),
        "large_holder": build_large_holder_proxy(holdings_by_date=holdings, taxonomy=taxonomy, closes=all_closes_by_symbol, end_date=end_date),
        "exclusions": {"symbol_rule": "^[1-9][0-9]{3}$"},
        "warnings": warnings,
    }
