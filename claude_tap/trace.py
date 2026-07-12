"""TraceWriter – async SQLite writer with statistics."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

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
        self._has_auxiliary_status_probe_error = False
        self._has_primary_success = False
        self.storage_error_count = 0
        self.dropped_trace_records = 0
        self._startup_storage_error: sqlite3.Error | None = None
        self._storage_warning_emitted = False

    async def write(self, record: dict) -> None:
        """Write a record and update statistics."""
        async with self._lock:
            self._write_locked(record)

        if self._live_server:
            await self._live_server.broadcast(record)

    async def write_next_turn(self, record: dict) -> None:
        """Assign the next trace turn under the writer lock, then write the record."""
        async with self._lock:
            record["turn"] = self.count + 1
            self._write_locked(record)

        if self._live_server:
            await self._live_server.broadcast(record)

    def _write_locked(self, record: dict) -> None:
        if self._metadata:
            capture = record.get("capture") if isinstance(record.get("capture"), dict) else {}
            record["capture"] = {**self._metadata, **capture}
        try:
            self._store.append_record(self.session_id, record)
        except sqlite3.Error as exc:
            self.dropped_trace_records += 1
            self._record_storage_error(exc)
        self.count += 1
        self._update_stats(record)

    def close(self) -> None:
        """Finalize the active session in SQLite."""
        if self._startup_storage_error is not None:
            return
        summary = self.get_summary()
        try:
            self._store.finalize_session(self.session_id, summary)
        except sqlite3.Error as exc:
            self._record_storage_error(exc)

    def record_startup_storage_error(self, exc: sqlite3.Error) -> None:
        """Record that SQLite could not create the initial session row."""
        self._startup_storage_error = exc
        self._record_storage_error(exc)

    def _record_storage_error(self, exc: sqlite3.Error) -> None:
        self.storage_error_count += 1
        if self._storage_warning_emitted:
            return
        self._storage_warning_emitted = True
        sys.stderr.write(f"claude-tap: trace storage failed; continuing without blocking proxy ({exc})\n")

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
            if isinstance(status, int):
                is_probe = _is_auxiliary_status_probe(record)
                if status >= 400:
                    if is_probe:
                        self._has_auxiliary_status_probe_error = True
                    else:
                        self._has_error = True
                elif status >= 200 and not is_probe:
                    self._has_primary_success = True
            if isinstance(response.get("error"), str) and response["error"]:
                self._has_error = True

    def get_summary(self) -> dict:
        """Return a summary of the trace statistics."""
        has_error = self._has_error or (self._has_auxiliary_status_probe_error and not self._has_primary_success)
        return {
            "api_calls": self.count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_create_tokens": self.total_cache_create_tokens,
            "models_used": self.models_used,
            "has_error": has_error,
            "trace_storage_errors": self.storage_error_count,
            "dropped_trace_records": self.dropped_trace_records,
        }


def create_trace_writer(
    *,
    store: TraceStore,
    client: str,
    proxy_mode: str,
    metadata: dict[str, str],
    started_at: datetime | None = None,
) -> TraceWriter:
    """Create a writer without letting unavailable trace storage block the client."""
    try:
        session_id = store.create_session(client=client, proxy_mode=proxy_mode, started_at=started_at)
    except sqlite3.Error as exc:
        writer = TraceWriter(str(uuid.uuid4()), live_server=None, metadata=metadata, store=store)
        writer.record_startup_storage_error(exc)
        return writer
    return TraceWriter(session_id, live_server=None, metadata=metadata, store=store)


def _is_auxiliary_status_probe(record: dict) -> bool:
    request = record.get("request")
    path = request.get("path") if isinstance(request, dict) else ""
    if not isinstance(path, str):
        return False
    clean_path = path.lower().split("?", 1)[0].rstrip("/")
    if clean_path in {"/models", "/v1/models", "/v1alpha/models", "/v1beta/models"}:
        return True
    match = re.fullmatch(r"/(?:v1/)?models/([^/:]+)", clean_path)
    return match is not None
