from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .candidate_engine import rank_candidates
from .candidate_builder import build_candidates_from_raw_cache
from .finmind_ingestion import (
    FinMindDataLoaderClient,
    build_single_request,
    ingest_finmind_requests,
    read_request_file,
)
from .ledger import PaperLedger
from .models import CandidateInput, CandidateScore
from .old_log_importer import import_trade_log_csv, render_failure_replay_markdown
from .replay import ReplayAssumptions, render_replay_markdown, replay_samples
from .reports import (
    render_close_report_markdown,
    render_close_report_telegram_summary,
    render_html,
    render_markdown,
)
from .simulation import (
    DryRunSimulationBroker,
    FileExecutionSyncStore,
    RiskDecision,
    ShioajiSimulationAdapter,
    SimulationResult,
    SignalIntent,
    broker_trades_from_payload,
    build_restart_sync_report,
    ledger_positions_from_payload,
    render_simulation_markdown,
    restore_ledger_from_positions,
)
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


def load_candidate_source_summary(path: Path) -> dict[str, object] | None:
    """Load optional source summary from candidate JSON payload."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        return raw["summary"]
    return None


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


def load_payload_summary(path: Path | None) -> dict[str, object] | None:
    """Load a top-level summary object from a JSON payload."""
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        return raw["summary"]
    return None


def load_required_payload_summary(path: Path, label: str) -> dict[str, object]:
    """Load a required summary object and fail clearly when payload shape is wrong."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("summary"), dict):
        return raw["summary"]
    raise ValueError(f"{label} payload must contain a summary object")


def load_payload_results(path: Path | None) -> list[dict[str, object]] | None:
    """Load optional top-level results from a JSON payload."""
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        return [dict(item) for item in raw["results"] if isinstance(item, dict)]
    return None


def load_replay_samples(path: Path) -> list[dict[str, object]]:
    """Load replay samples from old-log import output or a plain sample list."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("samples", [])
    if not isinstance(raw, list):
        raise ValueError("replay input must be a sample list or {samples: [...]}")
    return [dict(item) for item in raw]


def load_simulation_plan(path: Path) -> list[tuple[SignalIntent, RiskDecision]]:
    """Load simulation signals and risk decisions from JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("signals", []))
    if not isinstance(raw, list):
        raise ValueError("simulation input must be a list or {items: [...]}")

    plan: list[tuple[SignalIntent, RiskDecision]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("simulation item must be an object")
        signal_data = item.get("signal", item)
        decision_data = item.get("risk_decision", {"approved": True, "reason": "default_approved"})
        if not isinstance(signal_data, dict) or not isinstance(decision_data, dict):
            raise ValueError("simulation item requires object signal and risk_decision")
        plan.append((SignalIntent.from_dict(signal_data), RiskDecision.from_dict(decision_data)))
    return plan


def summarize_simulation_results(results: list[SimulationResult]) -> dict[str, int]:
    """Build status counts for simulation output without mixing replay expectancy."""
    summary: dict[str, int] = {
        "total": len(results),
        "expectancy_eligible": sum(1 for result in results if result.expectancy_eligible),
    }
    for result in results:
        summary[result.status] = summary.get(result.status, 0) + 1
    return summary


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


def cmd_candidates_build_from_raw(args: argparse.Namespace) -> None:
    """Build ranked candidates from FinMind raw JSONL cache."""
    result = build_candidates_from_raw_cache(
        cache_dir=Path(args.cache_dir),
        trading_date=args.date,
        limit=args.limit,
        min_trading_money=args.min_trading_money,
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-candidates.json"
    write_json(output, result.to_payload())
    print(output)


def cmd_report_daily(args: argparse.Namespace) -> None:
    """Build the daily candidate report, optionally including sample counts."""
    input_path = Path(args.input)
    candidates = load_candidate_scores(input_path)
    source_summary = load_candidate_source_summary(input_path)
    sample_summary = load_sample_summary(Path(args.samples)) if args.samples else None
    if args.format == "html":
        content = render_html(
            args.date,
            candidates,
            sample_summary=sample_summary,
            source_summary=source_summary,
        )
        default_suffix = "html"
    else:
        content = render_markdown(
            args.date,
            candidates,
            sample_summary=sample_summary,
            source_summary=source_summary,
        )
        default_suffix = "md"
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-daily.{default_suffix}"
    write_text(output, content)
    print(output)


def cmd_report_close(args: argparse.Namespace) -> None:
    """Build the daily close report from candidate, replay, and simulation outputs."""
    candidate_path = Path(args.candidates)
    replay_summary = load_required_payload_summary(Path(args.replay), "replay") if args.replay else None
    simulation_path = Path(args.simulation) if args.simulation else None
    simulation_summary = (
        load_required_payload_summary(simulation_path, "simulation") if simulation_path else None
    )
    candidates = load_candidate_scores(candidate_path)
    content = render_close_report_markdown(
        args.date,
        candidates,
        candidate_source_summary=load_candidate_source_summary(candidate_path),
        replay_summary=replay_summary,
        simulation_summary=simulation_summary,
        simulation_results=load_payload_results(simulation_path),
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-close.md"
    write_text(output, content)
    print(output)
    if args.telegram_summary_output:
        summary_output = Path(args.telegram_summary_output)
        write_text(
            summary_output,
            render_close_report_telegram_summary(
                args.date,
                candidates,
                replay_summary=replay_summary,
                simulation_summary=simulation_summary,
            ),
        )
        print(summary_output)


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


def cmd_replay_samples(args: argparse.Namespace) -> None:
    """Replay classified samples and write JSON plus an optional Markdown report."""
    result = replay_samples(
        load_replay_samples(Path(args.input)),
        assumptions=ReplayAssumptions(cost_r=args.cost_r),
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-replay.json"
    write_json(output, result.to_dict())
    print(output)
    if args.report_output:
        report_output = Path(args.report_output)
        write_text(report_output, render_replay_markdown(args.date, result))
        print(report_output)


def cmd_simulate_run(args: argparse.Namespace) -> None:
    """Run the dry-run Shioaji simulation adapter and write separate simulation output."""
    adapter = ShioajiSimulationAdapter(
        broker=DryRunSimulationBroker(),
        ledger=PaperLedger(),
    )
    sync_store = FileExecutionSyncStore(Path(args.execution_sync_store)) if args.execution_sync_store else None
    results = [
        adapter.execute(signal, decision)
        for signal, decision in load_simulation_plan(Path(args.input))
    ]
    if sync_store:
        for result in results:
            sync_store.record_result(result)
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-simulation.json"
    payload = {
        "trading_date": args.date,
        "sample_type": "simulation",
        "summary": summarize_simulation_results(results),
        "results": [result.to_dict() for result in results],
    }
    write_json(output, payload)
    print(output)
    if args.report_output:
        report_output = Path(args.report_output)
        write_text(report_output, render_simulation_markdown(args.date, results))
        print(report_output)


def cmd_simulate_restart_sync(args: argparse.Namespace) -> None:
    """Rebuild ledger state from persisted execution sync data and compare broker trades."""
    store = FileExecutionSyncStore(Path(args.store))
    snapshot = store.load_snapshot()
    positions = ledger_positions_from_payload(snapshot["open_positions"])
    ledger = restore_ledger_from_positions(positions)
    report = build_restart_sync_report(
        broker_trades_from_payload(snapshot["broker_trades"]),
        positions,
        ledger,
    )
    output = Path(args.output) if args.output else Path("reports") / "restart-sync.json"
    write_json(output, report)
    print(output)


def cmd_ingest_finmind(args: argparse.Namespace) -> None:
    """Run FinMind nightly ingestion with ledger-backed raw cache."""
    token = args.token or os.getenv("FINMIND_TOKEN")
    if args.requests:
        requests = read_request_file(Path(args.requests))
    else:
        requests = [
            build_single_request(
                dataset=args.dataset,
                trading_date=args.date,
                stock_id=args.stock_id or "market",
                start_date=args.start_date,
            )
        ]
    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=True)
    try:
        storage = DatabaseStorage(connection, dialect="tidb")
        client = FinMindDataLoaderClient(token) if token else _TokenMissingFinMindClient()
        summary = ingest_finmind_requests(
            repository=storage,
            cache_dir=Path(args.cache_dir),
            client=client,
            requests=requests,
            token=token,
            quota_limit=args.quota_limit,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        connection.close()


class _TokenMissingFinMindClient:
    def fetch_dataset(self, request):
        raise RuntimeError("FinMind token is required")


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
    build_from_raw = candidate_sub.add_parser("build-from-raw")
    build_from_raw.add_argument("--date", required=True)
    build_from_raw.add_argument("--cache-dir", default="data/raw")
    build_from_raw.add_argument("--output")
    build_from_raw.add_argument("--limit", type=int, default=80)
    build_from_raw.add_argument("--min-trading-money", type=float, default=80_000_000)
    build_from_raw.set_defaults(func=cmd_candidates_build_from_raw)
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
    close = report_sub.add_parser("close")
    close.add_argument("--date", required=True)
    close.add_argument("--candidates", required=True)
    close.add_argument("--replay")
    close.add_argument("--simulation")
    close.add_argument("--output")
    close.add_argument("--telegram-summary-output")
    close.set_defaults(func=cmd_report_close)

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

    replay = subparsers.add_parser("replay")
    replay_sub = replay.add_subparsers(required=True)
    replay_samples_parser = replay_sub.add_parser("samples")
    replay_samples_parser.add_argument("--date", required=True)
    replay_samples_parser.add_argument("--input", required=True)
    replay_samples_parser.add_argument("--output")
    replay_samples_parser.add_argument("--report-output")
    replay_samples_parser.add_argument("--cost-r", type=float, default=0.0)
    replay_samples_parser.set_defaults(func=cmd_replay_samples)

    simulate = subparsers.add_parser("simulate")
    simulate_sub = simulate.add_subparsers(required=True)
    simulate_run = simulate_sub.add_parser("run")
    simulate_run.add_argument("--date", required=True)
    simulate_run.add_argument("--input", required=True)
    simulate_run.add_argument("--output")
    simulate_run.add_argument("--report-output")
    simulate_run.add_argument("--execution-sync-store")
    simulate_run.set_defaults(func=cmd_simulate_run)
    restart_sync = simulate_sub.add_parser("restart-sync")
    restart_sync.add_argument("--store", required=True)
    restart_sync.add_argument("--output")
    restart_sync.set_defaults(func=cmd_simulate_restart_sync)

    ingest = subparsers.add_parser("ingest")
    ingest_sub = ingest.add_subparsers(required=True)
    finmind = ingest_sub.add_parser("finmind")
    finmind.add_argument("--date", required=True)
    finmind.add_argument("--dataset", default="TaiwanStockPrice")
    finmind.add_argument("--stock-id", default="market")
    finmind.add_argument("--start-date")
    finmind.add_argument("--requests")
    finmind.add_argument("--cache-dir", default="data/raw")
    finmind.add_argument("--quota-limit", type=int, default=540)
    finmind.add_argument("--token")
    finmind.set_defaults(func=cmd_ingest_finmind)

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point used by `python -m tw_day_trading_lab.cli`."""
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
