"""SSEReassembler – parse SSE bytes and reconstruct the full API response."""

from __future__ import annotations

import copy
import json

from claude_tap.usage import normalize_usage


class SSEReassembler:
    """Parse raw SSE bytes and reconstruct the full API response object
    by accumulating streaming events into a complete message snapshot."""

    def __init__(self, *, store_events: bool = True):
        self._store_events = store_events
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
        if self._store_events:
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
            gemini_chunk = self._gemini_chunk_payload(data) if event_type == "message" else None

            if event_type == "message_start":
                self._snapshot = copy.deepcopy(data.get("message", {}))
            elif event_type in ("response.created", "response.completed", "response.done"):
                response = data.get("response")
                if isinstance(response, dict):
                    self._snapshot = copy.deepcopy(response)
                elif event_type in ("response.completed", "response.done"):
                    self._snapshot = copy.deepcopy(data)
            elif gemini_chunk is not None:
                # Gemini streamGenerateContent uses bare `data: {...}` frames
                # with no `event:` header. Accumulate them before the generic
                # `_snapshot is None` guard so forward-proxy captures retain
                # assistant text and usage even when raw stream events are not
                # stored.
                self._accumulate_gemini_chunk(gemini_chunk)
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
                block = self._content_block_for_delta(idx, delta)
                if block is not None:
                    if delta.get("type") == "text_delta":
                        block["text"] = block.get("text", "") + delta.get("text", "")
                    elif delta.get("type") == "thinking_delta":
                        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                        if delta.get("signature"):
                            block["signature"] = delta["signature"]
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
                    self._snapshot["usage"].update(normalize_usage(usage))
        except Exception:
            pass

    def _content_block_for_delta(self, idx: int, delta: dict) -> dict | None:
        if not isinstance(idx, int) or idx < 0:
            idx = 0
        if "content" not in self._snapshot:
            self._snapshot["content"] = []
        content = self._snapshot["content"]
        if not isinstance(content, list):
            content = []
            self._snapshot["content"] = content
        while len(content) <= idx:
            content.append(self._empty_content_block_for_delta(delta))
        block = content[idx]
        if not isinstance(block, dict):
            block = self._empty_content_block_for_delta(delta)
            content[idx] = block
        if not block:
            block.update(self._empty_content_block_for_delta(delta))
        return block

    def _empty_content_block_for_delta(self, delta: dict) -> dict:
        if delta.get("type") == "thinking_delta":
            return {"type": "thinking", "thinking": ""}
        if delta.get("type") == "input_json_delta":
            return {"type": "tool_use", "id": "", "name": "", "input": {}}
        return {"type": "text", "text": ""}

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
        choice_usage = choice.get("usage")

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
                # picks it up without schema-specific code. content normally
                # contains the leading text block; thinking and tool_use blocks
                # are mirrored in as needed.
                "content": [{"type": "text", "text": ""}],
            }

        msg = self._snapshot["choices"][0]["message"]
        text_block = self._chat_completion_text_block()

        if isinstance(delta.get("role"), str) and delta["role"]:
            msg["role"] = delta["role"]
        if isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]:
            msg["reasoning_content"] = (msg.get("reasoning_content") or "") + delta["reasoning_content"]
            self._mirror_reasoning_to_content(msg["reasoning_content"])
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
        if isinstance(choice_usage, dict):
            self._merge_chat_completion_usage(choice_usage)

    def _gemini_chunk_payload(self, data: dict) -> dict | None:
        if self._is_gemini_chunk(data):
            return data
        response = data.get("response")
        if isinstance(response, dict) and self._is_gemini_chunk(response):
            return response
        return None

    def _is_gemini_chunk(self, data: dict) -> bool:
        return "candidates" in data or "usageMetadata" in data

    def _accumulate_gemini_chunk(self, data: dict) -> None:
        if self._snapshot is None or not isinstance(self._snapshot.get("candidates"), list):
            self._snapshot = {"candidates": []}

        for key, value in data.items():
            if key in {"candidates", "usageMetadata"}:
                continue
            self._snapshot[key] = copy.deepcopy(value)

        candidates = data.get("candidates")
        if isinstance(candidates, list):
            for position, candidate in enumerate(candidates):
                if isinstance(candidate, dict):
                    self._merge_gemini_candidate(position, candidate)

        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            self._snapshot["usageMetadata"] = copy.deepcopy(usage)
            self._snapshot["usage"] = normalize_usage(usage)

        self._snapshot["content"] = self._gemini_content_blocks()

    def _merge_gemini_candidate(self, position: int, candidate: dict) -> None:
        idx = candidate.get("index")
        if not isinstance(idx, int) or idx < 0:
            idx = position

        candidates = self._snapshot["candidates"]
        while len(candidates) <= idx:
            candidates.append({})

        target = candidates[idx]
        if not isinstance(target, dict):
            target = {}
            candidates[idx] = target

        for key, value in candidate.items():
            if key == "content" and isinstance(value, dict):
                self._merge_gemini_candidate_content(target, value)
            else:
                target[key] = copy.deepcopy(value)

    def _merge_gemini_candidate_content(self, candidate: dict, incoming: dict) -> None:
        content = candidate.get("content")
        if not isinstance(content, dict):
            content = {}
            candidate["content"] = content

        for key, value in incoming.items():
            if key == "parts":
                continue
            content[key] = copy.deepcopy(value)

        parts = content.get("parts")
        if not isinstance(parts, list):
            parts = []
            content["parts"] = parts

        for part in incoming.get("parts") or []:
            if isinstance(part, dict):
                self._append_gemini_part(parts, part)

    def _append_gemini_part(self, parts: list, part: dict) -> None:
        if self._is_mergeable_gemini_text_part(part):
            previous = parts[-1] if parts else None
            if (
                isinstance(previous, dict)
                and isinstance(previous.get("text"), str)
                and self._is_mergeable_gemini_text_part(previous)
                and previous.get("thought") == part.get("thought")
            ):
                previous["text"] += part["text"]
                return
        parts.append(copy.deepcopy(part))

    def _is_mergeable_gemini_text_part(self, part: dict) -> bool:
        if not isinstance(part.get("text"), str):
            return False
        return set(part).issubset({"text", "thought"})

    def _gemini_content_blocks(self) -> list[dict]:
        content: list[dict] = []
        for candidate in self._snapshot.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            candidate_content = candidate.get("content")
            if not isinstance(candidate_content, dict):
                continue
            for part in candidate_content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str) and part["text"].strip():
                    if part.get("thought") is True:
                        self._append_mergeable_content_block(content, {"type": "thinking", "thinking": part["text"]})
                    else:
                        self._append_mergeable_content_block(content, {"type": "text", "text": part["text"]})
                call = part.get("functionCall")
                if isinstance(call, dict):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": call.get("name", "tool_use"),
                            "input": call.get("args") if isinstance(call.get("args"), dict) else {},
                        }
                    )
        return content

    def _append_mergeable_content_block(self, content: list[dict], block: dict) -> None:
        previous = content[-1] if content else None
        if isinstance(previous, dict) and previous.get("type") == block.get("type"):
            if block.get("type") == "thinking":
                previous["thinking"] = f"{previous.get('thinking', '')}{block.get('thinking', '')}"
                return
            if block.get("type") == "text":
                previous["text"] = f"{previous.get('text', '')}{block.get('text', '')}"
                return
        content.append(block)

    def _mirror_tool_call_to_content(self, idx: int, tc: dict) -> None:
        """Sync one accumulated tool_call into the `content` array as a
        tool_use block. content always has a text block, and may also have a
        leading thinking block, so tool_use blocks live after those mirrors."""
        content = self._snapshot["content"]
        offset = 1 + (1 if self._chat_completion_thinking_block(create=False) is not None else 0)
        target = idx + offset
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

    def _chat_completion_text_block(self) -> dict:
        content = self._snapshot["content"]
        for block in content:
            if block.get("type") == "text":
                return block
        block = {"type": "text", "text": ""}
        content.append(block)
        return block

    def _chat_completion_thinking_block(self, *, create: bool) -> dict | None:
        content = self._snapshot["content"]
        for block in content:
            if block.get("type") == "thinking":
                return block
        if not create:
            return None
        block = {"type": "thinking", "thinking": ""}
        content.insert(0, block)
        return block

    def _mirror_reasoning_to_content(self, reasoning: str) -> None:
        block = self._chat_completion_thinking_block(create=True)
        if block is not None:
            block["thinking"] = reasoning

    def _merge_chat_completion_usage(self, usage: dict) -> None:
        """Merge an OpenAI-shape usage dict into the snapshot, exposing both
        prompt/completion and input/output token names for downstream code."""
        self._snapshot["usage"] = normalize_usage(usage)

    def reconstruct(self) -> dict | None:
        if self._snapshot is None:
            return None
        return self._snapshot
