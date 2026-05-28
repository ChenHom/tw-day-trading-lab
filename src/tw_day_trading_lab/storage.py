from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .models import CandidateScore


SAMPLE_COLUMNS = (
    "sample_id",
    "trading_date",
    "source",
    "symbol",
    "candidate_source",
    "archetype",
    "strategy_id",
    "setup_id",
    "idempotency_key",
    "entry_ts",
    "entry_price",
    "exit_ts",
    "exit_price",
    "exit_reason",
    "stop_price",
    "mfe_r",
    "mae_r",
    "realized_r_gross",
    "estimated_cost_r",
    "realized_r_net",
    "validity",
    "exclusion_reason",
    "position_id",
    "event_count",
    "warnings",
)

CANDIDATE_ITEM_COLUMNS = (
    "run_id",
    "symbol",
    "name",
    "rank_no",
    "archetype",
    "total_score",
    "next_day_actionable",
    "reasons",
    "downgrade_reasons",
)


class StorageError(RuntimeError):
    """Raised when a repository operation fails instead of swallowing DB errors."""


class SampleRepository(Protocol):
    """Port for storing and summarizing classified strategy samples."""

    def save_samples(self, samples: Sequence[Mapping[str, Any]]) -> None:
        """Persist classified samples."""

    def fetch_sample_summary(self) -> dict[str, Any]:
        """Read validity and exclusion reason counts."""


class CandidateRepository(Protocol):
    """Port for storing candidate run metadata and ranked items."""

    def save_candidate_run(
        self,
        *,
        run_id: str,
        trading_date: str,
        source: str,
        status: str,
        candidates: Sequence[CandidateScore | Mapping[str, Any]],
        notes: str | None = None,
    ) -> None:
        """Persist one candidate run and its ranked candidates."""

    def fetch_candidate_run_summary(self, run_id: str) -> dict[str, Any]:
        """Read item counts for one candidate run."""


@dataclass(frozen=True)
class TiDBConfig:
    """Connection settings for local or remote TiDB using the MySQL protocol."""

    host: str = "127.0.0.1"
    port: int = 4000
    user: str = "root"
    password: str = ""
    database: str = "tw_day_trading_lab"

    @classmethod
    def from_env(cls) -> "TiDBConfig":
        """Create TiDB config from environment variables with local-safe defaults."""
        return cls(
            host=os.getenv("TW_DAYTRADE_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("TW_DAYTRADE_DB_PORT", "4000")),
            user=os.getenv("TW_DAYTRADE_DB_USER", "root"),
            password=os.getenv("TW_DAYTRADE_DB_PASSWORD", ""),
            database=os.getenv("TW_DAYTRADE_DB_NAME", "tw_day_trading_lab"),
        )


def connect_tidb(config: TiDBConfig, *, use_database: bool = True):
    """Open a TiDB connection through mysql.connector without importing it globally."""
    try:
        import mysql.connector
    except ModuleNotFoundError as exc:
        raise StorageError("mysql.connector is required for TiDB storage") from exc

    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "connection_timeout": 10,
    }
    if use_database:
        kwargs["database"] = config.database
    try:
        return mysql.connector.connect(**kwargs)
    except Exception as exc:  # pragma: no cover - driver-specific exception types vary
        raise StorageError(f"connect_tidb failed: {exc}") from exc


def split_sql_statements(script: str) -> list[str]:
    """Split simple SQL migration scripts into executable statements."""
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def apply_schema_script(connection: Any, script: str) -> None:
    """Apply a MySQL/TiDB schema script statement by statement."""
    cursor = connection.cursor()
    try:
        for statement in split_sql_statements(script):
            cursor.execute(statement)
        connection.commit()
    except Exception as exc:
        _rollback(connection)
        raise StorageError(f"apply_schema_script failed: {exc}") from exc
    finally:
        cursor.close()


def apply_schema_file(connection: Any, path: Path, *, database: str | None = None) -> None:
    """Read and apply a SQL schema file."""
    script = path.read_text(encoding="utf-8")
    if database is not None:
        script = render_schema_script(script, database)
    apply_schema_script(connection, script)


def render_schema_script(script: str, database: str) -> str:
    """Render the schema script for the configured TiDB database name."""
    safe_database = validate_sql_identifier(database)
    return script.replace("tw_day_trading_lab", safe_database)


def validate_sql_identifier(identifier: str) -> str:
    """Accept only simple SQL identifiers for generated schema statements."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier}")
    return identifier


def create_sqlite_schema(connection: Any) -> None:
    """Create a SQLite-compatible test schema with the same repository contract."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidate_runs (
          run_id TEXT PRIMARY KEY,
          trading_date TEXT NOT NULL,
          source TEXT NOT NULL,
          generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          status TEXT NOT NULL,
          notes TEXT NULL
        );

        CREATE TABLE IF NOT EXISTS candidate_items (
          run_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          name TEXT NOT NULL,
          rank_no INTEGER NOT NULL,
          archetype TEXT NOT NULL,
          total_score REAL NOT NULL,
          next_day_actionable INTEGER NOT NULL DEFAULT 0,
          reasons TEXT NULL,
          downgrade_reasons TEXT NULL,
          PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS valid_samples (
          sample_id TEXT PRIMARY KEY,
          trading_date TEXT NOT NULL,
          source TEXT NOT NULL,
          symbol TEXT NOT NULL,
          candidate_source TEXT NOT NULL,
          archetype TEXT NOT NULL,
          strategy_id TEXT NOT NULL,
          setup_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          entry_ts TEXT NULL,
          entry_price REAL NULL,
          exit_ts TEXT NULL,
          exit_price REAL NULL,
          exit_reason TEXT NULL,
          stop_price REAL NULL,
          mfe_r REAL NULL,
          mae_r REAL NULL,
          realized_r_gross REAL NULL,
          estimated_cost_r REAL NULL,
          realized_r_net REAL NULL,
          validity TEXT NOT NULL,
          exclusion_reason TEXT NULL,
          position_id TEXT NULL,
          event_count INTEGER NOT NULL DEFAULT 0,
          warnings TEXT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sample_idempotency
          ON valid_samples (idempotency_key);
        """
    )
    connection.commit()


class DatabaseStorage:
    """DB-API repository adapter for SQLite tests and TiDB runtime."""

    def __init__(self, connection: Any, *, dialect: str) -> None:
        if dialect not in {"sqlite", "tidb"}:
            raise ValueError("dialect must be sqlite or tidb")
        self.connection = connection
        self.dialect = dialect
        self.placeholder = "?" if dialect == "sqlite" else "%s"

    def save_samples(self, samples: Sequence[Mapping[str, Any]]) -> None:
        """Persist classified samples, preserving duplicate idempotency evidence."""
        rows = [sample_to_row(sample) for sample in samples]
        if not rows:
            return
        self._execute_many(
            self._upsert_sql("valid_samples", SAMPLE_COLUMNS, "sample_id"),
            [tuple(row[column] for column in SAMPLE_COLUMNS) for row in rows],
            "save_samples",
        )

    def fetch_sample_summary(self) -> dict[str, Any]:
        """Return validity totals and exclusion reason counts from persisted samples."""
        try:
            validity_counts = self._fetch_counts(
                "SELECT validity, COUNT(*) FROM valid_samples GROUP BY validity"
            )
            reason_counts = self._fetch_counts(
                """
                SELECT exclusion_reason, COUNT(*)
                FROM valid_samples
                WHERE exclusion_reason IS NOT NULL
                GROUP BY exclusion_reason
                """
            )
        except Exception as exc:
            raise StorageError(f"fetch_sample_summary failed: {exc}") from exc
        return {
            "valid": validity_counts.get("valid", 0),
            "excluded": validity_counts.get("excluded", 0),
            "needs_review": validity_counts.get("needs_review", 0),
            "total": sum(validity_counts.values()),
            "reasons": dict(sorted(reason_counts.items())),
        }

    def save_candidate_run(
        self,
        *,
        run_id: str,
        trading_date: str,
        source: str,
        status: str,
        candidates: Sequence[CandidateScore | Mapping[str, Any]],
        notes: str | None = None,
    ) -> None:
        """Persist one candidate run and its item rows in one transaction."""
        candidate_rows = [candidate_to_row(run_id, candidate) for candidate in candidates]
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                self._upsert_sql(
                    "candidate_runs",
                    ("run_id", "trading_date", "source", "status", "notes"),
                    "run_id",
                ),
                (run_id, trading_date, source, status, notes),
            )
            if candidate_rows:
                cursor.executemany(
                    self._upsert_sql("candidate_items", CANDIDATE_ITEM_COLUMNS, "run_id,symbol"),
                    [
                        tuple(row[column] for column in CANDIDATE_ITEM_COLUMNS)
                        for row in candidate_rows
                    ],
                )
            self.connection.commit()
        except Exception as exc:
            _rollback(self.connection)
            raise StorageError(f"save_candidate_run failed: {exc}") from exc
        finally:
            if cursor is not None:
                cursor.close()

    def fetch_candidate_run_summary(self, run_id: str) -> dict[str, Any]:
        """Return candidate item totals for one run."""
        sql = (
            "SELECT COUNT(*), COALESCE(SUM(next_day_actionable), 0) "
            "FROM candidate_items WHERE run_id = " + self.placeholder
        )
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(sql, (run_id,))
            row = cursor.fetchone()
        except Exception as exc:
            raise StorageError(f"fetch_candidate_run_summary failed: {exc}") from exc
        finally:
            if cursor is not None:
                cursor.close()
        return {
            "run_id": run_id,
            "item_count": int(row[0] or 0),
            "actionable_count": int(row[1] or 0),
        }

    def _execute_many(self, sql: str, rows: Sequence[tuple[Any, ...]], operation: str) -> None:
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.executemany(sql, rows)
            self.connection.commit()
        except Exception as exc:
            _rollback(self.connection)
            raise StorageError(f"{operation} failed: {exc}") from exc
        finally:
            if cursor is not None:
                cursor.close()

    def _fetch_counts(self, sql: str) -> dict[str, int]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            return {str(key): int(count) for key, count in cursor.fetchall() if key is not None}
        finally:
            cursor.close()

    def _upsert_sql(self, table: str, columns: Sequence[str], key: str) -> str:
        names = ", ".join(columns)
        placeholders = ", ".join([self.placeholder] * len(columns))
        update_columns = [column for column in columns if column not in key.split(",")]
        if self.dialect == "sqlite":
            updates = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
            return (
                f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
                f"ON CONFLICT({key}) DO UPDATE SET {updates}"
            )
        updates = ", ".join(f"{column}=VALUES({column})" for column in update_columns)
        return (
            f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {updates}"
        )


def sample_to_row(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a sample payload into storage columns."""
    row = {column: sample.get(column) for column in SAMPLE_COLUMNS}
    row["entry_ts"] = normalize_datetime_text(row["entry_ts"])
    row["exit_ts"] = normalize_datetime_text(row["exit_ts"])
    row["event_count"] = int(row["event_count"] or 0)
    row["warnings"] = json.dumps(row.get("warnings") or [], ensure_ascii=False)
    return row


def candidate_to_row(run_id: str, candidate: CandidateScore | Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a candidate score into storage columns."""
    data = candidate.to_dict() if isinstance(candidate, CandidateScore) else dict(candidate)
    return {
        "run_id": run_id,
        "symbol": str(data["symbol"]),
        "name": str(data["name"]),
        "rank_no": int(data["rank"]),
        "archetype": str(data["archetype"]),
        "total_score": float(data["total_score"]),
        "next_day_actionable": 1 if data.get("next_day_actionable") else 0,
        "reasons": json.dumps(data.get("reasons", []), ensure_ascii=False),
        "downgrade_reasons": json.dumps(data.get("downgrade_reasons", []), ensure_ascii=False),
    }


def normalize_datetime_text(value: Any) -> str | None:
    """Convert ISO datetime text into a MySQL/TiDB friendly DATETIME string."""
    if value is None:
        return None
    text = str(value).strip()
    return text.replace("T", " ") if text else None


def load_import_payload(path: Path) -> dict[str, Any]:
    """Load old-log importer JSON output from disk."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("import payload must be a JSON object")
    return raw


def load_candidate_payload(path: Path) -> list[dict[str, Any]]:
    """Load candidate JSON output from disk and unwrap the CLI payload shape."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("candidates", [])
    if not isinstance(raw, list):
        raise ValueError("candidate payload must be a JSON list or {candidates: [...]}")
    return [dict(item) for item in raw]


def persist_import_samples(
    repository: SampleRepository,
    import_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist old-log samples through a repository port and return DB summary."""
    samples = import_payload.get("samples", [])
    if not isinstance(samples, Sequence):
        raise ValueError("import payload samples must be a list")
    repository.save_samples([dict(sample) for sample in samples])
    return repository.fetch_sample_summary()


def persist_candidate_run(
    repository: CandidateRepository,
    *,
    run_id: str,
    trading_date: str,
    source: str,
    status: str,
    candidates: Sequence[CandidateScore | Mapping[str, Any]],
    notes: str | None = None,
) -> dict[str, Any]:
    """Persist candidate output through a repository port and return run summary."""
    repository.save_candidate_run(
        run_id=run_id,
        trading_date=trading_date,
        source=source,
        status=status,
        candidates=candidates,
        notes=notes,
    )
    return repository.fetch_candidate_run_summary(run_id)


def _rollback(connection: Any) -> None:
    try:
        connection.rollback()
    except Exception:
        return
