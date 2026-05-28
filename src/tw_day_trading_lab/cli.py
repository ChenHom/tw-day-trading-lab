from __future__ import annotations

import argparse
import json
from pathlib import Path

from .candidate_engine import rank_candidates
from .models import CandidateInput, CandidateScore
from .reports import render_html, render_markdown


def load_candidate_inputs(path: Path) -> list[CandidateInput]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("candidate input must be a JSON list")
    return [CandidateInput.from_dict(item) for item in raw]


def load_candidate_scores(path: Path) -> list[CandidateScore]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("candidates", [])
    return [
        CandidateScore(
            symbol=item["symbol"],
            name=item["name"],
            rank=int(item["rank"]),
            archetype=item["archetype"],
            total_score=float(item["total_score"]),
            liquidity_score=float(item["liquidity_score"]),
            event_score=float(item["event_score"]),
            structure_score=float(item["structure_score"]),
            continuity_score=float(item["continuity_score"]),
            crowding_penalty=float(item["crowding_penalty"]),
            next_day_actionable=bool(item["next_day_actionable"]),
            reasons=tuple(item.get("reasons", [])),
            downgrade_reasons=tuple(item.get("downgrade_reasons", [])),
        )
        for item in raw
    ]


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_candidates_build(args: argparse.Namespace) -> None:
    inputs = load_candidate_inputs(Path(args.input))
    ranked = rank_candidates(inputs, limit=args.limit)
    payload = {
        "trading_date": args.date,
        "candidates": [item.to_dict() for item in ranked],
    }
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-candidates.json"
    write_json(output, payload)
    print(output)


def cmd_report_daily(args: argparse.Namespace) -> None:
    candidates = load_candidate_scores(Path(args.input))
    if args.format == "html":
        content = render_html(args.date, candidates)
        default_suffix = "html"
    else:
        content = render_markdown(args.date, candidates)
        default_suffix = "md"
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-daily.{default_suffix}"
    write_text(output, content)
    print(output)


def cmd_notify_telegram(args: argparse.Namespace) -> None:
    report_path = Path(args.report)
    content = report_path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    summary = "\n".join(lines[: min(12, len(lines))])
    if args.dry_run:
        print(summary)
        return
    raise SystemExit("telegram send is intentionally disabled in MVP; use --dry-run")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tw-daytrade")
    subparsers = parser.add_subparsers(required=True)

    candidates = subparsers.add_parser("candidates")
    candidate_sub = candidates.add_subparsers(required=True)
    build = candidate_sub.add_parser("build")
    build.add_argument("--date", required=True)
    build.add_argument("--input", required=True)
    build.add_argument("--output")
    build.add_argument("--limit", type=int, default=80)
    build.set_defaults(func=cmd_candidates_build)

    report = subparsers.add_parser("report")
    report_sub = report.add_subparsers(required=True)
    daily = report_sub.add_parser("daily")
    daily.add_argument("--date", required=True)
    daily.add_argument("--input", required=True)
    daily.add_argument("--format", choices=["md", "html"], default="md")
    daily.add_argument("--output")
    daily.set_defaults(func=cmd_report_daily)

    notify = subparsers.add_parser("notify")
    notify_sub = notify.add_subparsers(required=True)
    telegram = notify_sub.add_parser("telegram")
    telegram.add_argument("--date", required=True)
    telegram.add_argument("--report", required=True)
    telegram.add_argument("--dry-run", action="store_true")
    telegram.set_defaults(func=cmd_notify_telegram)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

