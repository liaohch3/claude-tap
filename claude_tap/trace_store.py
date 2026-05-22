"""SQLite-backed trace history index."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_FILE = ".cloudtap-traces.sqlite3"
SCHEMA_VERSION = 1


class TraceStore:
    """Persist trace session summaries and raw records in SQLite."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.db_path = self.output_dir / DB_FILE

    def append_record(self, trace_path: Path, record: dict[str, Any]) -> None:
        """Append one raw record to the SQLite store without replacing JSONL."""
        rel_path = self._rel_path(trace_path)
        with self._connect() as conn:
            self._ensure_minimal_session(conn, rel_path, trace_path)
            next_index = self._next_record_index(conn, rel_path)
            conn.execute(
                """
                INSERT OR REPLACE INTO trace_records
                    (rel_path, record_index, turn, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    rel_path,
                    next_index,
                    _int_or_none(record.get("turn")),
                    _str_or_none(record.get("timestamp")),
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def session_needs_index(self, rel_path: str, trace_path: Path) -> bool:
        """Return true when the JSONL file should be parsed into SQLite."""
        stat = trace_path.stat()
        html_exists = trace_path.with_suffix(".html").exists()
        log_exists = trace_path.with_suffix(".log").exists()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT mtime_ns, size_bytes, html_exists, log_exists, summary_json
                FROM trace_sessions
                WHERE rel_path = ?
                """,
                (rel_path,),
            ).fetchone()
        if row is None or not row["summary_json"]:
            return True
        return (
            row["mtime_ns"] != stat.st_mtime_ns
            or row["size_bytes"] != stat.st_size
            or bool(row["html_exists"]) != html_exists
            or bool(row["log_exists"]) != log_exists
        )

    def replace_session(
        self,
        *,
        rel_path: str,
        trace_path: Path,
        records: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        """Replace one session's SQLite summary and raw records."""
        stat = trace_path.stat()
        html_exists = trace_path.with_suffix(".html").exists()
        log_exists = trace_path.with_suffix(".log").exists()
        with self._connect() as conn:
            self._ensure_minimal_session(conn, rel_path, trace_path)
            conn.execute("DELETE FROM trace_records WHERE rel_path = ?", (rel_path,))
            conn.executemany(
                """
                INSERT INTO trace_records
                    (rel_path, record_index, turn, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        rel_path,
                        index,
                        _int_or_none(record.get("turn")),
                        _str_or_none(record.get("timestamp")),
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    )
                    for index, record in enumerate(records, start=1)
                ],
            )
            conn.execute(
                """
                INSERT INTO trace_sessions (
                    rel_path, trace_path, mtime_ns, size_bytes, html_exists,
                    log_exists, summary_json, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    trace_path = excluded.trace_path,
                    mtime_ns = excluded.mtime_ns,
                    size_bytes = excluded.size_bytes,
                    html_exists = excluded.html_exists,
                    log_exists = excluded.log_exists,
                    summary_json = excluded.summary_json,
                    indexed_at = excluded.indexed_at
                """,
                (
                    rel_path,
                    str(trace_path),
                    stat.st_mtime_ns,
                    stat.st_size,
                    int(html_exists),
                    int(log_exists),
                    json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def delete_missing(self, known_rel_paths: set[str]) -> None:
        """Delete SQLite sessions whose JSONL file no longer exists."""
        with self._connect() as conn:
            rows = conn.execute("SELECT rel_path FROM trace_sessions").fetchall()
            stale = [row["rel_path"] for row in rows if row["rel_path"] not in known_rel_paths]
            if not stale:
                return
            conn.executemany("DELETE FROM trace_records WHERE rel_path = ?", [(rel,) for rel in stale])
            conn.executemany("DELETE FROM trace_sessions WHERE rel_path = ?", [(rel,) for rel in stale])

    def list_summaries(self) -> list[dict[str, Any]]:
        """Return stored session summaries."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT summary_json
                FROM trace_sessions
                WHERE summary_json IS NOT NULL
                """
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            try:
                summary = json.loads(row["summary_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(summary, dict):
                summaries.append(summary)
        return summaries

    def load_summary(self, rel_path: str) -> dict[str, Any] | None:
        """Return one stored session summary."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT summary_json FROM trace_sessions WHERE rel_path = ?",
                (rel_path,),
            ).fetchone()
        if row is None or not row["summary_json"]:
            return None
        try:
            summary = json.loads(row["summary_json"])
        except json.JSONDecodeError:
            return None
        return summary if isinstance(summary, dict) else None

    def load_records(self, rel_path: str) -> list[dict[str, Any]]:
        """Return raw records for one session."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM trace_records
                WHERE rel_path = ?
                ORDER BY record_index
                """,
                (rel_path,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            try:
                record = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def _connect(self) -> sqlite3.Connection:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 1000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_sessions (
                rel_path TEXT PRIMARY KEY,
                trace_path TEXT NOT NULL,
                mtime_ns INTEGER,
                size_bytes INTEGER,
                html_exists INTEGER NOT NULL DEFAULT 0,
                log_exists INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT,
                indexed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_records (
                rel_path TEXT NOT NULL,
                record_index INTEGER NOT NULL,
                turn INTEGER,
                timestamp TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (rel_path, record_index),
                FOREIGN KEY (rel_path) REFERENCES trace_sessions(rel_path)
                    ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_records_rel_path ON trace_records(rel_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trace_records_timestamp ON trace_records(timestamp)")

    def _ensure_minimal_session(self, conn: sqlite3.Connection, rel_path: str, trace_path: Path) -> None:
        conn.execute(
            """
            INSERT INTO trace_sessions (rel_path, trace_path)
            VALUES (?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET trace_path = excluded.trace_path
            """,
            (rel_path, str(trace_path)),
        )

    def _next_record_index(self, conn: sqlite3.Connection, rel_path: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(record_index), 0) + 1 AS next_index FROM trace_records WHERE rel_path = ?",
            (rel_path,),
        ).fetchone()
        return int(row["next_index"])

    def _rel_path(self, trace_path: Path) -> str:
        return trace_path.resolve().relative_to(self.output_dir).as_posix()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
