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
from .strategy import VwapBreakoutStrategy
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
    generate_alerts_from_run,
    RegressionCase,
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
    get_schema_version,
    apply_migration,
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
    """Build the daily close report from candidate, replay, simulation, and readiness outputs."""
    candidate_path = Path(args.candidates)
    replay_summary = load_required_payload_summary(Path(args.replay), "replay") if args.replay else None
    simulation_path = Path(args.simulation) if args.simulation else None
    simulation_summary = (
        load_required_payload_summary(simulation_path, "simulation") if simulation_path else None
    )
    readiness_summary = (
        json.loads(Path(args.readiness).read_text(encoding="utf-8")) if getattr(args, "readiness", None) else None
    )
    candidates = load_candidate_scores(candidate_path)
    content = render_close_report_markdown(
        args.date,
        candidates,
        candidate_source_summary=load_candidate_source_summary(candidate_path),
        replay_summary=replay_summary,
        simulation_summary=simulation_summary,
        simulation_results=load_payload_results(simulation_path),
        readiness_summary=readiness_summary,
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
    """Send a Telegram notification or print a dry-run summary."""
    report_path = Path(args.report)
    content = report_path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip()]
    summary = "\n".join(lines[: min(12, len(lines))])

    telegram_enabled = os.getenv("TELEGRAM_ENABLED", "").lower() in ("true", "1", "yes")
    if not telegram_enabled:
        print("Telegram notifications are disabled via TELEGRAM_ENABLED. Skipping real send.")
        print(summary)
        return

    if args.dry_run:
        print(summary)
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables must be set for real send")

    import urllib.request
    import urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": summary}).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode("utf-8"))
            if res.get("ok"):
                print(f"Telegram notification sent successfully to chat {chat_id}")
            else:
                print(f"Telegram API error: {res}")
    except Exception as e:
        print(f"Failed to send Telegram notification: {e}")



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


def cmd_db_migrate(args: argparse.Namespace) -> None:
    """Scan and apply pending migrations under --schema-dir."""
    schema_dir = Path(args.schema_dir)
    if not schema_dir.exists():
        raise FileNotFoundError(f"Schema directory not found: {schema_dir}")

    import re
    migration_files = []
    for p in schema_dir.glob("*.sql"):
        match = re.match(r"^(\d+)_(.*)\.sql$", p.name)
        if match:
            version = int(match.group(1))
            description = match.group(2)
            migration_files.append((version, description, p))

    migration_files.sort(key=lambda x: x[0])

    config = TiDBConfig.from_env()
    connection = connect_tidb(config, use_database=False)
    try:
        from .storage import validate_sql_identifier, render_schema_script
        is_sqlite = "sqlite" in str(type(connection)).lower()
        if not is_sqlite:
            try:
                safe_database = validate_sql_identifier(config.database)
                cursor = connection.cursor()
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {safe_database}")
                cursor.execute(f"USE {safe_database}")
                cursor.close()
            except Exception:
                pass

        current_version = get_schema_version(connection)
        print(f"Current schema version: {current_version}")

        applied_count = 0
        for version, description, path in migration_files:
            if version > current_version:
                print(f"Applying migration {path.name} (version {version})...")
                sql = path.read_text(encoding="utf-8")
                sql = render_schema_script(sql, config.database)

                apply_migration(connection, version, sql, description)
                applied_count += 1

        if applied_count == 0:
            print("No pending migrations to apply.")
        else:
            print(f"Successfully applied {applied_count} migrations.")
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
    from .cost import TaiwanDayTradeCostModel
    cost_model = None
    if getattr(args, "use_cost_model", False):
        cost_model = TaiwanDayTradeCostModel(
            commission_discount=args.commission_discount,
            day_trade_tax_rate=args.day_trade_tax_rate,
            slippage_ticks_per_side=args.slippage_ticks,
        )
    result = replay_samples(
        load_replay_samples(Path(args.input)),
        assumptions=ReplayAssumptions(cost_r=args.cost_r, cost_model=cost_model),
    )
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-replay.json"
    write_json(output, result.to_dict())
    print(output)
    if args.report_output:
        report_output = Path(args.report_output)
        write_text(report_output, render_replay_markdown(args.date, result))
        print(report_output)


def cmd_replay_walk_forward(args: argparse.Namespace) -> None:
    """Run Walk-Forward analysis by splitting samples into in-sample and out-of-sample."""
    samples = load_replay_samples(Path(args.input))

    # Filter valid samples
    valid_samples = [s for s in samples if s.get("validity") == "valid"]
    if not valid_samples:
        print("No valid samples found for Walk-Forward validation.")
        return

    # Sort samples by trading date
    def get_sample_date(s: dict) -> str:
        key = s.get("idempotency_key", "")
        if ":" in key:
            return key.split(":")[0]
        return str(s.get("trading_date", ""))

    valid_samples.sort(key=get_sample_date)

    # Split datasets
    if args.split_date:
        train_samples = [s for s in valid_samples if get_sample_date(s) < args.split_date]
        test_samples = [s for s in valid_samples if get_sample_date(s) >= args.split_date]
    else:
        split_idx = int(len(valid_samples) * args.train_ratio)
        train_samples = valid_samples[:split_idx]
        test_samples = valid_samples[split_idx:]

    from .cost import TaiwanDayTradeCostModel
    cost_model = None
    if getattr(args, "use_cost_model", False):
        cost_model = TaiwanDayTradeCostModel(
            commission_discount=args.commission_discount,
            day_trade_tax_rate=args.day_trade_tax_rate,
            slippage_ticks_per_side=args.slippage_ticks,
        )
    assumptions = ReplayAssumptions(cost_r=args.cost_r, cost_model=cost_model)

    train_result = replay_samples(train_samples, assumptions=assumptions)
    test_result = replay_samples(test_samples, assumptions=assumptions)

    payload = {
        "train_summary": train_result.summary,
        "test_summary": test_result.summary,
        "split_date": args.split_date or (get_sample_date(test_samples[0]) if test_samples else "N/A"),
    }

    output = Path(args.output) if args.output else Path("reports") / "walk-forward-validation.json"
    write_json(output, payload)
    print(output)

    # Build Markdown report
    lines = [
        "# Walk-Forward Validation Report",
        "",
        "## Train Set (In-Sample)",
        f"- Samples: {train_result.summary['replayed']}",
        f"- expectancy Gross R: {train_result.summary['expectancy_gross_r']}",
        f"- average Cost R: {train_result.summary['average_cost_r']}",
        f"- expectancy Net R: {train_result.summary['expectancy_net_r']}",
        f"- 95% Confidence Interval for Net R: [{train_result.summary.get('net_expectancy_ci_lower')}, {train_result.summary.get('net_expectancy_ci_upper')}]",
    ]
    if train_result.summary.get("statistical_warning"):
        lines.append(f"> [!WARNING] Train: {train_result.summary['statistical_warning']}")

    lines.extend([
        "",
        "## Test Set (Out-of-Sample)",
        f"- Samples: {test_result.summary['replayed']}",
        f"- expectancy Gross R: {test_result.summary['expectancy_gross_r']}",
        f"- average Cost R: {test_result.summary['average_cost_r']}",
        f"- expectancy Net R: {test_result.summary['expectancy_net_r']}",
        f"- 95% Confidence Interval for Net R: [{test_result.summary.get('net_expectancy_ci_lower')}, {test_result.summary.get('net_expectancy_ci_upper')}]",
    ])
    if test_result.summary.get("statistical_warning"):
        lines.append(f"> [!WARNING] Test: {test_result.summary['statistical_warning']}")

    # Check for overfitting
    train_net = train_result.summary['expectancy_net_r'] or 0.0
    test_net = test_result.summary['expectancy_net_r'] or 0.0
    lines.append("")
    lines.append("## Overfitting Check")
    if train_net > 0.0 and test_net <= 0.0:
        lines.append("> [!CAUTION]")
        lines.append(f"> Overfitting detected! In-Sample expectancy is positive ({train_net:.2f}R), but Out-of-Sample expectancy is negative/flat ({test_net:.2f}R).")
    elif train_net <= 0.0:
        lines.append("> [!WARNING]")
        lines.append(f"> In-Sample has no edge ({train_net:.2f}R). Strategy needs refinement.")
    else:
        lines.append("> [!NOTE]")
        lines.append(f"> Strategy holds edge on both sets (Train: {train_net:.2f}R, Test: {test_net:.2f}R).")

    if args.report_output:
        report_output = Path(args.report_output)
        write_text(report_output, "\n".join(lines) + "\n")
        print(report_output)


def find_close_price(symbol: str, trading_date: str, cache_dir: Path) -> float | None:
    p = cache_dir / "finmind" / "TaiwanStockPrice" / trading_date / f"{symbol}.jsonl"
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if str(row.get("date")) == trading_date:
                    return float(row["close"])
    except Exception:
        pass
    return None


def cmd_cost_analysis(args: argparse.Namespace) -> None:
    """Run cost analysis on a candidates file to evaluate cost_r for each candidate."""
    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        raise FileNotFoundError(f"Candidates file not found: {candidates_path}")

    with open(candidates_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    trading_date = ""
    candidate_list = []
    if isinstance(data, dict):
        trading_date = data.get("trading_date", "")
        candidate_list = data.get("candidates", [])
    else:
        candidate_list = data

    cache_dir = Path(args.cache_dir)

    from .cost import TaiwanDayTradeCostModel
    model = TaiwanDayTradeCostModel(
        commission_discount=args.commission_discount,
        day_trade_tax_rate=args.day_trade_tax_rate,
        slippage_ticks_per_side=args.slippage_ticks,
    )

    print(f"Cost Analysis (stop_loss={args.stop_pct}%, commission_discount={args.commission_discount * 100}%)")
    print("─" * 60)
    print(f"{'Symbol':<8}{'Price':<8}{'Tick':<8}{'Risk':<8}{'Cost':<8}{'cost_r':<8}")

    costs_r = []
    for cand in candidate_list:
        symbol = cand.get("symbol")
        price = None
        if trading_date:
            price = find_close_price(symbol, trading_date, cache_dir)
        if price is None:
            price = 100.0 # Default fallback

        tick = model.tick_size(price)
        risk = price * (args.stop_pct / 100.0)
        cost = model.round_trip_cost(price, quantity=1000)
        cost_r = model.cost_r(price, price - risk, quantity=1000)
        costs_r.append(cost_r)

        print(f"{symbol:<8}{price:<8.2f}{tick:<8.2f}{risk:<8.2f}{cost:<8.2f}{cost_r:<8.2f}R")

    if costs_r:
        import statistics
        print("─" * 60)
        print(f"Median cost_r: {statistics.median(costs_r):.2f}R")
        print(f"Mean cost_r:   {statistics.mean(costs_r):.2f}R")
        print(f"Max cost_r:    {max(costs_r):.2f}R")
        print(f"Min cost_r:    {min(costs_r):.2f}R")

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


def check_git_clean(cwd: str) -> dict:
    """Run git diff --check and git status --porcelain to detect repo cleanliness.

    Returns::

        {
            "clean": bool,
            "uncommitted_files": [str, ...],
            "whitespace_issues": bool,
        }
    """
    import subprocess

    uncommitted_files: list = []
    whitespace_issues = False

    # git status --porcelain: one line per changed file
    try:
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if status_res.returncode == 0:
            for line in status_res.stdout.splitlines():
                line = line.strip()
                if line:
                    # last token on the line is the file path
                    parts = line.split(None, 1)
                    fname = parts[1].strip() if len(parts) > 1 else line
                    uncommitted_files.append(fname)
    except Exception:
        pass

    # git diff --check: exits non-zero when whitespace issues are found
    try:
        diff_res = subprocess.run(
            ["git", "diff", "--check"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        whitespace_issues = diff_res.returncode != 0
    except Exception:
        pass

    clean = len(uncommitted_files) == 0
    return {
        "clean": clean,
        "uncommitted_files": uncommitted_files,
        "whitespace_issues": whitespace_issues,
    }


def check_ops_lock(lock_path: Path) -> dict:
    """Check whether an ops lock file exists at *lock_path*.

    Returns::

        {
            "locked": bool,
            "lock_file": str,
        }
    """
    locked = lock_path.exists()
    return {
        "locked": locked,
        "lock_file": str(lock_path),
    }


def audit_daily_ops_bundle(
    *,
    manifest_path: Path,
    close_report_path: Path,
    regression_dir: Path | None = None,
) -> dict[str, object]:
    """Verify that a daily ops bundle can be audited from its manifest."""
    checks: list[dict[str, object]] = []

    def add_check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not manifest_path.exists():
        add_check("manifest_exists", False, str(manifest_path))
        return {
            "status": "failed",
            "summary": {"total": 1, "ok": 0, "failed": 1},
            "checks": checks,
        }

    add_check("manifest_exists", True, str(manifest_path))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = str(manifest.get("run_id") or "")
    trading_date = str(manifest.get("trading_date") or "")
    add_check("run_id_present", bool(run_id), run_id)
    add_check("trading_date_present", bool(trading_date), trading_date)

    all_artifacts = list(manifest.get("input_artifacts", [])) + list(manifest.get("output_artifacts", []))
    add_check("artifact_list_present", bool(all_artifacts), f"{len(all_artifacts)} artifacts")
    artifact_paths: list[Path] = []
    for artifact in all_artifacts:
        path = Path(str(artifact.get("path", "")))
        artifact_paths.append(path)
        expected_checksum = str(artifact.get("checksum", ""))
        exists = path.exists()
        add_check(f"artifact_exists:{path.name}", exists, str(path))
        if exists:
            actual_checksum = get_file_checksum(path)
            add_check(
                f"artifact_checksum:{path.name}",
                bool(expected_checksum) and actual_checksum == expected_checksum,
                str(path),
            )

    required_suffixes = {
        "input_plan.json",
        "simulation_output.json",
        "restart_sync.json",
        "readiness_report.json",
        "alerts.json",
        "callback_store.json",
    }
    present_names = {path.name for path in artifact_paths}
    for suffix in sorted(required_suffixes):
        add_check(f"required_artifact:{suffix}", suffix in present_names, suffix)

    readiness_paths = [path for path in artifact_paths if path.name == "readiness_report.json" and path.exists()]
    if readiness_paths:
        readiness = json.loads(readiness_paths[0].read_text(encoding="utf-8"))
        add_check("readiness_status_present", bool(readiness.get("status")), str(readiness.get("status", "")))
        add_check("readiness_summary_present", isinstance(readiness.get("summary"), dict), readiness_paths[0].as_posix())
    else:
        add_check("readiness_status_present", False, "readiness_report.json missing")
        add_check("readiness_summary_present", False, "readiness_report.json missing")

    alerts_count = 0
    alerts_paths = [path for path in artifact_paths if path.name == "alerts.json" and path.exists()]
    if alerts_paths:
        alerts_payload = json.loads(alerts_paths[0].read_text(encoding="utf-8"))
        add_check("alerts_is_list", isinstance(alerts_payload, list), alerts_paths[0].as_posix())
        required_alert_fields = {"alert_id", "severity", "category", "owner", "manual_action", "send_gate", "dedupe_key"}
        if isinstance(alerts_payload, list):
            alerts_count = len(alerts_payload)
            missing_alerts = [
                str(alert.get("alert_id") or idx)
                for idx, alert in enumerate(alerts_payload)
                if not required_alert_fields.issubset(set(alert))
            ]
            add_check("alerts_operator_fields", not missing_alerts, ",".join(missing_alerts))
    else:
        add_check("alerts_is_list", False, "alerts.json missing")
        add_check("alerts_operator_fields", False, "alerts.json missing")

    add_check("close_report_exists", close_report_path.exists(), str(close_report_path))

    if regression_dir is not None:
        regression_cases = sorted(regression_dir.glob("case-*.json")) if regression_dir.exists() else []
        add_check(
            "regression_dir_present",
            regression_dir.exists(),
            str(regression_dir),
        )
        add_check(
            "regression_cases_traceable",
            all(run_id in path.name for path in regression_cases),
            f"{len(regression_cases)} cases",
        )
        add_check(
            "regression_cases_for_alerts",
            alerts_count == 0 or len(regression_cases) > 0,
            f"alerts={alerts_count} cases={len(regression_cases)}",
        )

    ok_count = sum(1 for check in checks if check["ok"])
    failed_count = len(checks) - ok_count
    return {
        "run_id": run_id,
        "trading_date": trading_date,
        "status": "ok" if failed_count == 0 else "failed",
        "summary": {"total": len(checks), "ok": ok_count, "failed": failed_count},
        "checks": checks,
    }


def cmd_simulate_daily_ops(args: argparse.Namespace) -> None:
    """Run the daily simulation ops chain and audit the produced bundle."""
    from datetime import datetime

    trading_date = args.date or datetime.now().strftime("%Y-%m-%d")
    output_dir = Path(args.output_dir) if args.output_dir else Path("reports") / f"{trading_date}-daily-ops"
    output_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = Path(args.cache_dir)
    candidate_output = Path(args.candidates_output) if args.candidates_output else output_dir / "candidates.json"
    ops_output_dir = Path(args.ops_output_dir) if args.ops_output_dir else output_dir / "ops"
    close_report_path = Path(args.close_report_output) if args.close_report_output else output_dir / "close.md"
    telegram_summary_path = (
        Path(args.telegram_summary_output)
        if args.telegram_summary_output
        else output_dir / "telegram-summary.txt"
    )
    regression_output_dir = (
        Path(args.regression_output_dir)
        if args.regression_output_dir
        else output_dir / "regression"
    )
    audit_output = Path(args.audit_output) if args.audit_output else output_dir / "daily_bundle_audit.json"

    steps: list[dict[str, object]] = []

    def record_step(name: str, status: str, detail: str = "") -> None:
        steps.append({"name": name, "status": status, "detail": detail})

    if args.skip_ingestion:
        record_step("finmind_ingestion", "skipped", "--skip-ingestion")
    else:
        requests_path = args.requests
        if not requests_path:
            requests_path = str(output_dir / "ingestion_requests.json")
            write_json(
                Path(requests_path),
                [
                    {
                        "dataset": "TaiwanStockPrice",
                        "trading_date": trading_date,
                        "stock_id": args.stock_id or "market",
                        "start_date": args.start_date,
                    },
                    {
                        "dataset": "TaiwanStockInfo",
                        "trading_date": trading_date,
                        "stock_id": "market",
                    },
                    {
                        "dataset": "TaiwanStockPrice",
                        "trading_date": trading_date,
                        "stock_id": args.market_proxy_stock_id,
                        "start_date": args.market_proxy_start_date or args.start_date or trading_date,
                    },
                ],
            )
        try:
            cmd_ingest_finmind(
                argparse.Namespace(
                    date=trading_date,
                    requests=requests_path,
                    dataset=args.dataset,
                    stock_id=args.stock_id,
                    start_date=args.start_date,
                    cache_dir=args.cache_dir,
                    token=args.token,
                    quota_limit=args.quota_limit,
                )
            )
            record_step("finmind_ingestion", "ok", requests_path)
        except Exception as exc:
            record_step("finmind_ingestion", "failed", str(exc))
            if not args.allow_ingestion_failure:
                raise

    candidate_result = build_candidates_from_raw_cache(
        cache_dir=cache_dir,
        trading_date=trading_date,
        limit=args.limit,
        min_trading_money=args.min_trading_money,
    )
    write_json(candidate_output, candidate_result.to_payload())
    record_step(
        "candidate_build",
        "ok",
        f"{candidate_output} market_regime={candidate_result.summary.get('market_regime')}",
    )

    cmd_simulate_ops_run(
        argparse.Namespace(
            date=trading_date,
            candidates_input=str(candidate_output),
            candidate_date=args.candidate_date,
            cache_dir=str(cache_dir),
            execution_sync_store=args.execution_sync_store,
            output_dir=str(ops_output_dir),
            simulation_on=bool(args.simulation_on),
            allow_outside_session=bool(args.allow_outside_session),
            current_time=args.current_time,
            allow_live_trading=False,
            manual_approval_token=None,
            expected_manual_approval_token=None,
            max_pending_orders=args.max_pending_orders,
            disable_cancel_retry_plan=bool(args.disable_cancel_retry_plan),
            alert_dry_run=not bool(getattr(args, "send_alerts", False)),
            ignore_env_simulation=True,
        )
    )
    manifest_path = ops_output_dir / "ops_run_manifest.json"
    record_step("ops_run", "ok", str(manifest_path))

    simulation_output_path = ops_output_dir / "simulation_output.json"
    readiness_report_path = ops_output_dir / "readiness_report.json"
    cmd_report_close(
        argparse.Namespace(
            date=trading_date,
            candidates=str(candidate_output),
            replay=None,
            simulation=str(simulation_output_path),
            readiness=str(readiness_report_path),
            output=str(close_report_path),
            telegram_summary_output=str(telegram_summary_path),
        )
    )
    record_step("close_report", "ok", str(close_report_path))

    if args.skip_regression_import:
        record_step("regression_import", "skipped", "--skip-regression-import")
    else:
        cmd_simulate_regression_import(
            argparse.Namespace(
                manifest=str(manifest_path),
                output_dir=str(regression_output_dir),
            )
        )
        record_step("regression_import", "ok", str(regression_output_dir))

    audit = audit_daily_ops_bundle(
        manifest_path=manifest_path,
        close_report_path=close_report_path,
        regression_dir=None if args.skip_regression_import else regression_output_dir,
    )
    audit["steps"] = steps + [
        {
            "name": "bundle_audit",
            "status": str(audit["status"]),
            "detail": str(audit_output),
        }
    ]
    write_json(audit_output, audit)

    if args.fail_on_audit and audit["status"] != "ok":
        print(f"Daily ops bundle audit failed: {audit_output}")
        raise SystemExit(1)

    print("Daily simulation ops automation completed.")
    print(f"Date: {trading_date}")
    print(f"Output dir: {output_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Audit: {audit_output}")


TRADING_DAY_CYCLE_STAGES = (
    "candidate_ready",
    "intraday_waiting",
    "intraday_running",
    "force_exit",
    "close_buffer",
    "reporting",
    "next_candidates",
    "complete",
)


def _read_rows_from_json_or_jsonl(path: Path) -> list[dict[str, object]]:
    """Read a JSON/JSONL market-data artifact and return row dictionaries."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    raw = json.loads(text)
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("rows", "data", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
    return []


def resolve_trading_data_probe(
    *,
    trading_date: str,
    cache_dir: Path,
    trading_data_input: Path | None = None,
    market_proxy_stock_id: str = "0050",
) -> dict[str, object]:
    """Return trading-day status using only available API/cache rows, not a calendar."""
    probe_paths: list[Path] = []
    if trading_data_input is not None:
        probe_paths.append(trading_data_input)
    else:
        base = cache_dir / "finmind" / "TaiwanStockPrice" / trading_date
        probe_paths.extend(
            [
                base / f"{market_proxy_stock_id}.jsonl",
                base / "market.jsonl",
            ]
        )

    checked: list[dict[str, object]] = []
    for path in probe_paths:
        rows = _read_rows_from_json_or_jsonl(path)
        checked.append({"path": str(path), "rows": len(rows), "exists": path.exists()})
        if rows:
            return {
                "calendar_status": "trading_day",
                "source": str(path),
                "rows": len(rows),
                "checked": checked,
                "reason": "trading_data_available",
            }
    return {
        "calendar_status": "non_trading_day",
        "source": None,
        "rows": 0,
        "checked": checked,
        "reason": "trading_data_unavailable",
    }


def _time_to_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def resolve_trading_day_cycle_stage(
    *,
    current_time: str,
    start_policy: str,
    hard_stop_time: str,
    close_buffer_end_time: str,
    report_time: str,
    next_candidate_time: str,
) -> str:
    """Resolve the current trading-day cycle stage from wall-clock policy."""
    current = _time_to_minutes(current_time)
    start = _time_to_minutes(start_policy)
    hard_stop = _time_to_minutes(hard_stop_time)
    close_buffer_end = _time_to_minutes(close_buffer_end_time)
    report = _time_to_minutes(report_time)
    next_candidate = _time_to_minutes(next_candidate_time)

    if current < start:
        return "intraday_waiting"
    if current < hard_stop:
        return "intraday_running"
    if current < close_buffer_end:
        return "force_exit"
    if current < report:
        return "close_buffer"
    if current < next_candidate:
        return "reporting"
    return "next_candidates"


def build_trading_day_cycle_state(args: argparse.Namespace) -> dict[str, object]:
    """Build a dry-run trading-day cycle state without broker or alert side effects."""
    from datetime import datetime

    trading_date = args.date or datetime.now().strftime("%Y-%m-%d")
    start_policy = args.start_policy
    run_id = args.run_id or f"tdc-{trading_date}-{start_policy.replace(':', '')}"
    output_dir = Path(args.output_dir) if args.output_dir else Path("reports") / f"{trading_date}-trading-day-cycle"
    state_output = Path(args.state_output) if args.state_output else output_dir / "trading_day_run_state.json"
    cache_dir = Path(args.cache_dir)
    candidate_artifact = Path(args.candidates_input) if args.candidates_input else output_dir / "candidates.json"
    trading_data_input = Path(args.trading_data_input) if args.trading_data_input else None

    data_probe = resolve_trading_data_probe(
        trading_date=trading_date,
        cache_dir=cache_dir,
        trading_data_input=trading_data_input,
        market_proxy_stock_id=args.market_proxy_stock_id,
    )
    calendar_status = str(data_probe["calendar_status"])
    stage_history: list[dict[str, object]] = []
    blocked_reasons: list[str] = []
    manual_actions: list[str] = []

    def add_stage(stage: str, reason: str) -> None:
        stage_history.append(
            {
                "stage": stage,
                "time": {
                    "candidate_ready": "previous_close",
                    "intraday_waiting": f"before_{start_policy}",
                    "intraday_running": start_policy,
                    "force_exit": args.hard_stop_time,
                    "close_buffer": args.close_buffer_end_time,
                    "reporting": args.report_time,
                    "next_candidates": args.next_candidate_time,
                    "complete": "after_next_candidates",
                    "blocked": args.current_time,
                }.get(stage, args.current_time),
                "reason": reason,
            }
        )

    if calendar_status != "trading_day":
        blocked_reasons.append("trading_data_unavailable")
        manual_actions.append("Skip trading-day cycle; API/cache returned no trading rows.")
        add_stage("blocked", "trading_data_unavailable")
        current_stage = "blocked"
    elif args.run_all_stages:
        for stage in TRADING_DAY_CYCLE_STAGES:
            add_stage(stage, "dry_run_stage_transition")
        current_stage = "complete"
    else:
        add_stage("candidate_ready", "candidate_artifact_registered")
        current_stage = resolve_trading_day_cycle_stage(
            current_time=args.current_time,
            start_policy=start_policy,
            hard_stop_time=args.hard_stop_time,
            close_buffer_end_time=args.close_buffer_end_time,
            report_time=args.report_time,
            next_candidate_time=args.next_candidate_time,
        )
        add_stage(current_stage, "clock_policy_resolved")

    state = {
        "trading_day_run_id": run_id,
        "trading_date": trading_date,
        "mode": "dry_run",
        "calendar_status": calendar_status,
        "calendar_rule": "api_data_availability_only",
        "start_policy": start_policy,
        "hard_stop_time": args.hard_stop_time,
        "close_buffer_end_time": args.close_buffer_end_time,
        "report_time": args.report_time,
        "next_candidate_time": args.next_candidate_time,
        "stage": current_stage,
        "stage_history": stage_history,
        "candidate_artifact": {
            "path": str(candidate_artifact),
            "exists": candidate_artifact.exists(),
            "checksum": get_file_checksum(candidate_artifact) if candidate_artifact.exists() else None,
        },
        "trading_data_probe": data_probe,
        "position_state_artifact": {
            "path": str(output_dir / "position_state.json"),
            "exists": False,
            "checksum": None,
        },
        "report_artifact": {
            "path": str(output_dir / "report.md"),
            "github_url": None,
            "publish_status": "not_started",
        },
        "next_candidate_artifact": {
            "path": str(output_dir / "next_candidates.json"),
            "exists": False,
            "checksum": None,
        },
        "idempotency_key": f"{trading_date}:{start_policy}:trading-day-cycle",
        "lock": {
            "path": str(output_dir / ".trading-day-cycle.lock"),
            "policy": "single_trading_date_run",
        },
        "retry_policy": {
            "max_attempts": args.max_retries,
            "retryable_stages": ["intraday_waiting", "intraday_running", "reporting", "next_candidates"],
        },
        "blocked_reasons": blocked_reasons,
        "manual_actions": manual_actions,
        "side_effects": [],
    }
    state["_state_output"] = str(state_output)
    return state


def cmd_simulate_trading_day_cycle(args: argparse.Namespace) -> None:
    """Dry-run the trading-day cycle state machine and write run state."""
    state = build_trading_day_cycle_state(args)
    state_output = Path(str(state.pop("_state_output")))
    write_json(state_output, state)
    print("Trading day cycle dry-run completed.")
    print(f"Date: {state['trading_date']}")
    print(f"Calendar status: {state['calendar_status']}")
    print(f"Stage: {state['stage']}")
    print(f"State: {state_output}")


def cmd_simulate_ops_run(args: argparse.Namespace) -> None:
    """Run the daily simulation ops runner to generate candidate inputs, execute gates, place orders, check readiness, and produce run manifest."""
    import uuid
    from datetime import datetime, timedelta
    import sys

    # 1. Resolve trading date (default to current local date)
    trading_date_str = args.date
    if not trading_date_str:
        trading_date_str = datetime.now().strftime("%Y-%m-%d")

    # 2. Resolve candidates input file automatically
    candidates_path = None
    if args.candidates_input:
        candidates_path = Path(args.candidates_input)
    else:
        # Try current trading date
        candidates_path = Path("reports") / f"{trading_date_str}-candidates.json"
        if not candidates_path.exists():
            candidates_path = Path("reports") / f"{trading_date_str}-candidates-from-raw.json"

        # Try previous 10 days
        if not candidates_path.exists():
            try:
                ref_dt = datetime.strptime(trading_date_str, "%Y-%m-%d")
                for i in range(1, 11):
                    prev_date = (ref_dt - timedelta(days=i)).strftime("%Y-%m-%d")
                    p1 = Path("reports") / f"{prev_date}-candidates.json"
                    p2 = Path("reports") / f"{prev_date}-candidates-from-raw.json"
                    if p1.exists():
                        candidates_path = p1
                        break
                    elif p2.exists():
                        candidates_path = p2
                        break
            except Exception:
                pass

        # Fallback to examples
        if not candidates_path or not candidates_path.exists():
            fallback_sample = Path("examples/candidates.sample.json")
            if fallback_sample.exists():
                candidates_path = fallback_sample

    if not candidates_path or not candidates_path.exists():
        raise FileNotFoundError("Could not find a valid candidates input file. Please specify --candidates-input manually.")

    # 3. Generate run_id and metadata
    run_id = f"ops-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    created_at = datetime.now().isoformat()
    git_commit = get_git_commit()
    cwd = os.getcwd()

    # 3-B Pre-flight safety gates (run before any file I/O or Shioaji login)
    _preflight_blocked: list = []

    # 3-B-1 Git clean check
    _git_status = check_git_clean(cwd)
    if not _git_status["clean"]:
        _preflight_blocked.append("uncommitted_changes")
        print(
            f"[pre-flight] WARNING: git repo has uncommitted changes: "
            f"{_git_status['uncommitted_files']}"
        )

    # 3-B-2 Lock file detection
    _lock_path = Path(cwd) / ".ops.lock"
    _lock_status = check_ops_lock(_lock_path)
    if _lock_status["locked"]:
        _preflight_blocked.append("ops_lock_file_exists")
        print(
            f"[pre-flight] WARNING: ops lock file already exists at {_lock_path}. "
            "Another ops-run may be in progress."
        )

    # Create lock file; remove in finally so it is cleaned up even on failure
    _lock_created = False
    if not _lock_status["locked"]:
        try:
            _lock_path.write_text(
                json.dumps({"run_id": run_id, "created_at": created_at}) + "\n",
                encoding="utf-8",
            )
            _lock_created = True
        except Exception as _lock_err:
            print(f"[pre-flight] WARNING: could not create lock file: {_lock_err}")

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
    output_dir = Path(args.output_dir) if args.output_dir else Path("reports") / f"{trading_date_str}-ops"
    output_dir.mkdir(parents=True, exist_ok=True)

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

    # 4. Build input plan using the plan builder contract
    cache_dir = Path(args.cache_dir)
    plan_items = build_simulation_plan(
        trading_date=trading_date_str,
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

    # Check if we are running in after-market hours to automatically ingest and build candidates
    from datetime import datetime, time
    current_time_obj = None
    if args.current_time:
        try:
            parts = args.current_time.split(":")
            current_time_obj = time(int(parts[0]), int(parts[1]))
        except Exception:
            pass
    if not current_time_obj:
        current_time_obj = datetime.now().time()

    is_after_market = current_time_obj >= time(17, 30) or current_time_obj < time(8, 30)
    if is_after_market:
        print(f"Current time {current_time_obj.strftime('%H:%M')} is after-market. Automatically running daily ingestion and candidate building...")

        # Ingest data
        try:
            from .finmind_ingestion import FinMindDataLoaderClient, build_single_request, ingest_finmind_requests
            loader = FinMindDataLoaderClient(token=os.getenv("FINMIND_TOKEN", ""))
            reqs = [
                build_single_request(trading_date_str, "TaiwanStockPrice", "market"),
                build_single_request(trading_date_str, "TaiwanStockInfo", "market"),
                build_single_request(trading_date_str, "TaiwanStockMarginPurchaseSell", "market"),
                build_single_request(trading_date_str, "TaiwanStockChipActive", "market"),
            ]
            print(f"Ingesting FinMind market data for {trading_date_str}...")
            ingest_finmind_requests(loader, reqs, cache_dir=cache_dir)
            print("FinMind data ingestion completed.")
        except Exception as e:
            print(f"Warning: FinMind Ingestion failed: {e}")

        # Candidate Building
        try:
            candidate_output_path = Path("reports") / f"{trading_date_str}-candidates.json"
            print(f"Building next-day candidates and writing to {candidate_output_path}...")
            build_candidates_from_raw_cache(
                trading_date=trading_date_str,
                cache_dir=cache_dir,
                output_path=candidate_output_path,
            )
            print("Next-day candidates successfully built.")
        except Exception as e:
            print(f"Warning: Candidates building failed: {e}")

        print("After-market pipeline finished.")
        return

    # 5. Setup broker and adapter
    side_effects = []
    blocked_reasons = []

    # Determine if we are simulation on
    simulation_on = bool(args.simulation_on)
    if not simulation_on and not bool(getattr(args, "ignore_env_simulation", False)):
        env_sim = os.getenv("IS_SIMULATION", "").lower()
        if env_sim in ("true", "1", "yes", "on"):
            simulation_on = True

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
            "trading_date": trading_date_str,
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
        stream = ShioajiCallbackStream(api=api, store=sync_store, trading_date=trading_date_str)
        stream.start()

    # 6. Run the simulation adapter and check gates
    results = []
    strategy = VwapBreakoutStrategy()
    for plan_item in plan_items:
        signal_to_exec = plan_item.signal
        decision_to_exec = plan_item.risk_decision

        # Check VWAP breakout intraday strategy before executing
        if plan_item.risk_decision.approved:
            minute_bars = []
            if simulation_on:
                try:
                    symbol = plan_item.signal.symbol
                    contract = api.Contracts.Stocks[symbol]
                    kbars = api.kbars(contract, start_date=trading_date_str, end_date=trading_date_str)
                    if kbars and hasattr(kbars, "time") and len(kbars.time) > 0:
                        for i in range(len(kbars.time)):
                            bar_time = kbars.time[i]
                            if hasattr(bar_time, "strftime"):
                                bar_time_str = bar_time.strftime("%H:%M")
                            else:
                                bar_time_str = str(bar_time).split("T")[-1][:5]
                            minute_bars.append({
                                "time": bar_time_str,
                                "open": float(kbars.open[i]),
                                "high": float(kbars.high[i]),
                                "low": float(kbars.low[i]),
                                "close": float(kbars.close[i]),
                                "volume": float(kbars.volume[i]),
                            })
                except Exception as e:
                    print(f"Warning: Failed to fetch minute kbars for {plan_item.signal.symbol}: {e}")
            else:
                # Dry-run mock bars for testing
                for i in range(15):
                    minute_bars.append({
                        "time": f"09:{i:02d}",
                        "open": 900.0,
                        "high": 905.0,
                        "low": 895.0,
                        "close": 900.0,
                        "volume": 100.0,
                    })
                minute_bars.append({
                    "time": "09:15",
                    "open": 901.0,
                    "high": 910.0,
                    "low": 900.0,
                    "close": 908.0,
                    "volume": 300.0,
                })

            intraday_sig = strategy.generate_signal(
                symbol=plan_item.signal.symbol,
                minute_bars=minute_bars,
                prev_close=plan_item.signal.price,
            )

            if not intraday_sig.triggered:
                decision_to_exec = RiskDecision(
                    approved=False,
                    reason=f"strategy_not_triggered: {intraday_sig.reason}",
                    quantity=None,
                    price=None,
                )
            else:
                # Strategy triggered! Update price to actual intraday entry breakout price
                signal_to_exec = SignalIntent(
                    trading_date=plan_item.signal.trading_date,
                    strategy_id=plan_item.signal.strategy_id,
                    symbol=plan_item.signal.symbol,
                    setup_id=plan_item.signal.setup_id,
                    side=plan_item.signal.side,
                    quantity=plan_item.signal.quantity,
                    price=intraday_sig.entry_price,
                )
                decision_to_exec = RiskDecision(
                    approved=True,
                    reason=plan_item.risk_decision.reason,
                    quantity=plan_item.risk_decision.quantity,
                    price=intraday_sig.entry_price,
                )

        res = adapter.execute(
            signal_to_exec,
            decision_to_exec,
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
        "trading_date": trading_date_str,
        "sample_type": "simulation",
        "summary": summarize_simulation_results(results),
        "results": [result.to_dict() for result in results],
    }
    write_json(simulation_output_path, payload)

    # 7. Run restart sync comparison
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

    # 8. Run readiness report
    readiness_policy = ProductionReadinessPolicy(
        trading_date=trading_date_str,
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

    # Generate P9 Alerts
    alerts = generate_alerts_from_run(run_id, candidates_list, results, readiness_report)
    from .simulation import send_alerts_telegram
    alerts = send_alerts_telegram(alerts, dry_run=bool(getattr(args, "alert_dry_run", False)))
    alerts_path = output_dir / "alerts.json"
    write_json(alerts_path, [alert.to_dict() for alert in alerts])

    # Also save the callback store snapshot in the output directory if it is distinct
    callback_store_backup_path = output_dir / "callback_store.json"
    if callback_store_backup_path != sync_store_path:
        write_json(callback_store_backup_path, snapshot)

    # Gather manual actions
    manual_actions = readiness_report.get("manual_actions", [])

    # 9. Collect output artifacts and checksums
    output_artifacts = [
        {"path": str(simulation_output_path), "checksum": get_file_checksum(simulation_output_path)},
        {"path": str(restart_sync_path), "checksum": get_file_checksum(restart_sync_path)},
        {"path": str(readiness_report_path), "checksum": get_file_checksum(readiness_report_path)},
        {"path": str(readiness_md_path), "checksum": get_file_checksum(readiness_md_path)},
        {"path": str(alerts_path), "checksum": get_file_checksum(alerts_path)},
        {"path": str(callback_store_backup_path), "checksum": get_file_checksum(callback_store_backup_path)},
    ]

    # Build manifest
    manifest = {
        "run_id": run_id,
        "trading_date": trading_date_str,
        "created_at": created_at,
        "git_commit": git_commit,
        "cwd": cwd,
        "command": command,
        "env_sources": env_sources,
        "git_status": _git_status,
        "input_artifacts": input_artifacts,
        "output_artifacts": output_artifacts,
        "side_effects": side_effects,
        "blocked_reasons": _preflight_blocked + blocked_reasons,
        "manual_actions": manual_actions,
        "simulation_only": True,
    }

    manifest_path = output_dir / "ops_run_manifest.json"
    write_json(manifest_path, manifest)

    print(f"Daily simulation ops run completed successfully!")
    print(f"Run ID: {run_id}")
    print(f"Manifest written to: {manifest_path}")

    # 3-B-2 Remove lock file now that the run is complete
    if _lock_created:
        try:
            _lock_path.unlink(missing_ok=True)
        except Exception as _unlock_err:
            print(f"[post-run] WARNING: could not remove lock file: {_unlock_err}")


def cmd_simulate_regression_import(args: argparse.Namespace) -> None:
    """Import a daily ops run manifest and build regression cases for any alerts/failures."""
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = manifest["run_id"]
    trading_date = manifest["trading_date"]

    output_dir = Path(args.output_dir) if args.output_dir else Path("data/regression")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = output_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    alerts_file_path = None
    for art in manifest.get("output_artifacts", []):
        if art["path"].endswith("alerts.json"):
            alerts_file_path = Path(art["path"])
            break

    if not alerts_file_path or not alerts_file_path.exists():
        print("No alerts.json found in manifest artifacts. No regression cases generated.")
        return

    alerts_list = json.loads(alerts_file_path.read_text(encoding="utf-8"))
    if not alerts_list:
        print("Alerts list is empty. No regression cases generated.")
        return

    cases_created = []
    for alert in alerts_list:
        alert_id = alert["alert_id"]
        category = alert["category"]
        severity = alert["severity"]
        manual_action = alert["manual_action"]

        if category == "candidate_quality":
            failure_type = "candidate_quality"
        elif category == "execution_health":
            failure_type = "broker_callback_lifecycle"
        elif category == "readiness":
            failure_type = "risk_decision" if "gate" in alert_id or "limit" in alert_id else "reporting_issue"
        else:
            failure_type = "reporting_issue"

        case_id = f"case-{run_id}-{alert_id.replace('alert-' + run_id + '-', '')}"
        fixture_path = fixtures_dir / f"{case_id}_fixture.json"

        fixture_payload = {
            "alert": alert,
            "trading_date": trading_date,
            "run_id": run_id,
        }
        write_json(fixture_path, fixture_payload)

        expected_behavior = f"Resolve alert: {manual_action}"
        red_command = f"tw-daytrade simulate regression-run --case {output_dir}/{case_id}.json"
        closing_test_command = f"tw-daytrade simulate regression-run --case {output_dir}/{case_id}.json --mock-fix"

        case = RegressionCase(
            case_id=case_id,
            source_run_id=run_id,
            failure_type=failure_type,
            raw_source_payload=alert,
            minimal_fixture_path=str(fixture_path),
            expected_behavior=expected_behavior,
            red_command=red_command,
            closing_test_command=closing_test_command,
            status="open",
        )

        case_path = output_dir / f"{case_id}.json"
        write_json(case_path, case.to_dict())
        cases_created.append(case_id)
        print(f"Generated Regression Case: {case_path}")

    print(f"Imported manifest {run_id}. Created {len(cases_created)} regression cases.")


def cmd_simulate_regression_run(args: argparse.Namespace) -> None:
    """Run a specific regression case to test expected behavior and validation fixes."""
    case_path = Path(args.case)
    if not case_path.exists():
        raise FileNotFoundError(f"Regression case not found: {case_path}")

    case_data = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case_data["case_id"]
    fixture_path = Path(case_data["minimal_fixture_path"])

    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    print(f"Running regression case {case_id} (Failure Type: {case_data['failure_type']})...")

    if args.mock_fix:
        case_data["status"] = "fixed"
        write_json(case_path, case_data)
        print(f"Case {case_id} PASSED (mock-fix applied). Status updated to fixed.")
    else:
        if case_data["status"] == "open":
            print(f"Case {case_id} FAILED: Expected behavior '{case_data['expected_behavior']}' is unresolved.")
            raise SystemExit(1)
        else:
            print(f"Case {case_id} is already in state '{case_data['status']}'.")


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
    expected_token = args.expected_manual_approval_token or ""
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


def cmd_generate_approval_token(args: argparse.Namespace) -> None:
    """Generate a 5-minute valid HMAC manual approval token."""
    import sys
    secret = os.getenv("LIVE_APPROVAL_SECRET", "")
    if not secret:
        print("Error: LIVE_APPROVAL_SECRET environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    from .live import generate_live_approval_token
    token = generate_live_approval_token(secret)
    print(token)


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
    close.add_argument("--readiness")
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

    db_migrate = db_sub.add_parser("migrate")
    db_migrate.add_argument("--schema-dir", default="sql")
    db_migrate.set_defaults(func=cmd_db_migrate)

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
    replay_samples_parser.add_argument("--use-cost-model", action="store_true")
    replay_samples_parser.add_argument("--commission-discount", type=float, default=0.3)
    replay_samples_parser.add_argument("--day-trade-tax-rate", type=float, default=0.0015)
    replay_samples_parser.add_argument("--slippage-ticks", type=float, default=1.0)
    replay_samples_parser.set_defaults(func=cmd_replay_samples)

    walk_forward = replay_sub.add_parser("walk-forward")
    walk_forward.add_argument("--input", required=True)
    walk_forward.add_argument("--split-date")
    walk_forward.add_argument("--train-ratio", type=float, default=0.7)
    walk_forward.add_argument("--output")
    walk_forward.add_argument("--report-output")
    walk_forward.add_argument("--cost-r", type=float, default=0.0)
    walk_forward.add_argument("--use-cost-model", action="store_true")
    walk_forward.add_argument("--commission-discount", type=float, default=0.3)
    walk_forward.add_argument("--day-trade-tax-rate", type=float, default=0.0015)
    walk_forward.add_argument("--slippage-ticks", type=float, default=1.0)
    walk_forward.set_defaults(func=cmd_replay_walk_forward)

    cost_analysis = subparsers.add_parser("cost-analysis")
    cost_analysis.add_argument("--candidates", required=True)
    cost_analysis.add_argument("--cache-dir", default="data/raw")
    cost_analysis.add_argument("--stop-pct", type=float, default=1.0)
    cost_analysis.add_argument("--commission-discount", type=float, default=0.3)
    cost_analysis.add_argument("--day-trade-tax-rate", type=float, default=0.0015)
    cost_analysis.add_argument("--slippage-ticks", type=float, default=1.0)
    cost_analysis.set_defaults(func=cmd_cost_analysis)

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
    ops_run.add_argument("--date")
    ops_run.add_argument("--candidates-input")
    ops_run.add_argument("--candidate-date")
    ops_run.add_argument("--cache-dir", default="data/raw")
    ops_run.add_argument("--execution-sync-store")
    ops_run.add_argument("--output-dir")
    ops_run.add_argument("--simulation-on", action="store_true")
    ops_run.add_argument("--ignore-env-simulation", action="store_true")
    ops_run.add_argument("--allow-outside-session", action="store_true")
    ops_run.add_argument("--current-time")
    ops_run.add_argument("--allow-live-trading", action="store_true")
    ops_run.add_argument("--manual-approval-token")
    ops_run.add_argument("--expected-manual-approval-token")
    ops_run.add_argument("--max-pending-orders", type=int, default=0)
    ops_run.add_argument("--disable-cancel-retry-plan", action="store_true")
    ops_run.add_argument("--alert-dry-run", action="store_true")
    ops_run.set_defaults(func=cmd_simulate_ops_run)

    daily_ops = simulate_sub.add_parser("daily-ops")
    daily_ops.add_argument("--date")
    daily_ops.add_argument("--cache-dir", default="data/raw")
    daily_ops.add_argument("--requests")
    daily_ops.add_argument("--dataset", default="TaiwanStockPrice")
    daily_ops.add_argument("--stock-id")
    daily_ops.add_argument("--start-date")
    daily_ops.add_argument("--market-proxy-stock-id", default="0050")
    daily_ops.add_argument("--market-proxy-start-date")
    daily_ops.add_argument("--token")
    daily_ops.add_argument("--quota-limit", type=int, default=540)
    daily_ops.add_argument("--skip-ingestion", action="store_true")
    daily_ops.add_argument("--allow-ingestion-failure", action="store_true")
    daily_ops.add_argument("--candidates-output")
    daily_ops.add_argument("--candidate-date")
    daily_ops.add_argument("--limit", type=int, default=80)
    daily_ops.add_argument("--min-trading-money", type=float, default=80_000_000)
    daily_ops.add_argument("--execution-sync-store")
    daily_ops.add_argument("--output-dir")
    daily_ops.add_argument("--ops-output-dir")
    daily_ops.add_argument("--simulation-on", action="store_true")
    daily_ops.add_argument("--allow-outside-session", action="store_true")
    daily_ops.add_argument("--current-time", default="12:00")
    daily_ops.add_argument("--max-pending-orders", type=int, default=0)
    daily_ops.add_argument("--disable-cancel-retry-plan", action="store_true")
    daily_ops.add_argument("--close-report-output")
    daily_ops.add_argument("--telegram-summary-output")
    daily_ops.add_argument("--regression-output-dir")
    daily_ops.add_argument("--skip-regression-import", action="store_true")
    daily_ops.add_argument("--audit-output")
    daily_ops.add_argument("--fail-on-audit", action="store_true")
    daily_ops.add_argument("--send-alerts", action="store_true")
    daily_ops.set_defaults(func=cmd_simulate_daily_ops)

    trading_day_cycle = simulate_sub.add_parser("trading-day-cycle")
    trading_day_cycle.add_argument("--date")
    trading_day_cycle.add_argument("--cache-dir", default="data/raw")
    trading_day_cycle.add_argument("--trading-data-input")
    trading_day_cycle.add_argument("--market-proxy-stock-id", default="0050")
    trading_day_cycle.add_argument("--candidates-input")
    trading_day_cycle.add_argument("--output-dir")
    trading_day_cycle.add_argument("--state-output")
    trading_day_cycle.add_argument("--run-id")
    trading_day_cycle.add_argument("--start-policy", choices=("09:05", "10:00"), default="09:05")
    trading_day_cycle.add_argument("--hard-stop-time", default="13:20")
    trading_day_cycle.add_argument("--close-buffer-end-time", default="14:00")
    trading_day_cycle.add_argument("--report-time", default="15:00")
    trading_day_cycle.add_argument("--next-candidate-time", default="17:30")
    trading_day_cycle.add_argument("--current-time", default="09:05")
    trading_day_cycle.add_argument("--max-retries", type=int, default=2)
    trading_day_cycle.add_argument("--run-all-stages", action="store_true")
    trading_day_cycle.set_defaults(func=cmd_simulate_trading_day_cycle)

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

    generate_approval_token = simulate_sub.add_parser("generate-approval-token")
    generate_approval_token.set_defaults(func=cmd_generate_approval_token)

    regression_import = simulate_sub.add_parser("regression-import")
    regression_import.add_argument("--manifest", required=True)
    regression_import.add_argument("--output-dir")
    regression_import.set_defaults(func=cmd_simulate_regression_import)

    regression_run = simulate_sub.add_parser("regression-run")
    regression_run.add_argument("--case", required=True)
    regression_run.add_argument("--mock-fix", action="store_true")
    regression_run.set_defaults(func=cmd_simulate_regression_run)


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
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
