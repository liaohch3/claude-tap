"""TraceWriter – async JSONL writer with statistics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_tap.live import LiveViewerServer


class TraceWriter:
    """Writes trace records to a JSONL file and accumulates statistics."""

    def __init__(self, path: Path, live_server: "LiveViewerServer | None" = None):
        self.path = path
        self._lock = asyncio.Lock()
        self.count = 0
        # Token statistics
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_create_tokens = 0
        self.models_used: dict[str, int] = {}
        self._live_server = live_server
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep file handle open for real-time append + flush
        self._file = open(path, "a", encoding="utf-8")

    async def write(self, record: dict) -> None:
        """Write a record and update statistics."""
        async with self._lock:
            self._file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._file.flush()
            self.count += 1
            self._update_stats(record)

        # Broadcast to live viewer if enabled
        if self._live_server:
            await self._live_server.broadcast(record)

    def close(self) -> None:
        """Flush and close the JSONL file."""
        if self._file and not self._file.closed:
            self._file.flush()
            self._file.close()

    def _update_stats(self, record: dict) -> None:
        """Extract token usage from record and update totals."""
        req_body = record.get("request", {}).get("body", {})
        model = req_body.get("model", "unknown")
        self.models_used[model] = self.models_used.get(model, 0) + 1

        resp_body = record.get("response", {}).get("body", {})
        usage = resp_body.get("usage", {})
        if not usage and isinstance(resp_body, dict):
            usage = resp_body

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_cache_create_tokens += cache_create

    def get_summary(self) -> dict:
        """Return a summary of the trace statistics."""
        return {
            "api_calls": self.count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "cache_create_tokens": self.total_cache_create_tokens,
            "models_used": self.models_used,
        }
