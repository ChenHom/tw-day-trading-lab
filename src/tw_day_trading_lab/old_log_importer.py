from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any


MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(13, 30)
DEFAULT_STRATEGY_ID = "old-log-v1"


def parse_float(value: object) -> float | None:
    """Convert CSV numeric cells into floats while treating blanks as missing."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_timestamp(value: object) -> datetime | None:
    """Parse the old ISO-like timestamp format and return None for malformed cells."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_context(value: object) -> dict[str, Any]:
    """Parse the old JSON context column without letting malformed JSON break import."""
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_exit_action(action: str) -> bool:
    """Return true for all old exit actions such as EXIT_STOP_LOSS."""
    return action.upper().startswith("EXIT")


def is_debug_or_forced_event(event: "TradeLogEvent") -> bool:
    """Detect debug, forced, and test entries that must not enter expectancy."""
    haystack = " ".join(
        [
            event.reason,
            str(event.context.get("source_tag", "")),
            str(event.context.get("selection_reason", "")),
        ]
    ).lower()
    markers = ("debug", "forced", "test", "測試", "強制", "盤後")
    return any(marker in haystack for marker in markers)


def is_after_hours(timestamp: datetime | None) -> bool:
    """Identify entries outside Taiwan regular trading hours for this MVP."""
    if timestamp is None:
        return False
    current_time = timestamp.time()
    return current_time < MARKET_OPEN or current_time > MARKET_CLOSE


@dataclass(frozen=True)
class TradeLogEvent:
    """One normalized row from the old trade_decisions CSV."""

    row_id: str
    position_id: str
    timestamp: datetime | None
    timestamp_raw: str
    symbol: str
    action: str
    price: float | None
    reason: str
    entry_price: float | None
    stop_loss: float | None
    position_size: float | None
    mfe: float | None
    mae: float | None
    realized_r: float | None
    context: dict[str, Any]
    source_file: str
    row_number: int

    @classmethod
    def from_row(
        cls,
        row: dict[str, Any],
        *,
        source_file: str,
        row_number: int,
    ) -> "TradeLogEvent":
        """Normalize a CSV row into typed values without classifying it yet."""
        timestamp_raw = str(row.get("timestamp", "")).strip()
        return cls(
            row_id=str(row.get("id", "")).strip(),
            position_id=str(row.get("position_id", "")).strip(),
            timestamp=parse_timestamp(timestamp_raw),
            timestamp_raw=timestamp_raw,
            symbol=str(row.get("symbol", "")).strip(),
            action=str(row.get("action", "")).strip().upper(),
            price=parse_float(row.get("price")),
            reason=str(row.get("reason", "")).strip(),
            entry_price=parse_float(row.get("entry_price")),
            stop_loss=parse_float(row.get("stop_loss")),
            position_size=parse_float(row.get("position_size")),
            mfe=parse_float(row.get("mfe")),
            mae=parse_float(row.get("mae")),
            realized_r=parse_float(row.get("realized_r")),
            context=parse_context(row.get("context")),
            source_file=source_file,
            row_number=row_number,
        )


@dataclass(frozen=True)
class TradeLifecycle:
    """A classified trade sample compatible with the valid_samples contract."""

    sample_id: str
    trading_date: str
    source: str
    symbol: str
    candidate_source: str
    archetype: str
    strategy_id: str
    setup_id: str
    idempotency_key: str
    entry_ts: str | None
    entry_price: float | None
    exit_ts: str | None
    exit_price: float | None
    exit_reason: str | None
    stop_price: float | None
    mfe_r: float | None
    mae_r: float | None
    realized_r_gross: float | None
    estimated_cost_r: float | None
    realized_r_net: float | None
    validity: str
    exclusion_reason: str | None
    position_id: str
    event_count: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for reports and DB adapters."""
        return {
            "sample_id": self.sample_id,
            "trading_date": self.trading_date,
            "source": self.source,
            "symbol": self.symbol,
            "candidate_source": self.candidate_source,
            "archetype": self.archetype,
            "strategy_id": self.strategy_id,
            "setup_id": self.setup_id,
            "idempotency_key": self.idempotency_key,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "exit_ts": self.exit_ts,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "stop_price": self.stop_price,
            "mfe_r": self.mfe_r,
            "mae_r": self.mae_r,
            "realized_r_gross": self.realized_r_gross,
            "estimated_cost_r": self.estimated_cost_r,
            "realized_r_net": self.realized_r_net,
            "validity": self.validity,
            "exclusion_reason": self.exclusion_reason,
            "position_id": self.position_id,
            "event_count": self.event_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TradeImportResult:
    """The full import output: raw event count, classified samples, and duplicate groups."""

    source_path: str
    events_count: int
    samples: list[TradeLifecycle]
    duplicate_enter_groups: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        """Return JSON output for CLI export."""
        return {
            "source_path": self.source_path,
            "events_count": self.events_count,
            "summary": summarize_samples(self.samples),
            "duplicate_enter_groups": self.duplicate_enter_groups,
            "samples": [sample.to_dict() for sample in self.samples],
        }


def load_csv_events(path: Path) -> list[TradeLogEvent]:
    """Read an old trade_decisions CSV from disk and normalize each row."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            TradeLogEvent.from_row(row, source_file=str(path), row_number=row_number)
            for row_number, row in enumerate(reader, start=2)
        ]


def make_duplicate_enter_index(events: list[TradeLogEvent]) -> dict[str, list[str]]:
    """Index ENTER rows sharing the same symbol and timestamp."""
    groups: dict[tuple[str, str], list[TradeLogEvent]] = defaultdict(list)
    for event in events:
        if event.action != "ENTER":
            continue
        timestamp_key = event.timestamp.isoformat() if event.timestamp else event.timestamp_raw
        groups[(event.symbol, timestamp_key)].append(event)

    duplicate_groups: dict[str, list[str]] = {}
    for (symbol, timestamp_key), group_events in groups.items():
        if len(group_events) < 2:
            continue
        key = f"{symbol}@{timestamp_key}"
        duplicate_groups[key] = [event.position_id for event in group_events if event.position_id]
    return duplicate_groups


def classify_lifecycle(
    entry: TradeLogEvent,
    exits: list[TradeLogEvent],
    duplicate_keys: set[str],
) -> tuple[str, str | None, tuple[str, ...]]:
    """Classify one entry lifecycle as valid, excluded, or needs_review."""
    warnings: list[str] = []
    duplicate_key = _duplicate_key(entry)

    if entry.timestamp is None:
        return "needs_review", "malformed_timestamp", tuple(warnings)
    if is_debug_or_forced_event(entry):
        return "excluded", "debug_or_forced_order", tuple(warnings)
    if is_after_hours(entry.timestamp):
        return "excluded", "after_hours", tuple(warnings)
    if duplicate_key in duplicate_keys:
        return "excluded", "duplicate_enter_same_symbol_timestamp", tuple(warnings)
    if not entry.position_id:
        return "needs_review", "missing_position_id", tuple(warnings)
    if not exits:
        return "needs_review", "missing_exit", tuple(warnings)
    if any(exit_event.timestamp is None for exit_event in exits):
        return "needs_review", "malformed_exit_timestamp", tuple(warnings)

    return "valid", None, tuple(warnings)


def normalize_lifecycles(
    events: list[TradeLogEvent],
    *,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    source: str = "old_log",
    estimated_cost_r: float = 0.0,
) -> list[TradeLifecycle]:
    """Convert old ENTER/EXIT events into classified trade lifecycles."""
    exits_by_position: dict[str, list[TradeLogEvent]] = defaultdict(list)
    for event in events:
        if event.position_id and is_exit_action(event.action):
            exits_by_position[event.position_id].append(event)

    duplicate_enter_groups = make_duplicate_enter_index(events)
    duplicate_keys = set(duplicate_enter_groups)
    samples: list[TradeLifecycle] = []
    for entry in events:
        if entry.action != "ENTER":
            continue
        exits = sorted(
            exits_by_position.get(entry.position_id, []),
            key=lambda event: event.timestamp or datetime.max,
        )
        validity, exclusion_reason, warnings = classify_lifecycle(entry, exits, duplicate_keys)
        samples.append(
            build_lifecycle(
                entry,
                exits,
                strategy_id=strategy_id,
                source=source,
                estimated_cost_r=estimated_cost_r,
                validity=validity,
                exclusion_reason=exclusion_reason,
                warnings=warnings,
            )
        )
    return samples


def build_lifecycle(
    entry: TradeLogEvent,
    exits: list[TradeLogEvent],
    *,
    strategy_id: str,
    source: str,
    estimated_cost_r: float,
    validity: str,
    exclusion_reason: str | None,
    warnings: tuple[str, ...],
) -> TradeLifecycle:
    """Build the normalized lifecycle object from one entry and its exits."""
    last_exit = exits[-1] if exits else None
    trading_date = entry.timestamp.date().isoformat() if entry.timestamp else "unknown"
    setup_id = make_setup_id(entry)
    gross_r = compute_realized_r(entry, exits)
    cost_r = estimated_cost_r if gross_r is not None else None
    net_r = round(gross_r - estimated_cost_r, 4) if gross_r is not None else None
    sample_id = entry.position_id or f"{trading_date}:{entry.symbol}:row-{entry.row_number}"
    exit_reason = "+".join(sorted({event.action for event in exits})) if exits else None

    return TradeLifecycle(
        sample_id=sample_id,
        trading_date=trading_date,
        source=source,
        symbol=entry.symbol,
        candidate_source=str(entry.context.get("source_tag") or "old_log"),
        archetype="legacy_old_log",
        strategy_id=strategy_id,
        setup_id=setup_id,
        idempotency_key=f"{trading_date}:{strategy_id}:{entry.symbol}:{setup_id}:buy",
        entry_ts=entry.timestamp.isoformat() if entry.timestamp else None,
        entry_price=entry.entry_price if entry.entry_price is not None else entry.price,
        exit_ts=last_exit.timestamp.isoformat() if last_exit and last_exit.timestamp else None,
        exit_price=last_exit.price if last_exit else None,
        exit_reason=exit_reason,
        stop_price=entry.stop_loss,
        mfe_r=entry.mfe,
        mae_r=entry.mae,
        realized_r_gross=gross_r,
        estimated_cost_r=cost_r,
        realized_r_net=net_r,
        validity=validity,
        exclusion_reason=exclusion_reason,
        position_id=entry.position_id,
        event_count=1 + len(exits),
        warnings=warnings,
    )


def make_setup_id(entry: TradeLogEvent) -> str:
    """Create a setup id granular enough to catch same-minute duplicate entries."""
    if entry.timestamp is None:
        return "legacy_entry:unknown_time"
    return f"legacy_entry:{entry.timestamp.strftime('%H%M')}"


def compute_realized_r(entry: TradeLogEvent, exits: list[TradeLogEvent]) -> float | None:
    """Compute gross R, using position-size weighting for partial exits when possible."""
    realized_exits = [event for event in exits if event.realized_r is not None]
    if not realized_exits:
        return None
    if entry.position_size:
        weighted = 0.0
        weight_total = 0.0
        for event in realized_exits:
            if event.position_size is None:
                continue
            weight = event.position_size / entry.position_size
            weighted += (event.realized_r or 0.0) * weight
            weight_total += weight
        if weight_total > 0:
            return round(weighted, 4)
    return round(realized_exits[-1].realized_r or 0.0, 4)


def summarize_samples(samples: list[TradeLifecycle]) -> dict[str, Any]:
    """Count validity classes and exclusion reasons for reports."""
    validity_counts = Counter(sample.validity for sample in samples)
    reason_counts = Counter(
        sample.exclusion_reason for sample in samples if sample.exclusion_reason
    )
    return {
        "valid": validity_counts.get("valid", 0),
        "excluded": validity_counts.get("excluded", 0),
        "needs_review": validity_counts.get("needs_review", 0),
        "total": len(samples),
        "reasons": dict(sorted(reason_counts.items())),
    }


def import_trade_log_csv(path: Path) -> TradeImportResult:
    """Import one old trade_decisions CSV and classify all ENTER lifecycles."""
    events = load_csv_events(path)
    return TradeImportResult(
        source_path=str(path),
        events_count=len(events),
        samples=normalize_lifecycles(events),
        duplicate_enter_groups=make_duplicate_enter_index(events),
    )


def render_failure_replay_markdown(trading_date: str, result: TradeImportResult) -> str:
    """Render an audit report focused on failed and excluded old-log samples."""
    summary = summarize_samples(result.samples)
    lines = [
        f"# Old Log Failure Replay {trading_date}",
        "",
        f"- Source: `{result.source_path}`",
        f"- Events: {result.events_count}",
        f"- valid / excluded / needs_review: {summary['valid']} / {summary['excluded']} / {summary['needs_review']}",
        "",
        "## Classification Reasons",
        "",
        "| Reason | Count |",
        "|---|---:|",
    ]
    for reason, count in summary["reasons"].items():
        lines.append(f"| {reason} | {count} |")

    lines.extend(
        [
            "",
            "## Samples",
            "",
            "| Symbol | Position | Validity | Reason | Entry | Exit | Gross R |",
            "|---|---|---|---|---|---|---:|",
        ]
    )
    for sample in result.samples:
        gross_r = "-" if sample.realized_r_gross is None else f"{sample.realized_r_gross:.4f}"
        lines.append(
            "| "
            f"{sample.symbol} | {sample.position_id or '-'} | {sample.validity} | "
            f"{sample.exclusion_reason or '-'} | {sample.entry_ts or '-'} | "
            f"{sample.exit_ts or '-'} | {gross_r} |"
        )
    return "\n".join(lines) + "\n"


def _duplicate_key(event: TradeLogEvent) -> str:
    """Build the duplicate-enter key used by duplicate detection and classification."""
    timestamp_key = event.timestamp.isoformat() if event.timestamp else event.timestamp_raw
    return f"{event.symbol}@{timestamp_key}"
