"""SSEReassembler – parse SSE bytes and reconstruct the full API response."""

from __future__ import annotations

import copy
import json


class SSEReassembler:
    """Parse raw SSE bytes and reconstruct the full API response object
    by accumulating streaming events into a complete message snapshot."""

    def __init__(self):
        self.events: list[dict] = []
        self._buf = b""
        self._current_event: str | None = None
        self._current_data_lines: list[str] = []
        self._snapshot: dict | None = None

    def feed_bytes(self, chunk: bytes):
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._feed_line(line.decode("utf-8", errors="replace"))

    def _feed_line(self, line: str):
        line = line.rstrip("\r")
        if line.startswith("event:"):
            self._current_event = line[len("event:") :].strip()
            self._current_data_lines = []
        elif line.startswith("data:"):
            self._current_data_lines.append(line[len("data:") :].strip())
        elif line == "":
            if self._current_event is not None:
                raw_data = "\n".join(self._current_data_lines)
                try:
                    data = json.loads(raw_data)
                except (json.JSONDecodeError, ValueError):
                    data = raw_data
                event_record = {"event": self._current_event, "data": data}
                self.events.append(event_record)
                self._accumulate(self._current_event, data)
                self._current_event = None
                self._current_data_lines = []

    def _accumulate(self, event_type: str, data) -> None:
        """Accumulate an SSE event into the message snapshot.

        This replaces the anthropic SDK's accumulate_event() with a simple
        manual implementation that handles the Anthropic streaming protocol.
        """
        if not isinstance(data, dict):
            return
        try:
            if event_type == "message_start":
                self._snapshot = copy.deepcopy(data.get("message", {}))
            elif self._snapshot is None:
                return
            elif event_type == "content_block_start":
                block = copy.deepcopy(data.get("content_block", {}))
                if "content" not in self._snapshot:
                    self._snapshot["content"] = []
                idx = data.get("index", len(self._snapshot["content"]))
                # Extend content list if needed
                while len(self._snapshot["content"]) <= idx:
                    self._snapshot["content"].append({})
                self._snapshot["content"][idx] = block
            elif event_type == "content_block_delta":
                idx = data.get("index", 0)
                delta = data.get("delta", {})
                if idx < len(self._snapshot.get("content", [])):
                    block = self._snapshot["content"][idx]
                    if delta.get("type") == "text_delta":
                        block["text"] = block.get("text", "") + delta.get("text", "")
                    elif delta.get("type") == "thinking_delta":
                        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                    elif delta.get("type") == "input_json_delta":
                        block["_partial_json"] = block.get("_partial_json", "") + delta.get("partial_json", "")
            elif event_type == "content_block_stop":
                idx = data.get("index", 0)
                if idx < len(self._snapshot.get("content", [])):
                    block = self._snapshot["content"][idx]
                    if "_partial_json" in block:
                        try:
                            block["input"] = json.loads(block["_partial_json"])
                        except (json.JSONDecodeError, ValueError):
                            pass
                        del block["_partial_json"]
            elif event_type == "message_delta":
                delta = data.get("delta", {})
                for k, v in delta.items():
                    self._snapshot[k] = v
                usage = data.get("usage", {})
                if usage:
                    if "usage" not in self._snapshot:
                        self._snapshot["usage"] = {}
                    self._snapshot["usage"].update(usage)
        except Exception:
            pass

    def reconstruct(self) -> dict | None:
        if self._snapshot is None:
            return None
        return self._snapshot
