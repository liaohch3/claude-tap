"""Logging handler that persists proxy logs into SQLite."""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone

from claude_tap.trace_store import TraceStore, get_trace_store

_STORAGE_RETRY_SECONDS = 1.0


class SQLiteLogHandler(logging.Handler):
    """Write log records to the active trace session."""

    def __init__(self, session_id: str, store: TraceStore | None = None):
        super().__init__()
        self.session_id = session_id
        self._store = store or get_trace_store()
        self._retry_after = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if time.monotonic() < self._retry_after:
            return
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
        except sqlite3.Error:
            self._retry_after = time.monotonic() + _STORAGE_RETRY_SECONDS
            return
        except Exception:
            self.handleError(record)
