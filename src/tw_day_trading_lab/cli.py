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
from .bars import (
    FiveMinuteBarAggregator,
    OneMinuteBarAggregator,
    append_bars,
    check_bar_volume,
    latest_bars,
)
from .ledger import PaperLedger
from .market_data import (
    raw_tick_path,
    replay_raw_ticks,
    run_gated_shioaji_tick_stream_smoke,
)
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


def _parse_optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_intraday_bars_by_symbol(path: Path | None) -> dict[str, list[dict[str, object]]]:
    """Load fixture intraday bars and group them by candidate symbol."""
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict[str, object]]] = {}
    if isinstance(raw, dict):
        for key in ("bars", "data", "rows", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                raw = value
                break
        else:
            for symbol, bars in raw.items():
                if isinstance(bars, list):
                    grouped[str(symbol)] = [dict(item) for item in bars if isinstance(item, dict)]
            return grouped
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("stock_id") or "")
            if symbol:
                grouped.setdefault(symbol, []).append(dict(item))
    return grouped


def _normalize_intraday_bar(row: dict[str, object], symbol: str) -> dict[str, object]:
    """Normalize raw/cache intraday rows into the strategy bar shape."""
    raw_time = row.get("time") or row.get("Time") or row.get("minute") or row.get("datetime") or row.get("date")
    time_value = str(raw_time or "")
    if " " in time_value:
        time_value = time_value.split(" ", 1)[1]
    if len(time_value) >= 5:
        time_value = time_value[:5]
    return {
        "symbol": symbol,
        "time": time_value,
        "open": _parse_optional_float(row.get("open") or row.get("Open")),
        "high": _parse_optional_float(row.get("high") or row.get("max") or row.get("High")),
        "low": _parse_optional_float(row.get("low") or row.get("min") or row.get("Low")),
        "close": _parse_optional_float(row.get("close") or row.get("Close")),
        "volume": _parse_optional_float(row.get("volume") or row.get("Trading_Volume") or row.get("Volume")) or 0,
        "source": "raw_cache",
    }


def load_intraday_bars_for_cycle(
    *,
    trading_date: str,
    cache_dir: Path,
    candidates: list[CandidateScore],
    intraday_bars_input: Path | None,
    intraday_cache_dataset: str = "TaiwanStockPriceMinute",
) -> tuple[dict[str, list[dict[str, object]]], dict[str, object]]:
    """Load intraday bars from explicit fixture input or candidate-scoped raw cache."""
    if intraday_bars_input is not None:
        return _load_intraday_bars_by_symbol(intraday_bars_input), {
            "mode": "explicit_input",
            "dataset": None,
            "sources": [{"path": str(intraday_bars_input), "exists": intraday_bars_input.exists()}],
        }

    grouped: dict[str, list[dict[str, object]]] = {}
    sources: list[dict[str, object]] = []
    for candidate in candidates:
        symbol = candidate.symbol
        path = cache_dir / "finmind" / intraday_cache_dataset / trading_date / f"{symbol}.jsonl"
        rows = _read_rows_from_json_or_jsonl(path)
        sources.append({"symbol": symbol, "path": str(path), "exists": path.exists(), "rows": len(rows)})
        if rows:
            grouped[symbol] = [_normalize_intraday_bar(row, symbol) for row in rows]
    return grouped, {
        "mode": "raw_cache",
        "dataset": intraday_cache_dataset,
        "sources": sources,
    }


def _load_open_positions_by_symbol(path: Path | None) -> dict[str, dict[str, object]]:
    """Load dry-run position state for exit-first watch-loop evaluation."""
    if path is None or not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        positions = raw.get("open_positions", raw.get("positions", []))
    else:
        positions = raw
    grouped: dict[str, dict[str, object]] = {}
    if isinstance(positions, list):
        for item in positions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("stock_id") or "")
            if symbol:
                grouped[symbol] = dict(item)
    return grouped


def _load_position_state_payload(path: Path | None) -> dict[str, object]:
    """Load dry-run position state while keeping a stable default shape."""
    if path is None or not path.exists():
        return {
            "open_positions": [],
            "pending_orders": [],
            "lifecycle_decisions": [],
            "summary": {},
        }
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {
            "open_positions": raw.get("open_positions", raw.get("positions", [])) if isinstance(raw.get("open_positions", raw.get("positions", [])), list) else [],
            "pending_orders": raw.get("pending_orders", []) if isinstance(raw.get("pending_orders", []), list) else [],
            "lifecycle_decisions": raw.get("lifecycle_decisions", []) if isinstance(raw.get("lifecycle_decisions", []), list) else [],
            "summary": raw.get("summary", {}) if isinstance(raw.get("summary", {}), dict) else {},
        }
    if isinstance(raw, list):
        return {
            "open_positions": raw,
            "pending_orders": [],
            "lifecycle_decisions": [],
            "summary": {},
        }
    return {
        "open_positions": [],
        "pending_orders": [],
        "lifecycle_decisions": [],
        "summary": {},
    }


def _bars_until_time(
    bars: list[dict[str, object]],
    current_time: str,
) -> list[dict[str, object]]:
    """Return bars visible at the current scan time; bars without time are treated as visible."""
    visible = []
    for bar in sorted(bars, key=lambda item: str(item.get("time", ""))):
        bar_time = str(bar.get("time") or "")
        if not bar_time or bar_time <= current_time:
            visible.append(bar)
    return visible


def _signal_to_dict(signal: object) -> dict[str, object]:
    return {
        "triggered": bool(getattr(signal, "triggered", False)),
        "direction": str(getattr(signal, "direction", "")),
        "entry_price": getattr(signal, "entry_price", None),
        "stop_price": getattr(signal, "stop_price", None),
        "target_price": getattr(signal, "target_price", None),
        "reason": str(getattr(signal, "reason", "")),
    }


def _evaluate_exit_signal(
    *,
    position: dict[str, object],
    bars: list[dict[str, object]],
    current_time: str,
    hard_stop_time: str,
) -> dict[str, object]:
    """Evaluate dry-run exits before any new entry logic for an open position."""
    entry_price = _parse_optional_float(position.get("entry_price"))
    stop_price = _parse_optional_float(position.get("stop_price"))
    target_price = _parse_optional_float(position.get("target_price"))
    if entry_price is None or stop_price is None or target_price is None:
        return {
            "triggered": False,
            "direction": "sell",
            "reason": "position_state_incomplete",
        }

    visible_bars = _bars_until_time(bars, current_time)
    for bar in visible_bars:
        low_value = _parse_optional_float(bar.get("low", bar.get("min")))
        high_value = _parse_optional_float(bar.get("high", bar.get("max")))
        if low_value is not None and low_value <= stop_price:
            return {
                "triggered": True,
                "direction": "sell",
                "exit_price": stop_price,
                "reason": "stop_loss",
            }
        if high_value is not None and high_value >= target_price:
            return {
                "triggered": True,
                "direction": "sell",
                "exit_price": target_price,
                "reason": "take_profit",
            }

    if current_time >= hard_stop_time:
        exit_price = entry_price
        if visible_bars:
            last_bar = visible_bars[-1]
            exit_price = _parse_optional_float(last_bar.get("close", last_bar.get("close_price"))) or entry_price
        return {
            "triggered": True,
            "direction": "sell",
            "exit_price": exit_price,
            "reason": "time_exit",
        }

    return {
        "triggered": False,
        "direction": "sell",
        "reason": "exit_not_triggered",
    }


def build_intraday_watch_events(
    *,
    trading_day_run_id: str,
    trading_date: str,
    candidates: list[CandidateScore],
    bars_by_symbol: dict[str, list[dict[str, object]]],
    positions_by_symbol: dict[str, dict[str, object]],
    market_data_artifact: Path | None,
    current_time: str,
    hard_stop_time: str,
    observation_minutes: int = 15,
    volume_surge_ratio: float = 1.5,
) -> list[dict[str, object]]:
    """Evaluate candidate-only intraday watch events in dry-run mode."""
    strategy = VwapBreakoutStrategy(
        observation_minutes=observation_minutes,
        volume_surge_ratio=volume_surge_ratio,
    )
    events: list[dict[str, object]] = []
    for candidate in candidates:
        symbol = candidate.symbol
        all_bars = bars_by_symbol.get(symbol, [])
        visible_bars = _bars_until_time(all_bars, current_time)
        position = positions_by_symbol.get(symbol)
        base_event = {
            "event_id": f"{trading_day_run_id}:{current_time}:{symbol}",
            "trading_day_run_id": trading_day_run_id,
            "trading_date": trading_date,
            "timestamp": f"{trading_date}T{current_time}:00+08:00",
            "symbol": symbol,
            "candidate_rank": candidate.rank,
            "candidate_actionable": candidate.next_day_actionable,
            "bars_seen": len(visible_bars),
            "market_data_artifact": str(market_data_artifact) if market_data_artifact else None,
            "entry_signal": None,
            "exit_signal": None,
            "risk_decision": {"approved": False, "reason": "not_evaluated"},
            "action": "no_action",
            "reason": "not_evaluated",
            "order_intent_id": None,
            "side_effects": [],
        }

        if not visible_bars:
            base_event["reason"] = "intraday_data_missing"
            events.append(base_event)
            continue

        if position is not None:
            exit_signal = _evaluate_exit_signal(
                position=position,
                bars=all_bars,
                current_time=current_time,
                hard_stop_time=hard_stop_time,
            )
            base_event["exit_signal"] = exit_signal
            base_event["reason"] = str(exit_signal["reason"])
            if exit_signal.get("triggered"):
                base_event["action"] = "exit_approved"
                base_event["risk_decision"] = {"approved": True, "reason": "open_position_exit_first"}
                base_event["order_intent_id"] = f"{trading_day_run_id}:{symbol}:exit:{current_time}"
            else:
                base_event["action"] = "no_action"
                base_event["risk_decision"] = {"approved": False, "reason": "exit_not_triggered"}
            events.append(base_event)
            continue

        if current_time >= hard_stop_time:
            base_event["action"] = "entry_rejected"
            base_event["reason"] = "after_hard_stop"
            base_event["risk_decision"] = {"approved": False, "reason": "after_hard_stop"}
            events.append(base_event)
            continue

        if not candidate.next_day_actionable:
            base_event["reason"] = "candidate_not_actionable"
            base_event["risk_decision"] = {"approved": False, "reason": "candidate_not_actionable"}
            events.append(base_event)
            continue

        entry_signal = strategy.generate_signal(symbol, visible_bars)
        base_event["entry_signal"] = _signal_to_dict(entry_signal)
        base_event["reason"] = entry_signal.reason
        if entry_signal.triggered:
            base_event["action"] = "entry_approved"
            base_event["risk_decision"] = {
                "approved": True,
                "reason": "dry_run_strategy_signal",
                "quantity": 1000,
            }
            base_event["order_intent_id"] = f"{trading_day_run_id}:{symbol}:entry:{current_time}"
        else:
            base_event["action"] = "no_action"
            base_event["risk_decision"] = {"approved": False, "reason": entry_signal.reason}
        events.append(base_event)
    return events


def build_trading_day_order_intents(
    *,
    trading_day_run_id: str,
    trading_date: str,
    watch_events: list[dict[str, object]],
    position_state: dict[str, object],
    current_time: str,
    hard_stop_time: str,
    max_open_positions: int = 3,
    daily_risk_stop_r: float = -3.0,
) -> dict[str, object]:
    """Convert watch events into dry-run order and cancel intents."""
    open_positions = [item for item in position_state.get("open_positions", []) if isinstance(item, dict)]
    pending_orders = [item for item in position_state.get("pending_orders", []) if isinstance(item, dict)]
    lifecycle_decisions = [item for item in position_state.get("lifecycle_decisions", []) if isinstance(item, dict)]
    positions_by_symbol = {str(item.get("symbol") or item.get("stock_id") or ""): item for item in open_positions}
    existing_ids = {
        str(item.get("order_intent_id") or item.get("intent_id") or item.get("idempotency_key") or "")
        for item in pending_orders
    }
    existing_ids.discard("")
    seen_ids: set[str] = set(existing_ids)
    daily_realized_r = _parse_optional_float(position_state.get("summary", {}).get("daily_realized_r")) or 0.0

    order_intents: list[dict[str, object]] = []
    cancel_intents: list[dict[str, object]] = []
    manual_actions: list[str] = []

    for pending in pending_orders:
        status = str(pending.get("status") or "submitted")
        if current_time >= hard_stop_time and status not in {"filled", "cancelled", "rejected"}:
            pending_id = str(pending.get("order_intent_id") or pending.get("intent_id") or pending.get("idempotency_key") or "")
            cancel_intents.append(
                {
                    "cancel_intent_id": f"{trading_day_run_id}:{pending_id or 'pending'}:cancel:{current_time}",
                    "source_order_intent_id": pending_id or None,
                    "symbol": str(pending.get("symbol") or ""),
                    "reason": "stale_pending_order_after_hard_stop",
                    "status": "dry_run_ready",
                    "side_effects": [],
                }
            )

    for decision in lifecycle_decisions:
        if str(decision.get("normalized_status") or "") == "partial_filled":
            manual_actions.append("Partial fill requires manual reconciliation before online simulation.")

    for event in watch_events:
        action = str(event.get("action") or "")
        if action not in {"entry_approved", "exit_approved"}:
            continue
        intent_id = str(event.get("order_intent_id") or f"{trading_day_run_id}:{event.get('symbol')}:{action}:{current_time}")
        symbol = str(event.get("symbol") or "")
        side = "buy" if action == "entry_approved" else "sell"
        price = None
        quantity = 1000
        source_signal = event.get("entry_signal") if side == "buy" else event.get("exit_signal")
        if isinstance(source_signal, dict):
            price = source_signal.get("entry_price") if side == "buy" else source_signal.get("exit_price")
        if side == "buy" and isinstance(event.get("risk_decision"), dict):
            quantity = int(event["risk_decision"].get("quantity") or quantity)
        if side == "sell" and symbol in positions_by_symbol:
            quantity = int(positions_by_symbol[symbol].get("quantity") or quantity)

        status = "dry_run_ready"
        blocked_reason = None
        if intent_id in seen_ids:
            status = "duplicate_suppressed"
            blocked_reason = "duplicate_intent"
        elif side == "buy" and len(open_positions) >= max_open_positions:
            status = "blocked"
            blocked_reason = "max_open_positions_reached"
        elif side == "buy" and daily_realized_r <= daily_risk_stop_r:
            status = "blocked"
            blocked_reason = "daily_risk_stop_reached"
        else:
            seen_ids.add(intent_id)

        order_intents.append(
            {
                "order_intent_id": intent_id,
                "trading_day_run_id": trading_day_run_id,
                "trading_date": trading_date,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "source_event_id": event.get("event_id"),
                "status": status,
                "blocked_reason": blocked_reason,
                "mode": "dry_run",
                "side_effects": [],
            }
        )

    return {
        "trading_day_run_id": trading_day_run_id,
        "trading_date": trading_date,
        "mode": "dry_run",
        "order_intents": order_intents,
        "cancel_intents": cancel_intents,
        "execution_policy": {
            "max_open_positions": max_open_positions,
            "daily_risk_stop_r": daily_risk_stop_r,
            "duplicate_intent_policy": "suppress",
            "pending_order_policy": "cancel_after_hard_stop",
            "partial_fill_policy": "manual_reconciliation",
        },
        "manual_actions": manual_actions,
        "summary": {
            "order_intents": len(order_intents),
            "dry_run_ready": sum(1 for item in order_intents if item["status"] == "dry_run_ready"),
            "blocked": sum(1 for item in order_intents if item["status"] == "blocked"),
            "duplicate_suppressed": sum(1 for item in order_intents if item["status"] == "duplicate_suppressed"),
            "cancel_intents": len(cancel_intents),
            "manual_actions": len(manual_actions),
        },
        "side_effects": [],
    }


def build_trading_day_position_state(
    *,
    trading_day_run_id: str,
    trading_date: str,
    source_state: dict[str, object],
    order_intents_payload: dict[str, object],
) -> dict[str, object]:
    """Create a dry-run position-state artifact without mutating fills."""
    return {
        "trading_day_run_id": trading_day_run_id,
        "trading_date": trading_date,
        "mode": "dry_run",
        "open_positions": source_state.get("open_positions", []),
        "pending_orders": source_state.get("pending_orders", []),
        "generated_order_intents": order_intents_payload.get("order_intents", []),
        "generated_cancel_intents": order_intents_payload.get("cancel_intents", []),
        "manual_actions": order_intents_payload.get("manual_actions", []),
        "summary": order_intents_payload.get("summary", {}),
        "mutation_policy": "no_fill_mutation_in_dry_run",
        "side_effects": [],
    }


def render_trading_day_report_markdown(
    *,
    state: dict[str, object],
    watch_events: list[dict[str, object]],
    order_intents_payload: dict[str, object],
) -> str:
    """Render the 15:00 dry-run trading-day report."""
    summary = order_intents_payload.get("summary", {})
    action_counts: dict[str, int] = {}
    for event in watch_events:
        action = str(event.get("action") or "unknown")
        action_counts[action] = action_counts.get(action, 0) + 1
    lines = [
        f"# Trading Day Report {state['trading_date']}",
        "",
        "## Summary",
        "",
        f"- mode: `{state['mode']}`",
        f"- run id: `{state['trading_day_run_id']}`",
        f"- stage: `{state['stage']}`",
        f"- calendar status: `{state['calendar_status']}`",
        f"- watch events: {len(watch_events)}",
        f"- order intents: {summary.get('order_intents', 0)}",
        f"- cancel intents: {summary.get('cancel_intents', 0)}",
        f"- blocked intents: {summary.get('blocked', 0)}",
        f"- duplicate suppressed: {summary.get('duplicate_suppressed', 0)}",
        "",
        "## Action Counts",
        "",
    ]
    for action in sorted(action_counts):
        lines.append(f"- {action}: {action_counts[action]}")
    lines.extend(
        [
            "",
            "## Pros",
            "",
            "- Candidate-only watch loop avoids scanning the whole market.",
            "- Approved entries/exits are captured as dry-run intents before any broker side effect.",
            "- 13:20 hard-stop policy rejects new entries and prepares cancel/exit handling.",
            "",
            "## Cons / Blockers",
            "",
        ]
    )
    blockers = list(state.get("blocked_reasons", [])) + list(order_intents_payload.get("manual_actions", []))
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- No blocking condition in this dry-run fixture.")
    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "- Run a multi-day fixture smoke before enabling Shioaji simulation side effects.",
            "- Keep live order execution blocked until a separate approval gate exists.",
            "",
            "## Dry-Run Links",
            "",
            "- GitHub publish status: `dry_run`",
            "- Operator link send status: `dry_run_not_sent`",
        ]
    )
    return "\n".join(lines) + "\n"


def build_next_candidates_handoff(
    *,
    trading_day_run_id: str,
    trading_date: str,
    candidates: list[CandidateScore],
) -> dict[str, object]:
    """Build the 17:30 next-candidate handoff without guessing the next trading date."""
    return {
        "trading_day_run_id": trading_day_run_id,
        "source_trading_date": trading_date,
        "next_trading_date_policy": "next_api_available_trading_day",
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for candidate in candidates if candidate.next_day_actionable],
        "side_effects": [],
    }


def build_end_to_end_smoke_summary(state: dict[str, object]) -> dict[str, object]:
    """Summarize whether the dry-run trading-day artifact chain is complete."""
    required_artifacts = [
        "candidate_artifact",
        "watch_events_artifact",
        "order_intents_artifact",
        "position_state_artifact",
        "report_artifact",
        "next_candidate_artifact",
    ]
    checks = []
    for key in required_artifacts:
        artifact = state.get(key, {})
        exists = bool(isinstance(artifact, dict) and artifact.get("exists"))
        checks.append({"artifact": key, "exists": exists, "path": artifact.get("path") if isinstance(artifact, dict) else None})
    status = "ok" if state.get("calendar_status") == "trading_day" and all(item["exists"] for item in checks) else "blocked"
    return {
        "trading_day_run_id": state.get("trading_day_run_id"),
        "trading_date": state.get("trading_date"),
        "status": status,
        "checks": checks,
        "side_effects": [],
    }


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
    position_state_artifact = Path(args.position_state_output) if args.position_state_output else output_dir / "position_state.json"
    watch_events_artifact = Path(args.watch_events_output) if args.watch_events_output else output_dir / "watch_events.json"
    order_intents_artifact = Path(args.order_intents_output) if args.order_intents_output else output_dir / "order_intents.json"
    report_artifact = Path(args.report_output) if args.report_output else output_dir / "report.md"
    next_candidate_artifact = Path(args.next_candidates_output) if args.next_candidates_output else output_dir / "next_candidates.json"
    smoke_artifact = Path(args.end_to_end_smoke_output) if args.end_to_end_smoke_output else output_dir / "end_to_end_smoke.json"
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
            "path": str(position_state_artifact),
            "exists": position_state_artifact.exists(),
            "checksum": get_file_checksum(position_state_artifact) if position_state_artifact.exists() else None,
        },
        "source_position_state_artifact": {
            "path": str(args.position_state_input) if args.position_state_input else None,
            "exists": Path(args.position_state_input).exists() if args.position_state_input else False,
            "checksum": get_file_checksum(Path(args.position_state_input)) if args.position_state_input and Path(args.position_state_input).exists() else None,
        },
        "watch_events_artifact": {
            "path": str(watch_events_artifact),
            "exists": watch_events_artifact.exists(),
            "checksum": get_file_checksum(watch_events_artifact) if watch_events_artifact.exists() else None,
            "summary": {
                "total": 0,
                "entry_approved": 0,
                "entry_rejected": 0,
                "exit_approved": 0,
                "no_action": 0,
            },
        },
        "intraday_data_adapter": {
            "mode": "explicit_input" if args.intraday_bars_input else "raw_cache",
            "dataset": args.intraday_cache_dataset,
            "sources": [],
            "candidate_scoped": True,
            "contract": "watch_events.json",
        },
        "order_intents_artifact": {
            "path": str(order_intents_artifact),
            "exists": order_intents_artifact.exists(),
            "checksum": get_file_checksum(order_intents_artifact) if order_intents_artifact.exists() else None,
            "summary": {
                "order_intents": 0,
                "dry_run_ready": 0,
                "blocked": 0,
                "duplicate_suppressed": 0,
                "cancel_intents": 0,
                "manual_actions": 0,
            },
        },
        "report_artifact": {
            "path": str(report_artifact),
            "github_url": None,
            "publish_status": "not_started",
            "exists": report_artifact.exists(),
            "checksum": get_file_checksum(report_artifact) if report_artifact.exists() else None,
        },
        "next_candidate_artifact": {
            "path": str(next_candidate_artifact),
            "exists": next_candidate_artifact.exists(),
            "checksum": get_file_checksum(next_candidate_artifact) if next_candidate_artifact.exists() else None,
        },
        "end_to_end_smoke_artifact": {
            "path": str(smoke_artifact),
            "exists": smoke_artifact.exists(),
            "checksum": get_file_checksum(smoke_artifact) if smoke_artifact.exists() else None,
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
    watch_events_output = Path(str(state["watch_events_artifact"]["path"]))
    order_intents_output = Path(str(state["order_intents_artifact"]["path"]))
    position_state_output = Path(str(state["position_state_artifact"]["path"]))
    report_output = Path(str(state["report_artifact"]["path"]))
    next_candidates_output = Path(str(state["next_candidate_artifact"]["path"]))
    smoke_output = Path(str(state["end_to_end_smoke_artifact"]["path"]))
    intraday_bars_input = Path(args.intraday_bars_input) if args.intraday_bars_input else None
    candidates_input = Path(args.candidates_input) if args.candidates_input else None
    position_state_input = Path(args.position_state_input) if args.position_state_input else None
    watch_events: list[dict[str, object]] = []
    candidates: list[CandidateScore] = []
    source_position_state = _load_position_state_payload(position_state_input)

    if (
        state["calendar_status"] == "trading_day"
        and candidates_input is not None
        and candidates_input.exists()
    ):
        candidates = load_candidate_scores(candidates_input)
        bars_by_symbol, adapter_summary = load_intraday_bars_for_cycle(
            trading_date=str(state["trading_date"]),
            cache_dir=Path(args.cache_dir),
            candidates=candidates,
            intraday_bars_input=intraday_bars_input,
            intraday_cache_dataset=args.intraday_cache_dataset,
        )
        state["intraday_data_adapter"] = adapter_summary | {
            "candidate_scoped": True,
            "contract": "watch_events.json",
        }
        watch_events = build_intraday_watch_events(
            trading_day_run_id=str(state["trading_day_run_id"]),
            trading_date=str(state["trading_date"]),
            candidates=candidates,
            bars_by_symbol=bars_by_symbol,
            positions_by_symbol={
                str(item.get("symbol") or item.get("stock_id") or ""): item
                for item in source_position_state.get("open_positions", [])
                if isinstance(item, dict)
            },
            market_data_artifact=intraday_bars_input,
            current_time=args.current_time,
            hard_stop_time=args.hard_stop_time,
            observation_minutes=args.strategy_observation_minutes,
            volume_surge_ratio=args.strategy_volume_surge_ratio,
        )
        write_json(watch_events_output, {"events": watch_events})
        summary = {
            "total": len(watch_events),
            "entry_approved": sum(1 for event in watch_events if event["action"] == "entry_approved"),
            "entry_rejected": sum(1 for event in watch_events if event["action"] == "entry_rejected"),
            "exit_approved": sum(1 for event in watch_events if event["action"] == "exit_approved"),
            "no_action": sum(1 for event in watch_events if event["action"] == "no_action"),
        }
        state["watch_events_artifact"] = {
            "path": str(watch_events_output),
            "exists": watch_events_output.exists(),
            "checksum": get_file_checksum(watch_events_output),
            "summary": summary,
        }
    elif state["calendar_status"] == "trading_day":
        state["manual_actions"].append("Intraday watch loop skipped; provide --candidates-input.")

    if state["calendar_status"] == "trading_day":
        if not candidates and candidates_input is not None and candidates_input.exists():
            candidates = load_candidate_scores(candidates_input)
        order_intents_payload = build_trading_day_order_intents(
            trading_day_run_id=str(state["trading_day_run_id"]),
            trading_date=str(state["trading_date"]),
            watch_events=watch_events,
            position_state=source_position_state,
            current_time=args.current_time,
            hard_stop_time=args.hard_stop_time,
            max_open_positions=args.max_open_positions,
            daily_risk_stop_r=args.daily_risk_stop_r,
        )
        write_json(order_intents_output, order_intents_payload)
        state["order_intents_artifact"] = {
            "path": str(order_intents_output),
            "exists": order_intents_output.exists(),
            "checksum": get_file_checksum(order_intents_output),
            "summary": order_intents_payload["summary"],
        }

        position_payload = build_trading_day_position_state(
            trading_day_run_id=str(state["trading_day_run_id"]),
            trading_date=str(state["trading_date"]),
            source_state=source_position_state,
            order_intents_payload=order_intents_payload,
        )
        write_json(position_state_output, position_payload)
        state["position_state_artifact"] = {
            "path": str(position_state_output),
            "exists": position_state_output.exists(),
            "checksum": get_file_checksum(position_state_output),
        }

        report_markdown = render_trading_day_report_markdown(
            state=state,
            watch_events=watch_events,
            order_intents_payload=order_intents_payload,
        )
        write_text(report_output, report_markdown)
        state["report_artifact"] = {
            "path": str(report_output),
            "github_url": None,
            "publish_status": "dry_run",
            "send_status": "dry_run_not_sent",
            "exists": report_output.exists(),
            "checksum": get_file_checksum(report_output),
        }

        next_candidates_payload = build_next_candidates_handoff(
            trading_day_run_id=str(state["trading_day_run_id"]),
            trading_date=str(state["trading_date"]),
            candidates=candidates,
        )
        write_json(next_candidates_output, next_candidates_payload)
        state["next_candidate_artifact"] = {
            "path": str(next_candidates_output),
            "exists": next_candidates_output.exists(),
            "checksum": get_file_checksum(next_candidates_output),
            "candidate_count": next_candidates_payload["candidate_count"],
        }

        smoke_payload = build_end_to_end_smoke_summary(state)
        write_json(smoke_output, smoke_payload)
        state["end_to_end_smoke_artifact"] = {
            "path": str(smoke_output),
            "exists": smoke_output.exists(),
            "checksum": get_file_checksum(smoke_output),
            "status": smoke_payload["status"],
        }

    write_json(state_output, state)
    print("Trading day cycle dry-run completed.")
    print(f"Date: {state['trading_date']}")
    print(f"Calendar status: {state['calendar_status']}")
    print(f"Stage: {state['stage']}")
    print(f"Watch events: {state['watch_events_artifact']['path']}")
    print(f"Order intents: {state['order_intents_artifact']['path']}")
    print(f"Report: {state['report_artifact']['path']}")
    print(f"Next candidates: {state['next_candidate_artifact']['path']}")
    print(f"End-to-end smoke: {state['end_to_end_smoke_artifact']['path']}")
    print(f"State: {state_output}")


def _format_date_pattern(pattern: str, trading_date: str) -> Path:
    return Path(pattern.format(date=trading_date, trading_date=trading_date))


def build_trading_day_cycle_smoke_markdown(summary: dict[str, object]) -> str:
    """Render a compact operator-readable multi-day smoke report."""
    lines = [
        f"# Trading Day Cycle Stability Smoke {summary['run_id']}",
        "",
        "## Summary",
        "",
        f"- dates: {summary['summary']['total_dates']}",
        f"- ok: {summary['summary']['ok']}",
        f"- skipped non-trading: {summary['summary']['skipped_non_trading_day']}",
        f"- blocked: {summary['summary']['blocked']}",
        f"- side effects: `{summary['side_effects']}`",
        "",
        "## Days",
        "",
    ]
    for day in summary["days"]:
        data_requirements = day.get("data_requirements") if isinstance(day, dict) else None
        requirement_note = ""
        if isinstance(data_requirements, dict) and data_requirements.get("missing_intraday_symbols"):
            requirement_note = f" / missing intraday `{data_requirements['missing_intraday_symbols']}`"
        lines.append(
            f"- {day['date']}: `{day['status']}` / calendar `{day['calendar_status']}` / "
            f"smoke `{day.get('end_to_end_smoke_status')}`{requirement_note}"
        )
    lines.extend(
        [
            "",
            "## Gate Notes",
            "",
            "- This smoke only proves artifact-chain stability.",
            "- Shioaji simulation login/order/cancel side effects remain disabled.",
            "- GitHub publish and Telegram operator-link send remain dry-run only.",
        ]
    )
    return "\n".join(lines) + "\n"


def cmd_simulate_trading_day_cycle_smoke(args: argparse.Namespace) -> None:
    """Run a multi-day stability smoke over the dry-run trading-day cycle."""
    from datetime import datetime

    dates = [item.strip() for item in str(args.dates).split(",") if item.strip()]
    if not dates:
        raise ValueError("--dates must include at least one YYYY-MM-DD value")

    output_dir = Path(args.output_dir) if args.output_dir else Path("reports") / "trading-day-cycle-smoke"
    summary_output = Path(args.summary_output) if args.summary_output else output_dir / "multi_day_smoke_summary.json"
    report_output = Path(args.report_output) if args.report_output else output_dir / "multi_day_smoke.md"
    run_id = args.run_id or f"tdc-smoke-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    days: list[dict[str, object]] = []

    for trading_date in dates:
        day_output = output_dir / trading_date
        candidates_input = _format_date_pattern(args.candidates_input_pattern, trading_date)
        state_output = day_output / "trading_day_run_state.json"
        cycle_args = argparse.Namespace(
            date=trading_date,
            cache_dir=args.cache_dir,
            trading_data_input=None,
            market_proxy_stock_id=args.market_proxy_stock_id,
            candidates_input=str(candidates_input) if candidates_input.exists() else None,
            intraday_bars_input=None,
            intraday_cache_dataset=args.intraday_cache_dataset,
            position_state_input=args.position_state_input,
            position_state_output=None,
            output_dir=str(day_output),
            state_output=str(state_output),
            watch_events_output=None,
            order_intents_output=None,
            report_output=None,
            next_candidates_output=None,
            end_to_end_smoke_output=None,
            run_id=f"{run_id}:{trading_date}",
            start_policy=args.start_policy,
            hard_stop_time=args.hard_stop_time,
            close_buffer_end_time=args.close_buffer_end_time,
            report_time=args.report_time,
            next_candidate_time=args.next_candidate_time,
            current_time=args.current_time,
            max_retries=args.max_retries,
            run_all_stages=args.run_all_stages,
            strategy_observation_minutes=args.strategy_observation_minutes,
            strategy_volume_surge_ratio=args.strategy_volume_surge_ratio,
            max_open_positions=args.max_open_positions,
            daily_risk_stop_r=args.daily_risk_stop_r,
        )
        cmd_simulate_trading_day_cycle(cycle_args)
        state = json.loads(state_output.read_text(encoding="utf-8"))
        smoke_status = None
        smoke_path = state.get("end_to_end_smoke_artifact", {}).get("path") if isinstance(state.get("end_to_end_smoke_artifact"), dict) else None
        if smoke_path and Path(str(smoke_path)).exists():
            smoke_payload = json.loads(Path(str(smoke_path)).read_text(encoding="utf-8"))
            smoke_status = smoke_payload.get("status")

        calendar_status = str(state.get("calendar_status"))
        data_requirements = _evaluate_trading_day_smoke_data_requirements(
            state,
            require_intraday_bars=bool(getattr(args, "require_intraday_bars", False)),
        )
        if calendar_status == "non_trading_day":
            status = "skipped_non_trading_day"
        elif data_requirements["status"] == "blocked":
            status = "blocked"
        elif smoke_status == "ok":
            status = "ok"
        else:
            status = "blocked"
        days.append(
            {
                "date": trading_date,
                "status": status,
                "calendar_status": calendar_status,
                "stage": state.get("stage"),
                "state_path": str(state_output),
                "candidate_path": str(candidates_input) if candidates_input.exists() else None,
                "end_to_end_smoke_status": smoke_status,
                "data_requirements": data_requirements,
                "manual_actions": state.get("manual_actions", []),
                "blocked_reasons": list(state.get("blocked_reasons", [])) + list(data_requirements["blocked_reasons"]),
            }
        )

    summary = {
        "run_id": run_id,
        "mode": "dry_run",
        "dates": dates,
        "days": days,
        "summary": {
            "total_dates": len(days),
            "ok": sum(1 for day in days if day["status"] == "ok"),
            "skipped_non_trading_day": sum(1 for day in days if day["status"] == "skipped_non_trading_day"),
            "blocked": sum(1 for day in days if day["status"] == "blocked"),
        },
        "gates": {
            "shioaji_simulation_side_effects": "disabled",
            "github_publish": "dry_run_only",
            "telegram_send": "dry_run_only",
        },
        "side_effects": [],
    }
    write_json(summary_output, summary)
    write_text(report_output, build_trading_day_cycle_smoke_markdown(summary))
    print("Trading day cycle multi-day smoke completed.")
    print(f"Run id: {run_id}")
    print(f"Dates: {len(days)}")
    print(f"OK: {summary['summary']['ok']}")
    print(f"Skipped non-trading: {summary['summary']['skipped_non_trading_day']}")
    print(f"Blocked: {summary['summary']['blocked']}")
    print(f"Summary: {summary_output}")
    print(f"Report: {report_output}")


def _evaluate_trading_day_smoke_data_requirements(
    state: dict[str, object],
    *,
    require_intraday_bars: bool,
) -> dict[str, object]:
    """Evaluate optional data coverage requirements for real raw-cache smoke runs."""
    result: dict[str, object] = {
        "require_intraday_bars": require_intraday_bars,
        "status": "ok",
        "missing_intraday_symbols": [],
        "blocked_reasons": [],
    }
    if not require_intraday_bars:
        return result
    adapter = state.get("intraday_data_adapter")
    if not isinstance(adapter, dict):
        result["status"] = "blocked"
        result["blocked_reasons"] = ["intraday_data_adapter_missing"]
        return result
    missing: list[str] = []
    for source in adapter.get("sources", []):
        if not isinstance(source, dict):
            continue
        symbol = str(source.get("symbol") or "")
        rows = int(source.get("rows") or 0)
        if symbol and rows <= 0:
            missing.append(symbol)
    if missing:
        result["status"] = "blocked"
        result["missing_intraday_symbols"] = missing
        result["blocked_reasons"] = ["required_intraday_bars_missing"]
    return result


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


def cmd_bars_build(args: argparse.Namespace) -> None:
    """Rebuild canonical 1m and 5m bars from the stored raw Shioaji ticks."""
    cache_dir = Path(args.cache_dir)
    symbols = _resolve_tick_smoke_symbols(args)
    if not symbols:
        raise ValueError("--symbols or --candidates-input is required")

    ticks = []
    sources = []
    for symbol in symbols:
        path = raw_tick_path(cache_dir, args.date, symbol)
        symbol_ticks = replay_raw_ticks(path)
        sources.append({"symbol": symbol, "path": str(path), "ticks": len(symbol_ticks)})
        ticks.extend(symbol_ticks)
    # Bars are built on event time, so a multi-symbol replay must be merged.
    ticks.sort(key=lambda item: (item.timestamp, item.symbol, item.sequence))

    minute_aggregator = OneMinuteBarAggregator(lateness_seconds=args.lateness_seconds)
    emitted_1m = []
    for tick in ticks:
        emitted_1m.extend(minute_aggregator.on_tick(tick))
    emitted_1m.extend(minute_aggregator.close_all())

    five_aggregator = FiveMinuteBarAggregator()
    emitted_5m = []
    for bar in emitted_1m:
        emitted_5m.extend(five_aggregator.on_bar(bar))
    emitted_5m.extend(five_aggregator.close_all())

    bars_1m = latest_bars(emitted_1m)
    bars_5m = latest_bars(emitted_5m)

    stored = []
    if args.store_dir:
        # Append every emission, corrections included, not just the latest view.
        stored = [str(path) for path in append_bars(Path(args.store_dir), emitted_1m + emitted_5m)]

    payload = {
        "trading_date": args.date,
        "sources": sources,
        "aggregator_1m": minute_aggregator.stats(),
        "aggregator_5m": five_aggregator.stats(),
        "volume_check": check_bar_volume(bars_1m, ticks),
        "stored": stored,
        "bars_1m": [bar.to_dict() for bar in bars_1m],
        "bars_5m": [bar.to_dict() for bar in bars_5m],
    }
    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-bars.json"
    write_json(output, payload)
    print(output)
    print(
        f"ticks: {len(ticks)}  1m: {len(bars_1m)}  5m: {len(bars_5m)}  "
        f"volume_consistent: {payload['volume_check']['consistent']}"
    )


def _resolve_tick_smoke_symbols(args: argparse.Namespace) -> list[str]:
    """Resolve the candidate-scoped symbol list for the tick stream smoke."""
    if args.symbols:
        return [item.strip() for item in str(args.symbols).split(",") if item.strip()]
    if args.candidates_input:
        return [candidate.symbol for candidate in load_candidate_scores(Path(args.candidates_input))]
    return []


def cmd_simulate_shioaji_tick_smoke(args: argparse.Namespace) -> None:
    """Run the explicitly gated Shioaji candidate-scoped tick stream smoke."""
    symbols = _resolve_tick_smoke_symbols(args)
    enabled = bool(args.enable_tick_stream)
    api_key = os.getenv(args.api_key_env) if enabled else None
    secret_key = os.getenv(args.secret_key_env) if enabled else None
    credentials_present = bool(api_key and secret_key)
    api = None
    blocked_reason = ""
    blocked_error = ""

    if enabled and credentials_present and symbols:
        try:
            import shioaji as sj  # type: ignore
        except Exception as error:
            blocked_reason, blocked_error = "shioaji_import_failed", str(error)
        else:
            try:
                api = sj.Shioaji(simulation=True)
                # Market data only: contracts are needed to subscribe, order callbacks are not.
                api.login(
                    api_key=api_key,
                    secret_key=secret_key,
                    fetch_contract=True,
                    subscribe_trade=False,
                )
            except Exception as error:
                blocked_reason, blocked_error = "shioaji_login_failed", str(error)
                api = None

    if blocked_reason:
        report: dict[str, object] = {
            "status": "blocked",
            "mode": "shioaji_tick_stream",
            "trading_date": args.date,
            "checks": {
                "gate_enabled": enabled,
                "credentials_present": credentials_present,
                "simulation_api": False,
                "candidate_scoped": True,
                "symbol_count": len(symbols),
                "orders_allowed": False,
            },
            "side_effects": [],
            "review_reason": blocked_reason,
            "error": blocked_error,
        }
    else:
        try:
            report = run_gated_shioaji_tick_stream_smoke(
                api=api,
                trading_date=args.date,
                symbols=symbols,
                enabled=enabled,
                credentials_present=credentials_present,
                cache_dir=Path(args.cache_dir) if args.cache_dir else None,
                duration_seconds=args.duration_seconds,
            )
        finally:
            if api is not None:
                try:
                    # Shioaji caps concurrent connections per person_id, so the
                    # session must not be left for process exit to clean up.
                    api.logout()
                except Exception:
                    pass

    output = Path(args.output) if args.output else Path("reports") / f"{args.date}-shioaji-tick-smoke.json"
    write_json(output, report)
    print(output)
    live = report.get("live_validation")
    if isinstance(live, dict):
        if live.get("passed"):
            print("live_validation: PASSED (P1_LIVE_VALIDATED)")
        else:
            print(f"live_validation: not met ({', '.join(live.get('failed', []))})")
    if report.get("status") in {"degraded", "failed"}:
        import sys

        # Fail closed: a smoke that lost ticks must not read as a pass.
        print(f"market data unhealthy: {report.get('health')}", file=sys.stderr)
        sys.exit(1)


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

    bars = subparsers.add_parser("bars")
    bars_sub = bars.add_subparsers(required=True)
    build = bars_sub.add_parser("build")
    build.add_argument("--date", required=True)
    build.add_argument("--symbols")
    build.add_argument("--candidates-input")
    build.add_argument("--cache-dir", default="data/raw")
    build.add_argument("--store-dir", default="data/bars")
    build.add_argument("--lateness-seconds", type=float, default=3.0)
    build.add_argument("--output")
    build.set_defaults(func=cmd_bars_build)

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
    trading_day_cycle.add_argument("--intraday-bars-input")
    trading_day_cycle.add_argument("--intraday-cache-dataset", default="TaiwanStockPriceMinute")
    trading_day_cycle.add_argument("--position-state-input")
    trading_day_cycle.add_argument("--position-state-output")
    trading_day_cycle.add_argument("--output-dir")
    trading_day_cycle.add_argument("--state-output")
    trading_day_cycle.add_argument("--watch-events-output")
    trading_day_cycle.add_argument("--order-intents-output")
    trading_day_cycle.add_argument("--report-output")
    trading_day_cycle.add_argument("--next-candidates-output")
    trading_day_cycle.add_argument("--end-to-end-smoke-output")
    trading_day_cycle.add_argument("--run-id")
    trading_day_cycle.add_argument("--start-policy", choices=("09:05", "10:00"), default="09:05")
    trading_day_cycle.add_argument("--hard-stop-time", default="13:20")
    trading_day_cycle.add_argument("--close-buffer-end-time", default="14:00")
    trading_day_cycle.add_argument("--report-time", default="15:00")
    trading_day_cycle.add_argument("--next-candidate-time", default="17:30")
    trading_day_cycle.add_argument("--current-time", default="09:05")
    trading_day_cycle.add_argument("--max-retries", type=int, default=2)
    trading_day_cycle.add_argument("--run-all-stages", action="store_true")
    trading_day_cycle.add_argument("--strategy-observation-minutes", type=int, default=15)
    trading_day_cycle.add_argument("--strategy-volume-surge-ratio", type=float, default=1.5)
    trading_day_cycle.add_argument("--max-open-positions", type=int, default=3)
    trading_day_cycle.add_argument("--daily-risk-stop-r", type=float, default=-3.0)
    trading_day_cycle.set_defaults(func=cmd_simulate_trading_day_cycle)

    trading_day_cycle_smoke = simulate_sub.add_parser("trading-day-cycle-smoke")
    trading_day_cycle_smoke.add_argument("--dates", required=True, help="Comma-separated YYYY-MM-DD list.")
    trading_day_cycle_smoke.add_argument("--cache-dir", default="data/raw")
    trading_day_cycle_smoke.add_argument("--market-proxy-stock-id", default="0050")
    trading_day_cycle_smoke.add_argument("--candidates-input-pattern", default="reports/{date}-candidates.json")
    trading_day_cycle_smoke.add_argument("--intraday-cache-dataset", default="TaiwanStockPriceMinute")
    trading_day_cycle_smoke.add_argument("--require-intraday-bars", action="store_true")
    trading_day_cycle_smoke.add_argument("--position-state-input")
    trading_day_cycle_smoke.add_argument("--output-dir")
    trading_day_cycle_smoke.add_argument("--summary-output")
    trading_day_cycle_smoke.add_argument("--report-output")
    trading_day_cycle_smoke.add_argument("--run-id")
    trading_day_cycle_smoke.add_argument("--start-policy", choices=("09:05", "10:00"), default="09:05")
    trading_day_cycle_smoke.add_argument("--hard-stop-time", default="13:20")
    trading_day_cycle_smoke.add_argument("--close-buffer-end-time", default="14:00")
    trading_day_cycle_smoke.add_argument("--report-time", default="15:00")
    trading_day_cycle_smoke.add_argument("--next-candidate-time", default="17:30")
    trading_day_cycle_smoke.add_argument("--current-time", default="09:20")
    trading_day_cycle_smoke.add_argument("--max-retries", type=int, default=2)
    trading_day_cycle_smoke.add_argument("--run-all-stages", action="store_true")
    trading_day_cycle_smoke.add_argument("--strategy-observation-minutes", type=int, default=15)
    trading_day_cycle_smoke.add_argument("--strategy-volume-surge-ratio", type=float, default=1.5)
    trading_day_cycle_smoke.add_argument("--max-open-positions", type=int, default=3)
    trading_day_cycle_smoke.add_argument("--daily-risk-stop-r", type=float, default=-3.0)
    trading_day_cycle_smoke.set_defaults(func=cmd_simulate_trading_day_cycle_smoke)

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
    shioaji_tick_smoke = simulate_sub.add_parser("shioaji-tick-smoke")
    shioaji_tick_smoke.add_argument("--date", required=True)
    shioaji_tick_smoke.add_argument("--candidates-input")
    shioaji_tick_smoke.add_argument("--symbols")
    shioaji_tick_smoke.add_argument("--cache-dir", default="data/raw")
    shioaji_tick_smoke.add_argument("--duration-seconds", type=float, default=60.0)
    shioaji_tick_smoke.add_argument("--api-key-env", default="SHIOAJI_API_KEY")
    shioaji_tick_smoke.add_argument("--secret-key-env", default="SHIOAJI_SECRET_KEY")
    shioaji_tick_smoke.add_argument("--enable-tick-stream", action="store_true")
    shioaji_tick_smoke.add_argument("--output")
    shioaji_tick_smoke.set_defaults(func=cmd_simulate_shioaji_tick_smoke)
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
