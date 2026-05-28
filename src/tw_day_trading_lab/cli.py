from __future__ import annotations

import argparse
import json
from pathlib import Path

from .candidate_engine import rank_candidates
from .models import CandidateInput, CandidateScore
from .old_log_importer import import_trade_log_csv, render_failure_replay_markdown
from .reports import render_html, render_markdown
from .storage import (
    DatabaseStorage,
    TiDBConfig,
    apply_schema_file,
    connect_tidb,
    load_candidate_payload,
    load_import_payload,
    persist_candidate_run,
    persist_import_samples,
)


def load_candidate_inputs(path: Path) -> list[CandidateInput]:
    """Load raw candidate inputs from JSON for the candidate ranking command."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("candidate input must be a JSON list")
    return [CandidateInput.from_dict(item) for item in raw]


def load_candidate_scores(path: Path) -> list[CandidateScore]:
    """Load ranked candidate scores from either a list or wrapped CLI payload."""
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
    """Write text output and create the parent directory when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    """Write JSON output with stable UTF-8 formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_sample_summary(path: Path | None) -> dict[str, object] | None:
    """Load sample classification counts from an old-log import JSON file."""
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        return raw["summary"]
    return None


def cmd_candidates_build(args: argparse.Namespace) -> None:
    """Rank candidate inputs and write the daily candidate JSON payload."""
    inputs = load_candidate_inputs(Path(args.input))
    ranked = rank_candidates(inputs, limit=args.limit)
    payload = {
        "trading_date": args.date,
        "candidates": [item.to_dict() for item in ranked],
    }
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-candidates.json"
    write_json(output, payload)
    print(output)


def cmd_candidates_persist(args: argparse.Namespace) -> None:
    """Persist ranked candidate output into TiDB."""
    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=True)
    try:
        storage = DatabaseStorage(connection, dialect="tidb")
        run_id = args.run_id or f"{args.date}:{args.source}"
        summary = persist_candidate_run(
            storage,
            run_id=run_id,
            trading_date=args.date,
            source=args.source,
            status=args.status,
            candidates=load_candidate_payload(Path(args.input)),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        connection.close()


def cmd_report_daily(args: argparse.Namespace) -> None:
    """Build the daily candidate report, optionally including sample counts."""
    candidates = load_candidate_scores(Path(args.input))
    sample_summary = load_sample_summary(Path(args.samples)) if args.samples else None
    if args.format == "html":
        content = render_html(args.date, candidates, sample_summary=sample_summary)
        default_suffix = "html"
    else:
        content = render_markdown(args.date, candidates, sample_summary=sample_summary)
        default_suffix = "md"
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-daily.{default_suffix}"
    write_text(output, content)
    print(output)


def cmd_notify_telegram(args: argparse.Namespace) -> None:
    """Print a Telegram-safe summary unless real sends are explicitly implemented later."""
    report_path = Path(args.report)
    content = report_path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    summary = "\n".join(lines[: min(12, len(lines))])
    if args.dry_run:
        print(summary)
        return
    raise SystemExit("telegram send is intentionally disabled in MVP; use --dry-run")


def cmd_old_logs_import(args: argparse.Namespace) -> None:
    """Import old trade_decisions CSV data and emit classified samples plus report."""
    result = import_trade_log_csv(Path(args.input))
    output = Path(args.output) if args.output else Path("reports") / "old-log-samples.json"
    report_output = (
        Path(args.report_output) if args.report_output else Path("reports") / "old-log-failure-replay.md"
    )
    write_json(output, result.to_dict())
    write_text(report_output, render_failure_replay_markdown(args.date, result))
    print(output)
    print(report_output)


def cmd_db_init(args: argparse.Namespace) -> None:
    """Apply the TiDB schema through the MySQL protocol."""
    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=False)
    try:
        apply_schema_file(connection, Path(args.schema), database=config.database)
        print(f"schema applied: {config.database}")
    finally:
        connection.close()


def cmd_samples_persist(args: argparse.Namespace) -> None:
    """Persist old-log import JSON samples into TiDB."""
    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=True)
    try:
        storage = DatabaseStorage(connection, dialect="tidb")
        summary = persist_import_samples(storage, load_import_payload(Path(args.input)))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        connection.close()


def cmd_samples_summary(args: argparse.Namespace) -> None:
    """Print persisted sample validity counts from TiDB."""
    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=True)
    try:
        storage = DatabaseStorage(connection, dialect="tidb")
        print(json.dumps(storage.fetch_sample_summary(), ensure_ascii=False, indent=2))
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser and bind each command to its composition function."""
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
    persist_candidates = candidate_sub.add_parser("persist")
    persist_candidates.add_argument("--date", required=True)
    persist_candidates.add_argument("--input", required=True)
    persist_candidates.add_argument("--run-id")
    persist_candidates.add_argument("--source", default="cli")
    persist_candidates.add_argument("--status", default="generated")
    persist_candidates.set_defaults(func=cmd_candidates_persist)

    report = subparsers.add_parser("report")
    report_sub = report.add_subparsers(required=True)
    daily = report_sub.add_parser("daily")
    daily.add_argument("--date", required=True)
    daily.add_argument("--input", required=True)
    daily.add_argument("--samples")
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

    old_logs = subparsers.add_parser("old-logs")
    old_logs_sub = old_logs.add_subparsers(required=True)
    old_logs_import = old_logs_sub.add_parser("import")
    old_logs_import.add_argument("--date", required=True)
    old_logs_import.add_argument("--input", required=True)
    old_logs_import.add_argument("--output")
    old_logs_import.add_argument("--report-output")
    old_logs_import.set_defaults(func=cmd_old_logs_import)

    db = subparsers.add_parser("db")
    db_sub = db.add_subparsers(required=True)
    db_init = db_sub.add_parser("init")
    db_init.add_argument("--schema", default="sql/001_init.sql")
    db_init.set_defaults(func=cmd_db_init)

    samples = subparsers.add_parser("samples")
    samples_sub = samples.add_subparsers(required=True)
    samples_persist = samples_sub.add_parser("persist")
    samples_persist.add_argument("--input", required=True)
    samples_persist.set_defaults(func=cmd_samples_persist)
    samples_summary = samples_sub.add_parser("summary")
    samples_summary.set_defaults(func=cmd_samples_summary)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point used by `python -m tw_day_trading_lab.cli`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
