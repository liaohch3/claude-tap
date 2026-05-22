"""SQLite-backed trace storage (single local database)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_FILENAME = "traces.sqlite3"
SCHEMA_VERSION = 2
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_store: TraceStore | None = None
_store_lock = threading.Lock()


def resolve_db_path() -> Path:
    """Return the canonical local trace database path."""
    override = os.environ.get("CLOUDTAP_DB", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg_data:
        base = Path(xdg_data).expanduser() / "claude-tap"
    else:
        base = Path.home() / ".local" / "share" / "claude-tap"
    return (base / DB_FILENAME).resolve()


def get_trace_store() -> TraceStore:
    """Return the process-wide TraceStore singleton."""
    global _store
    with _store_lock:
        if _store is None:
            _store = TraceStore(resolve_db_path())
        return _store


def reset_trace_store() -> None:
    """Clear the process-wide TraceStore singleton (for tests)."""
    global _store
    with _store_lock:
        _store = None


class TraceStore:
    """Persist trace sessions, API records, and proxy logs in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def create_session(
        self,
        *,
        client: str = "",
        proxy_mode: str = "",
        started_at: datetime | None = None,
    ) -> str:
        """Create a new active trace session and return its id."""
        session_id = str(uuid.uuid4())
        now = started_at or datetime.now(timezone.utc)
        started_at_iso = now.isoformat()
        date_key = now.astimezone().date().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, started_at, updated_at, date_key, client, proxy_mode, status, record_count
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', 0)
                """,
                (session_id, started_at_iso, started_at_iso, date_key, client, proxy_mode),
            )
        return session_id

    def append_record(self, session_id: str, record: dict[str, Any]) -> None:
        """Append one API trace record to a session."""
        with self._connect() as conn:
            next_index = self._next_record_index(conn, session_id)
            updated_at = _str_or_none(record.get("timestamp")) or datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    next_index,
                    _int_or_none(record.get("turn")),
                    _str_or_none(record.get("timestamp")),
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?, record_count = record_count + 1
                WHERE id = ?
                """,
                (updated_at, session_id),
            )

    def append_log(
        self,
        session_id: str,
        message: str,
        *,
        level: str = "INFO",
        logged_at: str | None = None,
    ) -> None:
        """Append one proxy log line to a session."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(line_no), 0) + 1 AS next_line FROM proxy_logs WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            line_no = int(row["next_line"])
            conn.execute(
                """
                INSERT INTO proxy_logs (session_id, line_no, logged_at, level, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, line_no, logged_at, level, message),
            )

    def finalize_session(self, session_id: str, summary: dict[str, Any] | None = None) -> None:
        """Mark a session complete and persist its summary."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            status = "complete"
            if summary:
                record_count = summary.get("api_calls", 0)
                if record_count == 0:
                    status = "empty"
                elif summary.get("has_error"):
                    status = "error"
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(summary, ensure_ascii=False, separators=(",", ":")) if summary else None,
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                ),
            )

    def load_session_row(self, session_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def list_session_rows(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC, started_at DESC").fetchall()

    def load_records(
        self,
        session_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        offset = max(0, offset)
        params: list[object] = [session_id]
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ? OFFSET ?"
            params.append(max(0, limit))
            params.append(offset)
        elif offset:
            limit_sql = " LIMIT -1 OFFSET ?"
            params.append(offset)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM records
                WHERE session_id = ?
                ORDER BY record_index
                {limit_sql}
                """,
                params,
            ).fetchall()
        return _rows_to_records(rows)

    def load_logs(self, session_id: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT logged_at, level, message
                FROM proxy_logs
                WHERE session_id = ?
                ORDER BY line_no
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "logged_at": row["logged_at"] or "",
                "level": row["level"] or "",
                "message": row["message"] or "",
            }
            for row in rows
        ]

    def export_jsonl(self, session_id: str) -> str:
        records = self.load_records(session_id)
        return "\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + (
            "\n" if records else ""
        )

    def export_log(self, session_id: str) -> str:
        lines = []
        for entry in self.load_logs(session_id):
            timestamp = entry["logged_at"]
            message = entry["message"]
            if timestamp:
                lines.append(f"{timestamp} {message}")
            else:
                lines.append(message)
        return "\n".join(lines) + ("\n" if lines else "")

    def store_summary(self, session_id: str, summary: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET summary_json = ?, updated_at = ?, status = ?
                WHERE id = ?
                """,
                (
                    json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                    summary.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                    summary.get("status") or "complete",
                    session_id,
                ),
            )

    def dashboard_snapshot(self) -> dict[str, tuple[str, int, str]]:
        """Return session_id -> (updated_at, record_count, status) for change detection."""
        snapshot: dict[str, tuple[str, int, str]] = {}
        for row in self.list_session_rows():
            snapshot[row["id"]] = (
                row["updated_at"] or "",
                int(row["record_count"] or 0),
                row["status"] or "",
            )
        return snapshot

    def list_dates(self) -> tuple[list[str], bool]:
        dates: set[str] = set()
        has_legacy = False
        for row in self.list_session_rows():
            date_key = row["date_key"] or ""
            if _DATE_RE.match(date_key):
                dates.add(date_key)
            elif date_key == "legacy":
                has_legacy = True
        dates.add(datetime.now().date().isoformat())
        return sorted(dates, reverse=True), has_legacy

    def delete_sessions_by_date(
        self, date_key: str, *, protected_session_ids: set[str] | None = None
    ) -> dict[str, int]:
        protected = protected_session_ids or set()
        deleted_sessions = 0
        with self._connect() as conn:
            if date_key == "legacy":
                rows = conn.execute(
                    "SELECT id FROM sessions WHERE date_key = 'legacy' OR legacy_rel_path NOT LIKE '%/%'"
                ).fetchall()
            elif _DATE_RE.match(date_key):
                rows = conn.execute("SELECT id FROM sessions WHERE date_key = ?", (date_key,)).fetchall()
            else:
                raise ValueError("Invalid date format")

            for row in rows:
                session_id = row["id"]
                if session_id in protected:
                    continue
                conn.execute("DELETE FROM proxy_logs WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                deleted_sessions += 1
        return {"date": date_key, "deleted_sessions": deleted_sessions, "deleted_files": 0, "skipped_files": 0}

    def cleanup_old_sessions(self, max_sessions: int, *, protected_session_id: str | None = None) -> int:
        if max_sessions <= 0:
            return 0
        protected = {protected_session_id} if protected_session_id else set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM sessions
                ORDER BY started_at ASC
                """
            ).fetchall()
        if len(rows) <= max_sessions:
            return 0
        to_remove = rows[: len(rows) - max_sessions]
        removed = 0
        for row in to_remove:
            session_id = row["id"]
            if session_id in protected:
                continue
            with self._connect() as conn:
                conn.execute("DELETE FROM proxy_logs WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM records WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            removed += 1
        return removed

    def migrate_legacy_directory(self, output_dir: Path) -> int:
        """Import legacy JSONL/log files from a directory tree."""
        output_dir = output_dir.resolve()
        if not output_dir.is_dir():
            return 0

        imported = 0
        for trace_path in sorted(output_dir.glob("**/trace_*.jsonl")):
            rel_path = trace_path.relative_to(output_dir).as_posix()
            if self._legacy_session_exists(rel_path):
                continue
            records = _read_jsonl_file(trace_path)
            log_path = trace_path.with_suffix(".log")
            logs = _read_log_file(log_path) if log_path.is_file() else []
            manifest_entry = _manifest_entry_for_rel_path(output_dir, rel_path)
            session_id = self._import_legacy_session(
                rel_path=rel_path,
                trace_path=trace_path,
                records=records,
                logs=logs,
                manifest_entry=manifest_entry,
            )
            if session_id:
                imported += 1

        return imported

    def _import_legacy_session(
        self,
        *,
        rel_path: str,
        trace_path: Path,
        records: list[dict[str, Any]],
        logs: list[str],
        manifest_entry: dict[str, Any],
    ) -> str | None:
        session_id = str(uuid.uuid4())
        stat = trace_path.stat()
        started_at = _legacy_started_at(trace_path, records, manifest_entry, stat.st_mtime)
        date_key = trace_path.parent.name if _DATE_RE.match(trace_path.parent.name) else "legacy"
        client = ""
        proxy_mode = ""
        if isinstance(manifest_entry.get("client"), str):
            client = manifest_entry["client"]
        if isinstance(manifest_entry.get("proxy_mode"), str):
            proxy_mode = manifest_entry["proxy_mode"]
        if not client and records:
            capture = records[0].get("capture")
            if isinstance(capture, dict):
                client = str(capture.get("client") or "")
                proxy_mode = str(capture.get("proxy_mode") or "")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, started_at, updated_at, date_key, client, proxy_mode,
                    status, record_count, legacy_rel_path
                )
                VALUES (?, ?, ?, ?, ?, ?, 'complete', ?, ?)
                """,
                (
                    session_id,
                    started_at,
                    started_at,
                    date_key,
                    client,
                    proxy_mode,
                    len(records),
                    rel_path,
                ),
            )
            conn.executemany(
                """
                INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        index,
                        _int_or_none(record.get("turn")),
                        _str_or_none(record.get("timestamp")),
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                    )
                    for index, record in enumerate(records, start=1)
                ],
            )
            conn.executemany(
                """
                INSERT INTO proxy_logs (session_id, line_no, logged_at, level, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (session_id, index, _parse_log_timestamp(line), "INFO", _parse_log_message(line))
                    for index, line in enumerate(logs, start=1)
                ],
            )
        return session_id

    def _legacy_session_exists(self, rel_path: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE legacy_rel_path = ? LIMIT 1",
                (rel_path,),
            ).fetchone()
        return row is not None

    def _migration_done(self, marker: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM migration_state WHERE key = ?",
                (marker,),
            ).fetchone()
        return row is not None

    def _mark_migration_done(self, marker: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO migration_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (marker, datetime.now(timezone.utc).isoformat()),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self._create_v2_schema(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        if current != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported trace database schema version {current}; expected {SCHEMA_VERSION}.")
        self._create_v2_schema(conn)

    def _create_v2_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                date_key TEXT NOT NULL,
                client TEXT NOT NULL DEFAULT '',
                proxy_mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                record_count INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT,
                legacy_rel_path TEXT UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                session_id TEXT NOT NULL,
                record_index INTEGER NOT NULL,
                turn INTEGER,
                timestamp TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (session_id, record_index),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_logs (
                session_id TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                logged_at TEXT,
                level TEXT,
                message TEXT NOT NULL,
                PRIMARY KEY (session_id, line_no),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_date_key ON sessions(date_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_session_id ON records(session_id)")

    def _next_record_index(self, conn: sqlite3.Connection, session_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(record_index), 0) + 1 AS next_index FROM records WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["next_index"])


def _rows_to_records(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            record = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _read_log_file(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _manifest_entry_for_rel_path(output_dir: Path, rel_path: str) -> dict[str, Any]:
    manifest_path = output_dir / ".cloudtap-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    for entry in manifest.get("traces", []):
        if not isinstance(entry, dict):
            continue
        for file_name in entry.get("files", []):
            if isinstance(file_name, str) and file_name.replace("\\", "/") == rel_path:
                return entry
    return {}


def _legacy_started_at(
    trace_path: Path,
    records: list[dict[str, Any]],
    manifest_entry: dict[str, Any],
    mtime: float,
) -> str:
    if records:
        timestamp = records[0].get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            return timestamp
    created_at = manifest_entry.get("created_at")
    if isinstance(created_at, str) and created_at:
        return created_at
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _parse_log_timestamp(line: str) -> str | None:
    match = re.match(r"^(\d{2}:\d{2}:\d{2})\s", line)
    return match.group(1) if match else None


def _parse_log_message(line: str) -> str:
    match = re.match(r"^\d{2}:\d{2}:\d{2}\s+(.*)$", line)
    return match.group(1) if match else line


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
