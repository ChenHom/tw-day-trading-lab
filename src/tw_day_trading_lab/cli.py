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
    ProductionReadinessPolicy,
    RiskDecision,
    ShioajiSimulationAdapter,
    ShioajiSdkSimulationGateway,
    SimulationResult,
    SignalIntent,
    broker_trades_from_payload,
    build_restart_sync_report,
    ledger_positions_from_payload,
    normalize_shioaji_order_callback,
    render_simulation_markdown,
    render_production_readiness_markdown,
    restore_ledger_from_positions,
    build_production_readiness_report,
    run_callback_sequence_smoke,
    run_gated_shioaji_callback_stream_smoke,
    run_gated_shioaji_cancel_smoke,
    run_gated_shioaji_order_request_smoke,
    run_gated_shioaji_simulation_login_smoke,
    build_simulation_plan,
    check_pre_order_gates,
    SimulationPlanItem,
    ShioajiCallbackStream,
    ShioajiOrderRequestBroker,
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

def get_git_commit() -> str:
    import subprocess
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def get_file_checksum(path: Path) -> str:
    import hashlib
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_simulate_ops_run(args: argparse.Namespace) -> None:
    """Run the daily simulation ops runner to generate candidate inputs, execute gates, place orders, check readiness, and produce run manifest."""
    import uuid
    from datetime import datetime
    import sys

    # 1. Generate run_id and metadata
    run_id = f"ops-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    created_at = datetime.now().isoformat()
    git_commit = get_git_commit()
    cwd = os.getcwd()

    # Mask manual approval tokens in the command line
    cmd_args = sys.argv.copy()
    for idx, arg in enumerate(cmd_args):
        if arg in ("--manual-approval-token", "--expected-manual-approval-token") and idx + 1 < len(cmd_args):
            cmd_args[idx + 1] = "********"
    command = " ".join(cmd_args)

    # Env sources (e.g. check which environment keys are present)
    env_keys = ["SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    env_sources = {k: "present" if os.getenv(k) else "missing" for k in env_keys}

    # Output directory setup
    output_dir = Path(args.output_dir) if args.output_dir else Path("reports") / f"{args.date}-ops"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check candidates input file
    candidates_path = Path(args.candidates_input)
    if not candidates_path.exists():
        raise FileNotFoundError(f"Candidates input not found: {candidates_path}")

    # Calculate input checksums
    input_artifacts = [
        {"path": str(candidates_path), "checksum": get_file_checksum(candidates_path)}
    ]

    # Load candidates
    candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidate_date = args.candidate_date or candidates_payload.get("trading_date")
    if not candidate_date:
        raise ValueError("candidate_date is required or must be present in the candidates input file")

    # Convert candidates items to CandidateScore objects
    candidates_data = candidates_payload.get("candidates", [])
    candidates_list = []
    for item in candidates_data:
        score_obj = CandidateScore(
            symbol=str(item["symbol"]),
            name=str(item.get("name", item["symbol"])),
            rank=int(item.get("rank", 0)),
            archetype=str(item.get("archetype", "theme_follower")),
            total_score=float(item.get("total_score", 0)),
            liquidity_score=float(item.get("liquidity_score", 0)),
            event_score=float(item.get("event_score", 0)),
            structure_score=float(item.get("structure_score", 0)),
            continuity_score=float(item.get("continuity_score", 0)),
            crowding_penalty=float(item.get("crowding_penalty", 0)),
            next_day_actionable=bool(item.get("next_day_actionable", False)),
            reasons=tuple(item.get("reasons", [])),
            downgrade_reasons=tuple(item.get("downgrade_reasons", [])),
        )
        candidates_list.append(score_obj)

    # 2. Build input plan using the plan builder contract
    cache_dir = Path(args.cache_dir)
    plan_items = build_simulation_plan(
        trading_date=args.date,
        candidate_date=candidate_date,
        candidates=candidates_list,
        cache_dir=cache_dir,
        candidate_source=str(candidates_path),
    )

    input_plan_path = output_dir / "input_plan.json"
    write_json(input_plan_path, [item.to_dict() for item in plan_items])
    input_artifacts.append(
        {"path": str(input_plan_path), "checksum": get_file_checksum(input_plan_path)}
    )

    # 3. Setup broker and adapter
    side_effects = []
    blocked_reasons = []

    # Determine if we are simulation on
    simulation_on = bool(args.simulation_on)
    if simulation_on:
        # Check environment variables
        api_key = os.getenv("SHIOAJI_API_KEY", "")
        secret_key = os.getenv("SHIOAJI_SECRET_KEY", "")
        if not api_key or not secret_key:
            raise ValueError("SHIOAJI_API_KEY and SHIOAJI_SECRET_KEY are required for simulation-on mode")

        import shioaji as sj  # type: ignore
        api = sj.Shioaji(simulation=True)
        gateway = ShioajiSdkSimulationGateway(api, api_key, secret_key)
        broker = ShioajiOrderRequestBroker(gateway)
        side_effects.append("login")
        side_effects.append("set_order_callback")
    else:
        broker = DryRunSimulationBroker()

    ledger = PaperLedger()
    adapter = ShioajiSimulationAdapter(broker=broker, ledger=ledger)

    # Store callback sync store setup
    sync_store_path = Path(args.execution_sync_store) if args.execution_sync_store else output_dir / "callback_store.json"
    if not sync_store_path.exists():
        write_json(sync_store_path, {
            "trading_date": args.date,
            "callback_events": [],
            "broker_trades": [],
            "lifecycle_decisions": [],
            "callback_ordering_issues": [],
            "cancel_results": [],
            "open_positions": [],
            "custom_field_map": {},
        })
    sync_store = FileExecutionSyncStore(sync_store_path)

    # In simulation-on mode, login and setup callback stream
    if simulation_on:
        session = adapter._ensure_session()
        stream = ShioajiCallbackStream(api=api, store=sync_store, trading_date=args.date)
        stream.start()

    # 4. Run the simulation adapter and check gates
    results = []
    for plan_item in plan_items:
        res = adapter.execute(
            plan_item.signal,
            plan_item.risk_decision,
            current_time=args.current_time,
            allow_outside_session=bool(args.allow_outside_session),
            bypass_gates=False,
        )
        results.append(res)

        if res.status == "simulated" or res.status == "submitted":
            side_effects.append(f"place_order_{res.signal.symbol}")
        elif res.status == "gate_blocked":
            blocked_reasons.append(f"symbol_{res.signal.symbol}_blocked_by_{res.review_reason}")

        sync_store.record_result(res)

    # Write simulation output
    simulation_output_path = output_dir / "simulation_output.json"
    payload = {
        "trading_date": args.date,
        "sample_type": "simulation",
        "summary": summarize_simulation_results(results),
        "results": [result.to_dict() for result in results],
    }
    write_json(simulation_output_path, payload)

    # 5. Run restart sync comparison
    snapshot = sync_store.load_snapshot()
    positions = ledger_positions_from_payload(snapshot["open_positions"])
    restored_ledger = restore_ledger_from_positions(positions)
    restart_sync_report = build_restart_sync_report(
        broker_trades_from_payload(snapshot["broker_trades"]),
        positions,
        restored_ledger,
    )
    restart_sync_path = output_dir / "restart_sync.json"
    write_json(restart_sync_path, restart_sync_report)

    # 6. Run readiness report
    readiness_policy = ProductionReadinessPolicy(
        trading_date=args.date,
        current_time=args.current_time,
        allow_live_trading=bool(args.allow_live_trading),
        manual_approval_token=args.manual_approval_token or "",
        expected_manual_approval_token=args.expected_manual_approval_token or "",
        max_pending_orders=getattr(args, "max_pending_orders", 0),
        require_cancel_retry_plan=not getattr(args, "disable_cancel_retry_plan", False),
    )
    readiness_report = build_production_readiness_report(snapshot, readiness_policy)
    readiness_report_path = output_dir / "readiness_report.json"
    write_json(readiness_report_path, readiness_report)

    readiness_md_path = output_dir / "readiness_report.md"
    write_text(readiness_md_path, render_production_readiness_markdown(readiness_report))

    # Also save the callback store snapshot in the output directory if it is distinct
    callback_store_backup_path = output_dir / "callback_store.json"
    if callback_store_backup_path != sync_store_path:
        write_json(callback_store_backup_path, snapshot)

    # Gather manual actions
    manual_actions = readiness_report.get("manual_actions", [])

    # 7. Collect output artifacts and checksums
    output_artifacts = [
        {"path": str(simulation_output_path), "checksum": get_file_checksum(simulation_output_path)},
        {"path": str(restart_sync_path), "checksum": get_file_checksum(restart_sync_path)},
        {"path": str(readiness_report_path), "checksum": get_file_checksum(readiness_report_path)},
        {"path": str(readiness_md_path), "checksum": get_file_checksum(readiness_md_path)},
        {"path": str(callback_store_backup_path), "checksum": get_file_checksum(callback_store_backup_path)},
    ]

    # Build manifest
    manifest = {
        "run_id": run_id,
        "trading_date": args.date,
        "created_at": created_at,
        "git_commit": git_commit,
        "cwd": cwd,
        "command": command,
        "env_sources": env_sources,
        "input_artifacts": input_artifacts,
        "output_artifacts": output_artifacts,
        "side_effects": side_effects,
        "blocked_reasons": blocked_reasons,
        "manual_actions": manual_actions,
        "simulation_only": True,
    }

    manifest_path = output_dir / "ops_run_manifest.json"
    write_json(manifest_path, manifest)

    print(f"Daily simulation ops run completed successfully!")
    print(f"Run ID: {run_id}")
    print(f"Manifest written to: {manifest_path}")



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


def cmd_simulate_ingest_callback(args: argparse.Namespace) -> None:
    """Normalize a Shioaji callback payload and append it to the execution sync store."""
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("callback input must be a JSON object")
    store = FileExecutionSyncStore(Path(args.store))
    snapshot = store.load_snapshot()
    event = normalize_shioaji_order_callback(
        raw.get("stat", ""),
        raw.get("msg", {}),
        trading_date=args.date,
        custom_field_map=snapshot["custom_field_map"],
    )
    store.record_callback_event(event)
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-callback-event.json"
    write_json(output, event.to_dict())
    print(output)


def cmd_simulate_callback_smoke(args: argparse.Namespace) -> None:
    """Replay a sequence of callback payloads through the execution sync store."""
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    callbacks = raw.get("callbacks", raw) if isinstance(raw, dict) else raw
    if not isinstance(callbacks, list):
        raise ValueError("callback smoke input must be a JSON list or contain callbacks")
    store = FileExecutionSyncStore(Path(args.store))
    report = run_callback_sequence_smoke(
        trading_date=args.date,
        callbacks=callbacks,
        store=store,
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-callback-smoke.json"
    write_json(output, report)
    print(output)


def _last_order_report_id(reports: list[dict[str, object]]) -> str:
    for report in reversed(reports):
        if report.get("mode") != "shioaji_order_request":
            continue
        result = report.get("result")
        if isinstance(result, dict):
            return str(result.get("broker_order_id") or "")
    return ""


def cmd_simulate_shioaji_smoke(args: argparse.Namespace) -> None:
    """Run explicitly gated Shioaji simulation smoke checks."""
    reports: list[dict[str, object]] = []
    api = None
    api_key = None
    secret_key = None
    sdk_gateway = None
    store = FileExecutionSyncStore(Path(args.store)) if args.store else None

    if args.enable_login_smoke:
        try:
            import shioaji as sj  # type: ignore
        except Exception as error:
            reports.append(
                {
                    "status": "blocked",
                    "mode": "shioaji_simulation_login",
                    "review_reason": "shioaji_import_failed",
                    "error": str(error),
                }
            )
        else:
            api_key = os.getenv(args.api_key_env)
            secret_key = os.getenv(args.secret_key_env)
            api = sj.Shioaji(simulation=True)
            if api_key and secret_key:
                sdk_gateway = ShioajiSdkSimulationGateway(
                    api=api,
                    api_key=api_key,
                    secret_key=secret_key,
                    fetch_contract=args.fetch_contract,
                    subscribe_trade=args.subscribe_trade,
                )
            reports.append(
                run_gated_shioaji_simulation_login_smoke(
                    api_factory=lambda simulation: api,
                    api_key=api_key,
                    secret_key=secret_key,
                    enabled=True,
                    fetch_contract=args.fetch_contract,
                    subscribe_trade=args.subscribe_trade,
                )
            )
    else:
        reports.append(
            run_gated_shioaji_simulation_login_smoke(
                api_factory=lambda **_: None,
                api_key=None,
                secret_key=None,
                enabled=False,
            )
        )

    if args.enable_callback_stream:
        if api is None:
            raise ValueError("callback stream smoke requires --enable-login-smoke first")
        if store is None:
            raise ValueError("callback stream smoke requires --store")
        reports.append(
            run_gated_shioaji_callback_stream_smoke(
                api=api,
                store=store,
                trading_date=args.date,
                enabled=True,
            )
        )
    else:
        reports.append(
            {
                "status": "blocked",
                "mode": "shioaji_callback_stream",
                "review_reason": "enable_callback_stream_required",
            }
        )

    if args.enable_order_smoke:
        login_ok = any(
            report.get("mode") == "shioaji_simulation_login" and report.get("status") == "ok"
            for report in reports
        )
        if not login_ok or sdk_gateway is None:
            reports.append(
                {
                    "status": "blocked",
                    "mode": "shioaji_order_request",
                    "review_reason": "successful_login_smoke_required",
                }
            )
        elif store is None:
            reports.append(
                {
                    "status": "blocked",
                    "mode": "shioaji_order_request",
                    "review_reason": "store_required",
                }
            )
        elif not args.input:
            reports.append(
                {
                    "status": "blocked",
                    "mode": "shioaji_order_request",
                    "review_reason": "input_plan_required",
                }
            )
        else:
            plan = load_simulation_plan(Path(args.input))
            if not plan:
                reports.append(
                    {
                        "status": "blocked",
                        "mode": "shioaji_order_request",
                        "review_reason": "input_plan_empty",
                    }
                )
            else:
                signal, decision = plan[0]
                reports.append(
                    run_gated_shioaji_order_request_smoke(
                        gateway=sdk_gateway,
                        store=store,
                        signal=signal,
                        decision=decision,
                        enabled=True,
                        max_quantity=args.max_order_quantity,
                        current_time=args.current_time,
                        allow_outside_session=args.allow_outside_session,
                    )
                )
    else:
        reports.append(
            {
                "status": "blocked",
                "mode": "shioaji_order_request",
                "review_reason": "enable_order_smoke_required",
            }
        )

    if args.enable_cancel_smoke:
        login_ok = any(
            report.get("mode") == "shioaji_simulation_login" and report.get("status") == "ok"
            for report in reports
        )
        if not login_ok or sdk_gateway is None:
            reports.append(
                {
                    "status": "blocked",
                    "mode": "shioaji_cancel_order",
                    "review_reason": "successful_login_smoke_required",
                }
            )
        else:
            cancel_broker_order_id = args.cancel_broker_order_id or _last_order_report_id(reports)
            reports.append(
                run_gated_shioaji_cancel_smoke(
                    gateway=sdk_gateway,
                    broker_order_id=cancel_broker_order_id,
                    enabled=True,
                    store=store,
                )
            )
    else:
        reports.append(
            {
                "status": "blocked",
                "mode": "shioaji_cancel_order",
                "review_reason": "enable_cancel_smoke_required",
            }
        )

    payload = {
        "trading_date": args.date,
        "summary": {
            "total": len(reports),
            "ok": sum(1 for report in reports if report.get("status") in {"ok", "registered"}),
            "blocked": sum(1 for report in reports if report.get("status") == "blocked"),
        },
        "reports": reports,
    }
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-shioaji-smoke.json"
    write_json(output, payload)
    print(output)


def cmd_simulate_production_readiness(args: argparse.Namespace) -> None:
    """Build the P7 production readiness gate report from execution sync state."""
    store = FileExecutionSyncStore(Path(args.store))
    expected_token = args.expected_manual_approval_token or f"{args.date}:LIVE-TRADING-APPROVED"
    report = build_production_readiness_report(
        store.load_snapshot(),
        ProductionReadinessPolicy(
            trading_date=args.date,
            current_time=args.current_time,
            allow_live_trading=args.allow_live_trading,
            manual_approval_token=args.manual_approval_token or "",
            expected_manual_approval_token=expected_token,
            max_pending_orders=args.max_pending_orders,
            require_cancel_retry_plan=not args.disable_cancel_retry_plan,
        ),
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-production-readiness.json"
    write_json(output, report)
    print(output)
    if args.report_output:
        report_output = Path(args.report_output)
        write_text(report_output, render_production_readiness_markdown(report))
        print(report_output)


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

    ops_run = simulate_sub.add_parser("ops-run")
    ops_run.add_argument("--date", required=True)
    ops_run.add_argument("--candidates-input", required=True)
    ops_run.add_argument("--candidate-date")
    ops_run.add_argument("--cache-dir", default="data/raw")
    ops_run.add_argument("--execution-sync-store")
    ops_run.add_argument("--output-dir")
    ops_run.add_argument("--simulation-on", action="store_true")
    ops_run.add_argument("--allow-outside-session", action="store_true")
    ops_run.add_argument("--current-time")
    ops_run.add_argument("--allow-live-trading", action="store_true")
    ops_run.add_argument("--manual-approval-token")
    ops_run.add_argument("--expected-manual-approval-token")
    ops_run.add_argument("--max-pending-orders", type=int, default=0)
    ops_run.add_argument("--disable-cancel-retry-plan", action="store_true")
    ops_run.set_defaults(func=cmd_simulate_ops_run)

    restart_sync = simulate_sub.add_parser("restart-sync")
    restart_sync.add_argument("--store", required=True)
    restart_sync.add_argument("--output")
    restart_sync.set_defaults(func=cmd_simulate_restart_sync)
    ingest_callback = simulate_sub.add_parser("ingest-callback")
    ingest_callback.add_argument("--date", required=True)
    ingest_callback.add_argument("--input", required=True)
    ingest_callback.add_argument("--store", required=True)
    ingest_callback.add_argument("--output")
    ingest_callback.set_defaults(func=cmd_simulate_ingest_callback)
    callback_smoke = simulate_sub.add_parser("callback-smoke")
    callback_smoke.add_argument("--date", required=True)
    callback_smoke.add_argument("--input", required=True)
    callback_smoke.add_argument("--store", required=True)
    callback_smoke.add_argument("--output")
    callback_smoke.set_defaults(func=cmd_simulate_callback_smoke)
    shioaji_smoke = simulate_sub.add_parser("shioaji-smoke")
    shioaji_smoke.add_argument("--date", required=True)
    shioaji_smoke.add_argument("--output")
    shioaji_smoke.add_argument("--store")
    shioaji_smoke.add_argument("--input")
    shioaji_smoke.add_argument("--api-key-env", default="SHIOAJI_API_KEY")
    shioaji_smoke.add_argument("--secret-key-env", default="SHIOAJI_SECRET_KEY")
    shioaji_smoke.add_argument("--enable-login-smoke", action="store_true")
    shioaji_smoke.add_argument("--enable-callback-stream", action="store_true")
    shioaji_smoke.add_argument("--enable-order-smoke", action="store_true")
    shioaji_smoke.add_argument("--enable-cancel-smoke", action="store_true")
    shioaji_smoke.add_argument("--cancel-broker-order-id")
    shioaji_smoke.add_argument("--max-order-quantity", type=int, default=1000)
    shioaji_smoke.add_argument("--current-time")
    shioaji_smoke.add_argument("--allow-outside-session", action="store_true")
    shioaji_smoke.add_argument("--fetch-contract", action="store_true")
    shioaji_smoke.add_argument("--subscribe-trade", action="store_true")
    shioaji_smoke.set_defaults(func=cmd_simulate_shioaji_smoke)
    production_readiness = simulate_sub.add_parser("production-readiness")
    production_readiness.add_argument("--date", required=True)
    production_readiness.add_argument("--store", required=True)
    production_readiness.add_argument("--output")
    production_readiness.add_argument("--report-output")
    production_readiness.add_argument("--current-time")
    production_readiness.add_argument("--allow-live-trading", action="store_true")
    production_readiness.add_argument("--manual-approval-token")
    production_readiness.add_argument("--expected-manual-approval-token")
    production_readiness.add_argument("--max-pending-orders", type=int, default=0)
    production_readiness.add_argument("--disable-cancel-retry-plan", action="store_true")
    production_readiness.set_defaults(func=cmd_simulate_production_readiness)

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
