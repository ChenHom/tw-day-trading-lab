from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .cost import TaiwanDayTradeCostModel


@dataclass(frozen=True)
class ReplayAssumptions:
    """Cost and slippage assumptions."""

    cost_r: float = 0.0
    cost_model: TaiwanDayTradeCostModel | None = None

    def get_cost_r(self, entry_price: float | None, stop_price: float | None, quantity: int = 1000) -> float:
        if self.cost_model is not None and entry_price is not None and stop_price is not None:
            return self.cost_model.cost_r(entry_price, stop_price, quantity)
        return self.cost_r



@dataclass(frozen=True)
class ReplayTrade:
    """One replayed valid trade sample with R metrics."""

    sample_id: str
    symbol: str
    idempotency_key: str
    realized_r_gross: float | None
    estimated_cost_r: float | None
    realized_r_net: float | None
    mfe_r: float | None
    mae_r: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayResult:
    """Replay output containing per-trade rows and aggregate expectancy."""

    trades: list[ReplayTrade]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "trades": [trade.to_dict() for trade in self.trades],
        }


def replay_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    price_bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    assumptions: ReplayAssumptions | None = None,
) -> ReplayResult:
    """Replay classified samples while using only validity=valid for expectancy."""
    assumptions = assumptions or ReplayAssumptions()
    price_bars_by_symbol = price_bars_by_symbol or {}
    seen_idempotency_keys: set[str] = set()
    trades: list[ReplayTrade] = []
    skipped_excluded = 0
    skipped_needs_review = 0
    skipped_duplicate = 0

    for sample in samples:
        validity = str(sample.get("validity") or "")
        if validity != "valid":
            if validity == "needs_review":
                skipped_needs_review += 1
            else:
                skipped_excluded += 1
            continue
        idempotency_key = str(sample.get("idempotency_key") or "")
        if idempotency_key in seen_idempotency_keys:
            skipped_duplicate += 1
            continue
        seen_idempotency_keys.add(idempotency_key)
        trades.append(
            replay_one_sample(
                sample,
                price_bars=price_bars_by_symbol.get(str(sample.get("symbol") or ""), []),
                assumptions=assumptions,
            )
        )

    summary = summarize_replay(
        samples,
        trades,
        skipped_excluded=skipped_excluded,
        skipped_needs_review=skipped_needs_review,
        skipped_duplicate=skipped_duplicate,
    )
    return ReplayResult(trades=trades, summary=summary)


def replay_one_sample(
    sample: Mapping[str, Any],
    *,
    price_bars: Sequence[Mapping[str, Any]],
    assumptions: ReplayAssumptions,
) -> ReplayTrade:
    """Compute replay metrics for one valid sample."""
    entry_price = parse_number(sample.get("entry_price"))
    exit_price = parse_number(sample.get("exit_price"))
    stop_price = parse_number(sample.get("stop_price"))
    risk = compute_risk(entry_price, stop_price)
    gross_r = parse_number(sample.get("realized_r_gross"))
    if gross_r is None and risk is not None and entry_price is not None and exit_price is not None:
        gross_r = round((exit_price - entry_price) / risk, 4)

    quantity = int(sample.get("quantity") or 1000)
    cost_r = assumptions.get_cost_r(entry_price, stop_price, quantity) if gross_r is not None else None
    net_r = round(gross_r - cost_r, 4) if gross_r is not None and cost_r is not None else None
    mfe_r = parse_number(sample.get("mfe_r"))
    mae_r = parse_number(sample.get("mae_r"))
    if (mfe_r is None or mae_r is None) and risk is not None and entry_price is not None:
        path_mfe, path_mae = compute_path_excursions(entry_price, risk, price_bars)
        mfe_r = path_mfe if mfe_r is None else mfe_r
        mae_r = path_mae if mae_r is None else mae_r

    return ReplayTrade(
        sample_id=str(sample.get("sample_id") or ""),
        symbol=str(sample.get("symbol") or ""),
        idempotency_key=str(sample.get("idempotency_key") or ""),
        realized_r_gross=gross_r,
        estimated_cost_r=cost_r,
        realized_r_net=net_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
    )


def summarize_replay(
    samples: Sequence[Mapping[str, Any]],
    trades: Sequence[ReplayTrade],
    *,
    skipped_excluded: int,
    skipped_needs_review: int,
    skipped_duplicate: int,
) -> dict[str, Any]:
    gross_values = [trade.realized_r_gross for trade in trades if trade.realized_r_gross is not None]
    net_values = [trade.realized_r_net for trade in trades if trade.realized_r_net is not None]
    cost_values = [trade.estimated_cost_r for trade in trades if trade.estimated_cost_r is not None]

    import math
    n = len(net_values)
    ci_lower = None
    ci_upper = None
    warning = None

    if n >= 2:
        mean_val = sum(net_values) / n
        variance = sum((x - mean_val) ** 2 for x in net_values) / (n - 1)
        std_dev = math.sqrt(variance)
        se = std_dev / math.sqrt(n)
        margin = 1.96 * se
        ci_lower = round(mean_val - margin, 4)
        ci_upper = round(mean_val + margin, 4)

    if n < 30:
        warning = f"Extremely small sample size (N={n} < 30). Expectancy lacks statistical significance."
    elif n < 100:
        warning = f"Small sample size (N={n} < 100). Use caution when evaluating strategy expectancy."

    return {
        "total_samples": len(samples),
        "replayed": len(trades),
        "skipped_excluded": skipped_excluded,
        "skipped_needs_review": skipped_needs_review,
        "skipped_duplicate_idempotency": skipped_duplicate,
        "expectancy_gross_r": average(gross_values),
        "average_cost_r": average(cost_values),
        "expectancy_net_r": average(net_values),
        "net_expectancy_ci_lower": ci_lower,
        "net_expectancy_ci_upper": ci_upper,
        "statistical_warning": warning,
    }


def render_replay_markdown(trading_date: str, result: ReplayResult) -> str:
    """Render replay results with gross/cost/net R split explicitly visible."""
    summary = result.summary
    lines = [
        f"# Replay Report {trading_date}",
        "",
        "## Summary",
        "",
        "- expectancy scope: `validity = valid` only",
        f"- total samples: {summary['total_samples']}",
        f"- replayed: {summary['replayed']}",
        f"- skipped excluded / needs_review: {summary['skipped_excluded']} / {summary['skipped_needs_review']}",
        f"- skipped duplicate idempotency: {summary['skipped_duplicate_idempotency']}",
        f"- expectancy Gross R: {_format_optional(summary['expectancy_gross_r'])}",
        f"- average Cost R: {_format_optional(summary['average_cost_r'])}",
        f"- expectancy Net R: {_format_optional(summary['expectancy_net_r'])}",
        f"- 95% Confidence Interval for Net R: [{_format_optional(summary.get('net_expectancy_ci_lower'))}, {_format_optional(summary.get('net_expectancy_ci_upper'))}]",
        "",
    ]
    if summary.get("statistical_warning"):
        lines.append(f"> [!WARNING]")
        lines.append(f"> {summary['statistical_warning']}")
        lines.append("")

    lines.extend([
        "## Trades",
        "",
        "| Sample | Symbol | Gross R | Cost R | Net R | MFE R | MAE R |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for trade in result.trades:
        lines.append(
            "| "
            f"{trade.sample_id} | {trade.symbol} | "
            f"{_format_optional(trade.realized_r_gross)} | "
            f"{_format_optional(trade.estimated_cost_r)} | "
            f"{_format_optional(trade.realized_r_net)} | "
            f"{_format_optional(trade.mfe_r)} | "
            f"{_format_optional(trade.mae_r)} |"
        )
    return "\n".join(lines) + "\n"


def compute_path_excursions(
    entry_price: float,
    risk: float,
    price_bars: Sequence[Mapping[str, Any]],
) -> tuple[float | None, float | None]:
    highs = [parse_number(bar.get("high", bar.get("max"))) for bar in price_bars]
    lows = [parse_number(bar.get("low", bar.get("min"))) for bar in price_bars]
    high_values = [value for value in highs if value is not None]
    low_values = [value for value in lows if value is not None]
    mfe_r = round((max(high_values) - entry_price) / risk, 4) if high_values else None
    mae_r = round((min(low_values) - entry_price) / risk, 4) if low_values else None
    return mfe_r, mae_r


def compute_risk(entry_price: float | None, stop_price: float | None) -> float | None:
    if entry_price is None or stop_price is None:
        return None
    risk = entry_price - stop_price
    return risk if risk > 0 else None


def parse_number(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _format_optional(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def check_exits(
    *,
    entry_price: float,
    stop_price: float,
    target_price: float,
    price_bars: Sequence[Mapping[str, Any]],
    exit_time: str = "13:20",
) -> tuple[float, str]:
    """
    Simulate exits on price bars.
    Returns (exit_price, exit_reason).
    """
    for bar in price_bars:
        # Check time exit first if bar contains time
        bar_time = bar.get("time")
        if bar_time and bar_time >= exit_time:
            return float(bar.get("close", bar.get("close_price", entry_price))), "time_exit"

        # Check Stop Loss
        low_val = bar.get("low", bar.get("min"))
        if low_val is not None and float(low_val) <= stop_price:
            return stop_price, "stop_loss"

        # Check Take Profit
        high_val = bar.get("high", bar.get("max"))
        if high_val is not None and float(high_val) >= target_price:
            return target_price, "take_profit"

    # Default time exit at the end of the bars
    if price_bars:
        last_bar = price_bars[-1]
        return float(last_bar.get("close", last_bar.get("close_price", entry_price))), "time_exit"

    return entry_price, "no_bars"
