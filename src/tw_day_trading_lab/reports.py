from __future__ import annotations

from html import escape
from typing import Any

from .models import CandidateScore


def render_markdown(
    trading_date: str,
    candidates: list[CandidateScore],
    sample_summary: dict[str, Any] | None = None,
    source_summary: dict[str, Any] | None = None,
) -> str:
    """Render the daily candidate report as Markdown."""
    actionable = [item for item in candidates if item.next_day_actionable]
    lines = [
        f"# 台股當沖日報 {trading_date}",
        "",
        "## 摘要",
        "",
        f"- 候選總數：{len(candidates)}",
        f"- `next_day_actionable`：{len(actionable)}",
        "- 真實下單：禁止，第一階段只允許 replay / paper / simulation 驗證",
        "",
        "## Top Candidates",
        "",
        "| Rank | Symbol | Name | Archetype | Score | Actionable | Downgrade |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for item in candidates:
        downgrade = ", ".join(item.downgrade_reasons) if item.downgrade_reasons else "-"
        lines.append(
            f"| {item.rank} | {item.symbol} | {item.name} | {item.archetype} | "
            f"{item.total_score:.2f} | {item.next_day_actionable} | {downgrade} |"
        )
    if source_summary:
        lines.extend(
            [
                "",
                "## 資料來源",
                "",
                _format_source_summary_markdown(source_summary),
            ]
        )
    lines.extend(
        [
            "",
            "## 樣本統計",
            "",
            _format_sample_summary_markdown(sample_summary),
            "- simulation 樣本必須和 replay 樣本分開列示",
        ]
    )
    return "\n".join(lines) + "\n"


def render_html(
    trading_date: str,
    candidates: list[CandidateScore],
    sample_summary: dict[str, Any] | None = None,
    source_summary: dict[str, Any] | None = None,
) -> str:
    """Render the daily candidate report as a small standalone HTML page."""
    rows = []
    for item in candidates:
        downgrade = ", ".join(item.downgrade_reasons) if item.downgrade_reasons else "-"
        rows.append(
            "<tr>"
            f"<td>{item.rank}</td>"
            f"<td>{escape(item.symbol)}</td>"
            f"<td>{escape(item.name)}</td>"
            f"<td>{escape(item.archetype)}</td>"
            f"<td>{item.total_score:.2f}</td>"
            f"<td>{'yes' if item.next_day_actionable else 'no'}</td>"
            f"<td>{escape(downgrade)}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>台股當沖日報 {escape(trading_date)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; line-height: 1.5; margin: 32px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; text-align: left; }}
    th {{ background: #f6f8fa; }}
  </style>
</head>
<body>
  <h1>台股當沖日報 {escape(trading_date)}</h1>
  <p>第一階段只允許 replay / paper / simulation 驗證，禁止真實自動下單。</p>
  <p>{escape(_format_source_summary_text(source_summary))}</p>
  <p>{escape(_format_sample_summary_text(sample_summary))}</p>
  <table>
    <thead>
      <tr>
        <th>Rank</th><th>Symbol</th><th>Name</th><th>Archetype</th>
        <th>Score</th><th>Actionable</th><th>Downgrade</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""


def render_close_report_markdown(
    trading_date: str,
    candidates: list[CandidateScore],
    *,
    candidate_source_summary: dict[str, Any] | None = None,
    replay_summary: dict[str, Any] | None = None,
    simulation_summary: dict[str, Any] | None = None,
    simulation_results: list[dict[str, Any]] | None = None,
) -> str:
    """Render one close report across candidate, replay, and simulation outputs."""
    actionable = [item for item in candidates if item.next_day_actionable]
    lines = [
        f"# 台股當沖 Close Report {trading_date}",
        "",
        "## Summary",
        "",
        f"- candidates：{len(candidates)}",
        f"- next_day_actionable：{len(actionable)}",
        "- strategy expectancy：只來自 replay 的 `validity = valid` 樣本",
        "- execution simulation：只驗證執行鏈路，不證明策略 edge",
        "- 真實下單：禁止，Telegram 只允許 dry-run summary",
        "",
        "## Candidate Close",
        "",
        _format_source_summary_markdown(candidate_source_summary or {}),
        "",
        "| Rank | Symbol | Name | Archetype | Score | Actionable | Downgrade |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for item in candidates:
        downgrade = ", ".join(item.downgrade_reasons) if item.downgrade_reasons else "-"
        lines.append(
            f"| {item.rank} | {item.symbol} | {item.name} | {item.archetype} | "
            f"{item.total_score:.2f} | {item.next_day_actionable} | {downgrade} |"
        )
    if not candidates:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Strategy Replay",
            "",
            "- expectancy scope: `validity = valid` only",
            _format_replay_summary_markdown(replay_summary),
            "",
            "## Execution Simulation",
            "",
            "- simulation 不納入 replay expectancy",
            _format_simulation_summary_markdown(simulation_summary),
            _format_needs_review_details_markdown(simulation_results),
        ]
    )
    return "\n".join(lines) + "\n"


def render_close_report_telegram_summary(
    trading_date: str,
    candidates: list[CandidateScore],
    *,
    replay_summary: dict[str, Any] | None = None,
    simulation_summary: dict[str, Any] | None = None,
) -> str:
    """Render a compact Telegram-safe close summary from structured report inputs."""
    actionable = [item for item in candidates if item.next_day_actionable]
    needs_review = int((simulation_summary or {}).get("needs_review", 0) or 0)
    simulated = int((simulation_summary or {}).get("simulated", 0) or 0)
    duplicate = int((simulation_summary or {}).get("duplicate", 0) or 0)
    replayed = int((replay_summary or {}).get("replayed", 0) or 0)
    action = (
        "先處理 needs_review，再進下一步"
        if needs_review
        else "可進下一步，但仍禁止真實自動下單"
    )
    return "\n".join(
        [
            f"台股當沖 Close Summary {trading_date}",
            f"- candidates/actionable：{len(candidates)} / {len(actionable)}",
            f"- replayed：{replayed}",
            f"- replay net R：{_format_optional_float((replay_summary or {}).get('expectancy_net_r'))}",
            f"- simulation：simulated {simulated} / needs_review {needs_review} / duplicate {duplicate}",
            f"- action：{action}",
        ]
    ) + "\n"


def _format_sample_summary_markdown(sample_summary: dict[str, Any] | None) -> str:
    """Format validity counts for Markdown while keeping the old fallback text."""
    if not sample_summary:
        return "- valid / excluded / needs_review：尚未接 replay ledger"
    return (
        "- valid / excluded / needs_review："
        f"{sample_summary.get('valid', 0)} / "
        f"{sample_summary.get('excluded', 0)} / "
        f"{sample_summary.get('needs_review', 0)}"
    )


def _format_source_summary_markdown(source_summary: dict[str, Any]) -> str:
    """Format candidate source and data gap counts for Markdown."""
    return "\n".join(
        [
            f"- 來源：{source_summary.get('source', 'unknown')}",
            f"- input files：{source_summary.get('input_files', 0)}",
            f"- built candidates：{source_summary.get('built_candidates', 0)}",
            f"- degraded candidates：{source_summary.get('degraded_candidates', 0)}",
            f"- 資料缺口檔案：{source_summary.get('data_gap_files', 0)}",
            f"- 低流動性過濾：{source_summary.get('filtered_low_liquidity', 0)}",
            f"- 非普通股過濾：{source_summary.get('filtered_non_common_stock', 0)}",
            f"- 缺法人資料：{source_summary.get('missing_chip_files', 0)}",
            f"- 缺融資融券資料：{source_summary.get('missing_margin_files', 0)}",
        ]
    )


def _format_source_summary_text(source_summary: dict[str, Any] | None) -> str:
    """Format candidate source summary for compact HTML text."""
    if not source_summary:
        return "候選資料來源：未提供 source summary"
    return (
        f"候選資料來源：{source_summary.get('source', 'unknown')}；"
        f"input files {source_summary.get('input_files', 0)}；"
        f"built {source_summary.get('built_candidates', 0)}；"
        f"degraded {source_summary.get('degraded_candidates', 0)}；"
        f"data gaps {source_summary.get('data_gap_files', 0)}；"
        f"missing chip {source_summary.get('missing_chip_files', 0)}；"
        f"missing margin {source_summary.get('missing_margin_files', 0)}"
    )


def _format_sample_summary_text(sample_summary: dict[str, Any] | None) -> str:
    """Format validity counts for HTML text."""
    if not sample_summary:
        return "valid / excluded / needs_review：尚未接 replay ledger"
    return (
        "valid / excluded / needs_review："
        f"{sample_summary.get('valid', 0)} / "
        f"{sample_summary.get('excluded', 0)} / "
        f"{sample_summary.get('needs_review', 0)}"
    )


def _format_optional_float(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def _format_replay_summary_markdown(replay_summary: dict[str, Any] | None) -> str:
    if not replay_summary:
        return "\n".join(
            [
                "- replay input：未提供",
                "- expectancy Gross R：-",
                "- average Cost R：-",
                "- expectancy Net R：-",
            ]
        )
    return "\n".join(
        [
            f"- total samples：{replay_summary.get('total_samples', 0)}",
            f"- replayed：{replay_summary.get('replayed', 0)}",
            "- skipped excluded / needs_review："
            f"{replay_summary.get('skipped_excluded', 0)} / "
            f"{replay_summary.get('skipped_needs_review', 0)}",
            f"- expectancy Gross R：{_format_optional_float(replay_summary.get('expectancy_gross_r'))}",
            f"- average Cost R：{_format_optional_float(replay_summary.get('average_cost_r'))}",
            f"- expectancy Net R：{_format_optional_float(replay_summary.get('expectancy_net_r'))}",
        ]
    )


def _format_simulation_summary_markdown(simulation_summary: dict[str, Any] | None) -> str:
    if not simulation_summary:
        return "\n".join(
            [
                "- simulation input：未提供",
                "- expectancy eligible：0",
                "- needs_review：0",
            ]
        )
    known_keys = {"total", "expectancy_eligible"}
    status_lines = [
        f"- {key}：{simulation_summary[key]}"
        for key in sorted(simulation_summary)
        if key not in known_keys
    ]
    if "needs_review" not in simulation_summary:
        status_lines.append("- needs_review：0")
    return "\n".join(
        [
            f"- total：{simulation_summary.get('total', 0)}",
            f"- expectancy eligible：{simulation_summary.get('expectancy_eligible', 0)}",
            *status_lines,
        ]
    )


def _format_needs_review_details_markdown(simulation_results: list[dict[str, Any]] | None) -> str:
    if not simulation_results:
        return ""
    needs_review = [
        item for item in simulation_results if str(item.get("status") or "") == "needs_review"
    ]
    if not needs_review:
        return ""
    lines = [
        "",
        "## Needs Review Details",
        "",
        "| Symbol | Idempotency Key | Reason |",
        "|---|---|---|",
    ]
    for item in needs_review:
        symbol = _extract_symbol(item)
        key = _extract_idempotency_key(item)
        reason = str(item.get("review_reason") or "-")
        lines.append(f"| {symbol} | {key} | {reason} |")
    return "\n".join(lines)


def _extract_symbol(item: dict[str, Any]) -> str:
    signal = item.get("signal")
    if isinstance(signal, dict) and signal.get("symbol"):
        return str(signal["symbol"])
    trade = item.get("broker_trade")
    if isinstance(trade, dict) and trade.get("symbol"):
        return str(trade["symbol"])
    return "-"


def _extract_idempotency_key(item: dict[str, Any]) -> str:
    trade = item.get("broker_trade")
    if isinstance(trade, dict) and trade.get("idempotency_key"):
        return str(trade["idempotency_key"])
    order_intent = item.get("order_intent")
    if isinstance(order_intent, dict):
        existing_key = order_intent.get("idempotency_key")
        if existing_key:
            return str(existing_key)
        parts = [
            order_intent.get("trading_date"),
            order_intent.get("strategy_id"),
            order_intent.get("symbol"),
            order_intent.get("setup_id"),
            order_intent.get("side"),
        ]
        if all(parts):
            return ":".join(str(part) for part in parts)
    return "-"
