"""SQLite-backed trace storage (single local database)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

DB_FILENAME = "traces.sqlite3"
SCHEMA_VERSION = 3
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STALE_ACTIVE_SESSION_AFTER = timedelta(hours=24)

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
        if _store is not None:
            _store.close()
        _store = None


class TraceStore:
    """Persist trace sessions, API records, and proxy logs in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._tls = threading.local()

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
        with self._write_lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO sessions (
                    id, started_at, updated_at, date_key, client, proxy_mode, status, record_count
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', 0)
                """,
                (session_id, started_at_iso, started_at_iso, date_key, client, proxy_mode),
            )
            conn.commit()
        return session_id

    def append_record(self, session_id: str, record: dict[str, Any]) -> None:
        """Append one API trace record to a session."""
        with self._write_lock:
            conn = self._connect()
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
            count_row = conn.execute(
                "SELECT record_count FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            record_count = int(count_row["record_count"]) if count_row is not None else next_index
            self._refresh_summary_after_append(conn, session_id, record, record_count)
            conn.commit()

    def append_log(
        self,
        session_id: str,
        message: str,
        *,
        level: str = "INFO",
        logged_at: str | None = None,
    ) -> None:
        """Append one proxy log line to a session."""
        with self._write_lock:
            conn = self._connect()
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
            conn.execute(
                """
                UPDATE sessions
                SET updated_at = ?
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), session_id),
            )
            conn.commit()

    def finalize_session(self, session_id: str, summary: dict[str, Any] | None = None) -> None:
        """Mark a session complete and persist its summary."""
        with self._write_lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT status, summary_json FROM sessions WHERE id = ?",
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

            existing_summary = None
            if row["summary_json"]:
                try:
                    existing_summary = json.loads(row["summary_json"])
                except json.JSONDecodeError:
                    pass

            updated_at = datetime.now(timezone.utc).isoformat()
            if isinstance(existing_summary, dict):
                existing_summary["status"] = status
                existing_summary["id"] = session_id
                existing_summary["updated_at"] = updated_at
                summary_json_str = json.dumps(existing_summary, ensure_ascii=False, separators=(",", ":"))
            else:
                summary_json_str = json.dumps(summary, ensure_ascii=False, separators=(",", ":")) if summary else None

            conn.execute(
                """
                UPDATE sessions
                SET status = ?, summary_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    summary_json_str,
                    updated_at,
                    session_id,
                ),
            )
            conn.commit()

    def load_session_row(self, session_id: str) -> sqlite3.Row | None:
        conn = self._connect()
        return conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()

    def list_session_rows(self) -> list[sqlite3.Row]:
        conn = self._connect()
        return conn.execute(
            """
            SELECT * FROM sessions
            ORDER BY COALESCE(julianday(updated_at), 0) DESC,
                     COALESCE(julianday(started_at), 0) DESC,
                     id DESC
            """
        ).fetchall()

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
        conn = self._connect()
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

    def load_boundary_records(self, session_id: str) -> list[dict[str, Any]]:
        """Load the first and last records for a session without reading everything."""
        conn = self._connect()
        first = conn.execute(
            """
            SELECT payload_json
            FROM records
            WHERE session_id = ?
            ORDER BY record_index
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        last = conn.execute(
            """
            SELECT payload_json
            FROM records
            WHERE session_id = ?
            ORDER BY record_index DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if first is None:
            return []
        if last is None or first["payload_json"] == last["payload_json"]:
            return _rows_to_records([first])
        return _rows_to_records([first, last])

    def load_records_for_date(self, date_key: str) -> list[dict[str, Any]]:
        """Load all records for sessions on a given date in one query."""
        conn = self._connect()
        if date_key == "legacy":
            rows = conn.execute(
                """
                SELECT r.payload_json
                FROM records r
                INNER JOIN sessions s ON s.id = r.session_id
                WHERE s.date_key = 'legacy' OR s.legacy_rel_path NOT LIKE '%/%'
                ORDER BY s.started_at ASC, r.record_index ASC
                """
            ).fetchall()
        elif _DATE_RE.match(date_key):
            rows = conn.execute(
                """
                SELECT r.payload_json
                FROM records r
                INNER JOIN sessions s ON s.id = r.session_id
                WHERE s.date_key = ?
                ORDER BY s.started_at ASC, r.record_index ASC
                """,
                (date_key,),
            ).fetchall()
        else:
            raise ValueError("Invalid date format")
        return _rows_to_records(rows)

    def load_logs(self, session_id: str) -> list[dict[str, str]]:
        conn = self._connect()
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
        with self._write_lock:
            conn = self._connect()
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
            conn.commit()

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
    ) -> dict[str, int | str]:
        protected = protected_session_ids or set()
        with self._write_lock:
            conn = self._connect()
            if date_key == "legacy":
                rows = conn.execute(
                    "SELECT id FROM sessions WHERE date_key = 'legacy' OR legacy_rel_path NOT LIKE '%/%'"
                ).fetchall()
            elif _DATE_RE.match(date_key):
                rows = conn.execute("SELECT id FROM sessions WHERE date_key = ?", (date_key,)).fetchall()
            else:
                raise ValueError("Invalid date format")

            to_delete = [row["id"] for row in rows if row["id"] not in protected]
            skipped = [row["id"] for row in rows if row["id"] in protected]
            if to_delete:
                placeholders = ",".join("?" * len(to_delete))
                conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", to_delete)
            conn.commit()
        return {
            "date": date_key,
            "deleted_sessions": len(to_delete),
            "deleted_files": len(to_delete),
            "skipped_sessions": len(skipped),
            "skipped_files": len(skipped),
        }

    def cleanup_old_sessions(self, max_sessions: int, *, protected_session_id: str | None = None) -> int:
        if max_sessions <= 0:
            return 0
        protected = {protected_session_id} if protected_session_id else set()
        with self._write_lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT id, status, updated_at, started_at
                FROM sessions
                ORDER BY COALESCE(julianday(started_at), 0) ASC,
                         started_at ASC,
                         id ASC
                """
            ).fetchall()
            if len(rows) <= max_sessions:
                return 0
            target_remove = len(rows) - max_sessions
            now = datetime.now(timezone.utc)
            to_remove = []
            for row in rows:
                if row["id"] in protected:
                    continue
                if row["status"] == "active" and not _is_stale_active_session(row["updated_at"], now):
                    continue
                to_remove.append(row["id"])
                if len(to_remove) >= target_remove:
                    break
            if not to_remove:
                return 0
            placeholders = ",".join("?" * len(to_remove))
            conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", to_remove)
            conn.commit()
            return len(to_remove)

    def migrate_legacy_directory(self, output_dir: Path) -> int:
        """Import legacy JSONL/log files from a directory tree."""
        output_dir = output_dir.resolve()
        if not output_dir.is_dir():
            return 0

        imported = 0
        legacy_source_key = _legacy_source_key(output_dir)
        manifest_entries = _manifest_entries_by_rel_path(output_dir)
        for trace_path in sorted(output_dir.glob("**/trace_*.jsonl")):
            rel_path = trace_path.relative_to(output_dir).as_posix()
            if self._legacy_session_exists(legacy_source_key, rel_path):
                continue
            records = _read_jsonl_file(trace_path)
            log_path = trace_path.with_suffix(".log")
            logs = _read_log_file(log_path) if log_path.is_file() else []
            manifest_entry = manifest_entries.get(rel_path, {})
            session_id = self._import_legacy_session(
                legacy_source_key=legacy_source_key,
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
        legacy_source_key: str,
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

        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, started_at, updated_at, date_key, client, proxy_mode,
                        status, record_count, legacy_source_key, legacy_rel_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'complete', ?, ?, ?)
                    """,
                    (
                        session_id,
                        started_at,
                        started_at,
                        date_key,
                        client,
                        proxy_mode,
                        len(records),
                        legacy_source_key,
                        rel_path,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                row = conn.execute(
                    "SELECT 1 FROM sessions WHERE legacy_source_key = ? AND legacy_rel_path = ? LIMIT 1",
                    (legacy_source_key, rel_path),
                ).fetchone()
                if row is not None:
                    return None
                raise
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
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is not None:
                from claude_tap.dashboard import build_imported_session_summary

                summary = build_imported_session_summary(row, records, manifest_entry)
                conn.execute(
                    """
                    UPDATE sessions
                    SET summary_json = ?, status = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                        summary.get("status") or "complete",
                        session_id,
                    ),
                )
            conn.commit()
        return session_id

    def _legacy_session_exists(self, legacy_source_key: str, rel_path: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE legacy_source_key = ? AND legacy_rel_path = ? LIMIT 1",
            (legacy_source_key, rel_path),
        ).fetchone()
        return row is not None

    def _migration_done(self, marker: str) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM migration_state WHERE key = ?",
            (marker,),
        ).fetchone()
        return row is not None

    def _mark_migration_done(self, marker: str) -> None:
        with self._write_lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO migration_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (marker, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    def _refresh_summary_after_append(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        record: dict[str, Any],
        record_count: int,
    ) -> None:
        from claude_tap.dashboard import merge_record_into_summary

        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return
        existing = None
        if row["summary_json"]:
            try:
                existing = json.loads(row["summary_json"])
            except json.JSONDecodeError:
                existing = None
        summary = merge_record_into_summary(
            existing,
            row=row,
            record=record,
            record_count=record_count,
        )
        conn.execute(
            """
            UPDATE sessions
            SET summary_json = ?
            WHERE id = ?
            """,
            (
                json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                session_id,
            ),
        )

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = self._open_connection()
            self._ensure_schema_once(conn)
            self._tls.conn = conn
        return conn

    def close(self) -> None:
        """Close the thread-local SQLite connection."""
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            conn.close()
            self._tls.conn = None

    def _ensure_schema_once(self, conn: sqlite3.Connection) -> None:
        with self._schema_lock:
            if self._schema_ready:
                return
            self._ensure_schema(conn)
            self._schema_ready = True

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self._create_v3_schema(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return
        if current == 2:
            self._migrate_v2_to_v3(conn)
            return
        if current != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported trace database schema version {current}; expected {SCHEMA_VERSION}.")
        self._create_v3_schema(conn)

    def _create_v3_schema(self, conn: sqlite3.Connection) -> None:
        self._create_v3_tables(conn)
        self._create_v3_indexes(conn)

    def _migrate_v2_to_v3(self, conn: sqlite3.Connection) -> None:
        suffix = uuid.uuid4().hex
        sessions_v2 = f"sessions_v2_{suffix}"
        records_v2 = f"records_v2_{suffix}"
        proxy_logs_v2 = f"proxy_logs_v2_{suffix}"
        if conn.in_transaction:
            conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN")
            conn.execute(f"ALTER TABLE sessions RENAME TO {sessions_v2}")
            conn.execute(f"ALTER TABLE records RENAME TO {records_v2}")
            conn.execute(f"ALTER TABLE proxy_logs RENAME TO {proxy_logs_v2}")
            self._create_v3_tables(conn)
            conn.execute(
                f"""
                INSERT INTO sessions (
                    id, started_at, updated_at, date_key, client, proxy_mode,
                    status, record_count, summary_json, legacy_source_key, legacy_rel_path
                )
                SELECT
                    id, started_at, updated_at, date_key, client, proxy_mode,
                    status, record_count, summary_json, '', legacy_rel_path
                FROM {sessions_v2}
                """
            )
            conn.execute(
                f"""
                INSERT INTO records (session_id, record_index, turn, timestamp, payload_json)
                SELECT session_id, record_index, turn, timestamp, payload_json
                FROM {records_v2}
                """
            )
            conn.execute(
                f"""
                INSERT INTO proxy_logs (session_id, line_no, logged_at, level, message)
                SELECT session_id, line_no, logged_at, level, message
                FROM {proxy_logs_v2}
                """
            )
            conn.execute(f"DROP TABLE {proxy_logs_v2}")
            conn.execute(f"DROP TABLE {records_v2}")
            conn.execute(f"DROP TABLE {sessions_v2}")
            self._create_v3_indexes(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _create_v3_tables(self, conn: sqlite3.Connection) -> None:
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
                legacy_source_key TEXT NOT NULL DEFAULT '',
                legacy_rel_path TEXT
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

    def _create_v3_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_date_key ON sessions(date_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_session_id ON records(session_id)")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_legacy_source_rel_path
            ON sessions(legacy_source_key, legacy_rel_path)
            WHERE legacy_rel_path IS NOT NULL
            """
        )

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


def _legacy_source_key(output_dir: Path) -> str:
    return sha256(str(output_dir.resolve()).encode("utf-8")).hexdigest()[:16]


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


def _manifest_entries_by_rel_path(output_dir: Path) -> dict[str, dict[str, Any]]:
    manifest_path = output_dir / ".cloudtap-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("traces", []):
        if not isinstance(entry, dict):
            continue
        for file_name in entry.get("files", []):
            if isinstance(file_name, str):
                entries[file_name.replace("\\", "/")] = entry
    return entries


def _manifest_entry_for_rel_path(output_dir: Path, rel_path: str) -> dict[str, Any]:
    return _manifest_entries_by_rel_path(output_dir).get(rel_path, {})


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


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_stale_active_session(updated_at: object, now: datetime) -> bool:
    updated = _parse_iso_datetime(updated_at)
    return updated is not None and updated <= now - STALE_ACTIVE_SESSION_AFTER


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
