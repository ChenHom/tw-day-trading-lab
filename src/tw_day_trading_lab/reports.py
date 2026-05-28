from __future__ import annotations

from html import escape

from .models import CandidateScore


def render_markdown(trading_date: str, candidates: list[CandidateScore]) -> str:
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
    lines.extend(
        [
            "",
            "## 樣本統計",
            "",
            "- valid / excluded / needs_review：尚未接 replay ledger",
            "- simulation 樣本必須和 replay 樣本分開列示",
        ]
    )
    return "\n".join(lines) + "\n"


def render_html(trading_date: str, candidates: list[CandidateScore]) -> str:
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

