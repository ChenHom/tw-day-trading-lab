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
        f"data gaps {source_summary.get('data_gap_files', 0)}"
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
