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
            # Emit on blank line if we have an explicit event: header (Anthropic /
            # OpenAI Responses) OR accumulated data: lines without a header
            # (OpenAI Chat Completions uses bare "data: {...}" frames).
            if self._current_event is not None or self._current_data_lines:
                raw_data = "\n".join(self._current_data_lines)
                # Skip OpenAI Chat Completions terminator "[DONE]" — it's a
                # protocol sentinel, not a payload, and would otherwise show up
                # as a noisy non-JSON event in the trace.
                if raw_data == "[DONE]" and self._current_event is None:
                    self._current_event = None
                    self._current_data_lines = []
                    return
                try:
                    data = json.loads(raw_data)
                except (json.JSONDecodeError, ValueError):
                    data = raw_data
                # Default event type for bare data: frames (OpenAI Chat
                # Completions). Snapshot reconstruction stays a no-op for
                # these — the events themselves are preserved in the trace.
                event_type = self._current_event or "message"
                self.add_event(event_type, data)
                self._current_event = None
                self._current_data_lines = []

    def add_event(self, event_type: str, data) -> None:
        """Append an already-parsed stream event and update the snapshot."""
        self.events.append({"event": event_type, "data": data})
        self._accumulate(event_type, data)

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
            elif event_type in ("response.created", "response.completed", "response.done"):
                response = data.get("response")
                if isinstance(response, dict):
                    self._snapshot = copy.deepcopy(response)
                elif event_type in ("response.completed", "response.done"):
                    self._snapshot = copy.deepcopy(data)
            elif event_type == "message" and "choices" in data:
                # OpenAI Chat Completions chunk — must run before the
                # `_snapshot is None` guard below, since the accumulator
                # initializes its own snapshot on the first chunk.
                self._accumulate_chat_completion_chunk(data)
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

    def _accumulate_chat_completion_chunk(self, data: dict) -> None:
        choices = data.get("choices") or []
        usage = data.get("usage")

        # Some providers send a final usage-only chunk: {"choices": [],
        # "usage": {...}}. Process the usage and bail out — there is no
        # delta body to apply.
        if not isinstance(choices, list) or not choices:
            if isinstance(usage, dict) and self._snapshot is not None:
                self._merge_chat_completion_usage(usage)
            return

        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        if self._snapshot is None:
            self._snapshot = {
                "id": data.get("id", ""),
                "object": "chat.completion",
                "model": data.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": delta.get("role") or "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
                # Anthropic-shape mirror so the viewer's renderResponseContent
                # picks it up without schema-specific code. content[0] is the
                # leading text block; tool_use blocks (mirrored from
                # msg.tool_calls) follow.
                "content": [{"type": "text", "text": ""}],
            }

        msg = self._snapshot["choices"][0]["message"]
        text_block = self._snapshot["content"][0]

        if isinstance(delta.get("role"), str) and delta["role"]:
            msg["role"] = delta["role"]
        if isinstance(delta.get("content"), str) and delta["content"]:
            msg["content"] = (msg.get("content") or "") + delta["content"]
            text_block["text"] = (text_block.get("text") or "") + delta["content"]

        # Tool calls arrive as indexed deltas: {"index": 0, "id":?, "type":?,
        # "function": {"name":?, "arguments":?}}. Each field accumulates by
        # concatenation — most providers stream `arguments` token by token.
        for tc_delta in delta.get("tool_calls") or []:
            if not isinstance(tc_delta, dict):
                continue
            idx = tc_delta.get("index", 0)
            tool_calls = msg.setdefault("tool_calls", [])
            while len(tool_calls) <= idx:
                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            existing = tool_calls[idx]
            if isinstance(tc_delta.get("id"), str):
                existing["id"] = tc_delta["id"]
            if isinstance(tc_delta.get("type"), str):
                existing["type"] = tc_delta["type"]
            fn_delta = tc_delta.get("function") or {}
            if isinstance(fn_delta, dict):
                fn = existing.setdefault("function", {"name": "", "arguments": ""})
                if isinstance(fn_delta.get("name"), str):
                    fn["name"] = (fn.get("name") or "") + fn_delta["name"]
                if isinstance(fn_delta.get("arguments"), str):
                    fn["arguments"] = (fn.get("arguments") or "") + fn_delta["arguments"]
            # Mirror this tool call into the Anthropic-shape `content` array
            # so the viewer (which only reads body.content) can render
            # tool-only responses, and so the sidebar's response_tool_names
            # extractor sees the call.
            self._mirror_tool_call_to_content(idx, existing)

        if finish_reason:
            self._snapshot["choices"][0]["finish_reason"] = finish_reason

        if isinstance(usage, dict):
            self._merge_chat_completion_usage(usage)

    def _mirror_tool_call_to_content(self, idx: int, tc: dict) -> None:
        """Sync one accumulated tool_call into the `content` array as a
        tool_use block. content[0] is the leading text block, so tool_use
        blocks live at content[idx + 1]."""
        content = self._snapshot["content"]
        target = idx + 1
        while len(content) <= target:
            content.append({"type": "tool_use", "id": "", "name": "", "input": {}})
        block = content[target]
        if tc.get("id"):
            block["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            block["name"] = fn["name"]
        args_str = fn.get("arguments", "")
        if args_str:
            try:
                block["input"] = json.loads(args_str)
            except (json.JSONDecodeError, ValueError):
                # Arguments are still streaming; leave the previously parsed
                # input (or {}) until a complete JSON arrives.
                pass

    def _merge_chat_completion_usage(self, usage: dict) -> None:
        """Merge an OpenAI-shape usage dict into the snapshot, exposing both
        prompt/completion and input/output token names for downstream code."""
        merged = dict(usage)
        if "prompt_tokens" in usage and "input_tokens" not in usage:
            merged["input_tokens"] = usage["prompt_tokens"]
        if "completion_tokens" in usage and "output_tokens" not in usage:
            merged["output_tokens"] = usage["completion_tokens"]
        self._snapshot["usage"] = merged

    def reconstruct(self) -> dict | None:
        if self._snapshot is None:
            return None
        return self._snapshot
