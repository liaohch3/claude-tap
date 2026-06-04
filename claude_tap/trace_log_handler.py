"""Logging handler that persists proxy logs into SQLite."""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timezone

from claude_tap.trace_store import TraceStore, get_trace_store


class SQLiteLogHandler(logging.Handler):
    """Write log records to the active trace session."""

    def __init__(self, session_id: str, store: TraceStore | None = None):
        super().__init__()
        self.session_id = session_id
        self._store = store or get_trace_store()
        self._storage_warning_emitted = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            formatter = self.formatter or logging.Formatter()
            if record.exc_info:
                message = f"{message}\n{formatter.formatException(record.exc_info)}"
            if record.stack_info:
                message = f"{message}\n{formatter.formatStack(record.stack_info)}"
            self._store.append_log(
                self.session_id,
                message,
                level=record.levelname,
                logged_at=datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S"),
            )
        except sqlite3.Error as exc:
            self._spool_fallback_log(record, message, exc)
        except Exception:
            self.handleError(record)

    def _spool_fallback_log(self, record: logging.LogRecord, message: str, exc: sqlite3.Error) -> None:
        logged_at = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        fallback_path = None
        method = getattr(self._store, "append_fallback_log", None)
        if method is not None:
            try:
                fallback_path = method(
                    self.session_id,
                    message,
                    level=record.levelname,
                    logged_at=logged_at,
                    error=exc,
                )
            except OSError:
                fallback_path = None
        if self._storage_warning_emitted:
            return
        self._storage_warning_emitted = True
        if fallback_path is None:
            sys.stderr.write(f"claude-tap: proxy log storage failed ({exc})\n")
        else:
            sys.stderr.write(f"claude-tap: proxy log storage failed; spooled to {fallback_path} ({exc})\n")
