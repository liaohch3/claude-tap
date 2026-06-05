"""TraceWriter – async SQLite writer with statistics."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_tap.trace_store import TraceStore, get_trace_store
from claude_tap.usage import normalize_usage

if TYPE_CHECKING:
    from claude_tap.live import LiveViewerServer


class TraceWriter:
    """Writes trace records to the local SQLite store and accumulates statistics."""

    def __init__(
        self,
        session_id: str,
        live_server: "LiveViewerServer | None" = None,
        metadata: dict[str, str] | None = None,
        store: TraceStore | None = None,
    ):
        self.session_id = session_id
        self._lock = asyncio.Lock()
        self.count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_create_tokens = 0
        self.models_used: dict[str, int] = {}
        self._live_server = live_server
        self._metadata = metadata or {}
        self._store = store or get_trace_store()
        self._has_error = False
        self.storage_error_count = 0
        self.spooled_trace_records = 0
        self.spooled_trace_summaries = 0
        self.dropped_trace_records = 0
        self._storage_warning_emitted = False
        self._startup_storage_error: sqlite3.Error | None = None

    async def write(self, record: dict) -> None:
        """Write a record and update statistics."""
        async with self._lock:
            if self._metadata:
                capture = record.get("capture") if isinstance(record.get("capture"), dict) else {}
                record["capture"] = {**self._metadata, **capture}
            try:
                self._store.append_record(self.session_id, record)
            except sqlite3.Error as exc:
                fallback_path = self._spool_fallback("append_fallback_record", record, exc)
                if fallback_path is None:
                    self.dropped_trace_records += 1
                else:
                    self.spooled_trace_records += 1
                self._record_storage_error(exc, fallback_path)
            self.count += 1
            self._update_stats(record)

        if self._live_server:
            await self._live_server.broadcast(record)

    def close(self) -> None:
        """Finalize the active session in SQLite."""
        if self._startup_storage_error is not None:
            self.spooled_trace_summaries += 1
            fallback_path = self._spool_fallback(
                "append_fallback_summary",
                self.get_summary(),
                self._startup_storage_error,
            )
            if fallback_path is None:
                self.spooled_trace_summaries -= 1
            return

        summary = self.get_summary()
        try:
            self._store.finalize_session(self.session_id, summary)
        except sqlite3.Error as exc:
            self.storage_error_count += 1
            self.spooled_trace_summaries += 1
            fallback_path = self._spool_fallback("append_fallback_summary", self.get_summary(), exc)
            if fallback_path is None:
                self.spooled_trace_summaries -= 1
            if not self._storage_warning_emitted:
                self._emit_storage_warning(exc, fallback_path)

    def record_startup_storage_error(self, exc: sqlite3.Error) -> None:
        """Record that the initial SQLite session row could not be created."""
        self._startup_storage_error = exc
        self._record_storage_error(exc, None)

    def _update_stats(self, record: dict) -> None:
        req_body = record.get("request", {}).get("body", {})
        model = req_body.get("model", "unknown") if isinstance(req_body, dict) else "unknown"
        self.models_used[model] = self.models_used.get(model, 0) + 1

        resp_body = record.get("response", {}).get("body", {})
        usage = resp_body.get("usage", {}) if isinstance(resp_body, dict) else {}
        if not usage and isinstance(resp_body, dict):
            usage = resp_body
        usage = normalize_usage(usage)

        self.total_input_tokens += usage.get("input_tokens", 0)
        self.total_output_tokens += usage.get("output_tokens", 0)
        self.total_cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        self.total_cache_create_tokens += usage.get("cache_creation_input_tokens", 0)

        response = record.get("response")
        if isinstance(response, dict):
            status = response.get("status")
            if isinstance(status, int) and status >= 400:
                self._has_error = True
            if isinstance(response.get("error"), str) and response["error"]:
                self._has_error = True

    def _spool_fallback(self, method_name: str, payload: dict[str, Any], exc: sqlite3.Error) -> Path | None:
        method = getattr(self._store, method_name, None)
        if method is None:
            return None
        try:
            result = method(self.session_id, payload, exc)
        except (OSError, sqlite3.Error):
            return None
        return result if isinstance(result, Path) else None

    def _record_storage_error(self, exc: sqlite3.Error, fallback_path: Path | None) -> None:
        self.storage_error_count += 1
        if self._storage_warning_emitted:
            return
        self._emit_storage_warning(exc, fallback_path)

    def _emit_storage_warning(self, exc: sqlite3.Error, fallback_path: Path | None) -> None:
        self._storage_warning_emitted = True
        if fallback_path is None:
            sys.stderr.write(f"claude-tap: trace storage failed; continuing without blocking proxy ({exc})\n")
        else:
            sys.stderr.write(
                f"claude-tap: trace storage failed; spooled fallback data to {fallback_path} and continued ({exc})\n"
            )

    def get_summary(self) -> dict:
        """Return a summary of the trace statistics."""
        return {
            "api_calls": self.count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_create_tokens": self.total_cache_create_tokens,
            "models_used": self.models_used,
            "has_error": self._has_error,
            "trace_storage_errors": self.storage_error_count,
            "spooled_trace_records": self.spooled_trace_records,
            "spooled_trace_summaries": self.spooled_trace_summaries,
            "dropped_trace_records": self.dropped_trace_records,
        }
