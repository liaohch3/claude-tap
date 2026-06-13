"""Codex App local session import for viewer-friendly trace records."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_tap.trace import TraceWriter
from claude_tap.trace_store import TraceStore, get_trace_store

CODEX_APP_TRANSPORT = "codex-app-transcript"
CODEX_APP_TRANSCRIPT_DISCOVERY_INTERVAL = 5.0


@dataclass
class _TranscriptParser:
    session_id: str
    model: str = "codex-app"
    instructions: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    cwd: str = ""
    cli_version: str = ""
    source: str = "codex-app"
    history_input: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_results: list[dict[str, Any]] = field(default_factory=list)
    current_output: list[dict[str, Any]] = field(default_factory=list)
    current_started_at: str | None = None
    response_count: int = 0

    def feed(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        start_turn: int,
        include_incomplete: bool,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []

        def flush(
            timestamp: str | None,
            usage: dict[str, int] | None = None,
            *,
            status: str = "completed",
        ) -> None:
            if not self.current_output:
                self.history_input.extend(self.pending_tool_results)
                self.pending_tool_results = []
                return

            self.response_count += 1
            response_id = _response_id(self.session_id, self.response_count)
            request_body: dict[str, Any] = {
                "type": "response.create",
                "model": self.model,
                "input": _json_clone(self.history_input),
            }
            if self.instructions:
                request_body["instructions"] = self.instructions
            if self.tools:
                request_body["tools"] = _json_clone(self.tools)
            metadata = {
                "codex_app_session_id": self.session_id,
                "codex_app_source": self.source,
            }
            if self.cwd:
                metadata["cwd"] = self.cwd
            request_body["metadata"] = metadata

            response_body: dict[str, Any] = {
                "id": response_id,
                "object": "response",
                "status": status,
                "model": self.model,
                "output": _json_clone(self.current_output),
            }
            if usage:
                response_body["usage"] = usage

            headers = {"x-codex-app-session-id": self.session_id}
            if self.cli_version:
                headers["x-codex-version"] = self.cli_version

            record: dict[str, Any] = {
                "timestamp": self.current_started_at or timestamp or datetime.now(timezone.utc).isoformat(),
                "request_id": f"codex_app_{uuid.uuid4().hex[:12]}",
                "turn": start_turn + self.response_count - 1,
                "duration_ms": 0,
                "transport": CODEX_APP_TRANSPORT,
                "upstream_base_url": "codex-app://sessions",
                "request": {
                    "method": "CODEX_APP_TRANSCRIPT",
                    "path": "/v1/responses",
                    "headers": headers,
                    "body": request_body,
                },
                "response": {
                    "status": 200,
                    "headers": {},
                    "body": response_body,
                },
            }
            if status != "completed":
                record["capture"] = {"codex_app_partial": True}
            records.append(record)
            self.history_input.extend(_json_clone(self.current_output))
            self.history_input.extend(self.pending_tool_results)
            self.current_output = []
            self.pending_tool_results = []
            self.current_started_at = None

        for row in rows:
            timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
            row_type = row.get("type")
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue

            if row_type == "session_meta":
                raw_session_id = payload.get("id")
                if isinstance(raw_session_id, str) and raw_session_id:
                    self.session_id = raw_session_id
                cli_version_value = payload.get("cli_version")
                if isinstance(cli_version_value, str):
                    self.cli_version = cli_version_value
                source_value = payload.get("source") or payload.get("originator") or payload.get("thread_source")
                if isinstance(source_value, str) and source_value:
                    self.source = source_value
                self.instructions = _base_instruction_text(payload.get("base_instructions"))
                self.tools = _normalize_tools(payload.get("dynamic_tools"))
                cwd_value = payload.get("cwd")
                if isinstance(cwd_value, str):
                    self.cwd = cwd_value
                continue

            if row_type == "turn_context":
                flush(timestamp)
                model_value = payload.get("model")
                if isinstance(model_value, str) and model_value:
                    self.model = model_value
                cwd_value = payload.get("cwd")
                if isinstance(cwd_value, str):
                    self.cwd = cwd_value
                continue

            if row_type == "event_msg" and payload.get("type") == "token_count":
                flush(timestamp, _usage_from_token_event(payload))
                continue

            if row_type != "response_item":
                continue

            if _is_message_input(payload):
                flush(timestamp)
                self.history_input.append(_json_clone(payload))
                continue
            if _is_call_output(payload):
                self.pending_tool_results.append(_json_clone(payload))
                continue
            if _is_model_output(payload):
                if self.current_started_at is None:
                    self.current_started_at = timestamp
                self.current_output.append(_json_clone(payload))

        if include_incomplete:
            flush(None, status="in_progress")

        return records


@dataclass
class _TranscriptCursor:
    parser: _TranscriptParser
    offset: int = 0
    partial_line: str = ""


class CodexAppTranscriptSessionRegistry:
    """Own one trace writer per Codex App transcript/query."""

    def __init__(
        self,
        *,
        store: TraceStore | None = None,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._store = store or get_trace_store()
        self._metadata = metadata or {}
        self._writers: dict[Path, TraceWriter] = {}
        self._session_ids: dict[Path, str] = {}

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._session_ids.values())

    async def write_next_turn(self, transcript_path: Path, record: dict[str, Any]) -> None:
        writer = self._writer_for_record(transcript_path, record)
        await writer.write_next_turn(record)

    def close(self) -> None:
        for writer in self._writers.values():
            writer.close()

    def get_summary(self) -> dict[str, Any]:
        summary = {
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_create_tokens": 0,
            "models_used": {},
            "has_error": False,
        }
        models: dict[str, int] = {}
        for writer in self._writers.values():
            item = writer.get_summary()
            summary["api_calls"] += int(item["api_calls"])
            summary["input_tokens"] += int(item["input_tokens"])
            summary["output_tokens"] += int(item["output_tokens"])
            summary["cache_read_tokens"] += int(item["cache_read_tokens"])
            summary["cache_create_tokens"] += int(item["cache_create_tokens"])
            summary["has_error"] = bool(summary["has_error"] or item["has_error"])
            for model, count in item["models_used"].items():
                models[model] = models.get(model, 0) + count
        summary["models_used"] = models
        return summary

    def _writer_for_record(self, transcript_path: Path, record: dict[str, Any]) -> TraceWriter:
        writer = self._writers.get(transcript_path)
        if writer is not None:
            return writer

        metadata = dict(self._metadata)
        codex_app_session_id = _record_codex_app_session_id(record)
        if codex_app_session_id:
            metadata["codex_app_session_id"] = codex_app_session_id
        session_id = self._store.create_session(
            client=metadata.get("client", "codexapp"),
            proxy_mode=metadata.get("proxy_mode", "transcript"),
            started_at=_record_datetime(record),
        )
        writer = TraceWriter(session_id, metadata=metadata, store=self._store)
        self._writers[transcript_path] = writer
        self._session_ids[transcript_path] = session_id
        return writer


def codex_app_home(home: Path | None = None) -> Path:
    """Return the Codex App home directory."""
    if home is not None:
        return home / ".codex" if home.name != ".codex" else home
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_app_sessions_dir(home: Path | None = None) -> Path:
    return codex_app_home(home) / "sessions"


def find_codex_app_transcripts(*, since: float, home: Path | None = None) -> list[Path]:
    """Return Codex App session JSONL files modified at or after ``since``."""
    sessions_dir = codex_app_sessions_dir(home)
    if not sessions_dir.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.glob("**/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since:
            candidates.append((mtime, path))
    return [path for _, path in sorted(candidates, key=lambda item: item[0])]


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        row = _parse_jsonl_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _read_new_jsonl_rows(path: Path, cursor: _TranscriptCursor) -> list[dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size < cursor.offset:
        cursor.parser = _TranscriptParser(path.stem)
        cursor.offset = 0
        cursor.partial_line = ""

    try:
        with path.open("rb") as file_obj:
            file_obj.seek(cursor.offset)
            data = file_obj.read()
            cursor.offset = file_obj.tell()
    except OSError:
        return []

    if not data and not cursor.partial_line:
        return []

    text = data.decode("utf-8", errors="replace")
    combined = cursor.partial_line + text
    if not combined:
        return []

    lines = combined.splitlines()
    cursor.partial_line = ""
    if combined and not combined.endswith(("\n", "\r")) and lines:
        candidate = lines[-1]
        if _parse_jsonl_line(candidate) is None:
            cursor.partial_line = candidate
            lines = lines[:-1]

    rows: list[dict[str, Any]] = []
    for line in lines:
        row = _parse_jsonl_line(line)
        if row is not None:
            rows.append(row)
    return rows


def _base_instruction_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return ""


def _normalize_tools(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tools: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        namespace = item.get("namespace")
        tool_name = f"{namespace}.{name}" if isinstance(namespace, str) and namespace else name
        tool: dict[str, Any] = {"type": "function", "name": tool_name}
        description = item.get("description")
        if isinstance(description, str) and description:
            tool["description"] = description
        input_schema = item.get("inputSchema") or item.get("input_schema") or item.get("parameters")
        if isinstance(input_schema, dict):
            tool["parameters"] = input_schema
        tools.append(tool)
    return tools


def _usage_from_token_event(payload: dict[str, Any]) -> dict[str, int]:
    info = payload.get("info")
    if not isinstance(info, dict):
        return {}
    raw = info.get("last_token_usage")
    if not isinstance(raw, dict):
        return {}

    usage: dict[str, int] = {}
    field_map = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
        "cached_input_tokens": "cache_read_input_tokens",
    }
    for source_key, target_key in field_map.items():
        value = raw.get(source_key)
        if isinstance(value, int) and value >= 0:
            usage[target_key] = value
    reasoning = raw.get("reasoning_output_tokens")
    if isinstance(reasoning, int) and reasoning >= 0:
        usage["reasoning_output_tokens"] = reasoning
    return usage


def _is_message_input(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "message" and payload.get("role") in {"developer", "system", "user"}


def _is_message_output(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "message" and payload.get("role") == "assistant"


def _is_call_output(payload: dict[str, Any]) -> bool:
    item_type = payload.get("type")
    return isinstance(item_type, str) and (item_type == "tool_search_output" or item_type.endswith("_call_output"))


def _is_model_output(payload: dict[str, Any]) -> bool:
    item_type = payload.get("type")
    if _is_message_output(payload):
        return True
    return isinstance(item_type, str) and (
        item_type == "reasoning" or item_type == "tool_search_call" or item_type.endswith("_call")
    )


def _response_id(session_id: str, index: int) -> str:
    return f"resp_codexapp_{session_id.replace('-', '')[:20]}_{index}"


def _record_codex_app_session_id(record: dict[str, Any]) -> str:
    request = record.get("request")
    headers = request.get("headers") if isinstance(request, dict) else {}
    body = request.get("body") if isinstance(request, dict) else {}
    metadata = body.get("metadata") if isinstance(body, dict) else {}
    value = metadata.get("codex_app_session_id") if isinstance(metadata, dict) else None
    if not isinstance(value, str) or not value:
        value = headers.get("x-codex-app-session-id") if isinstance(headers, dict) else None
    return value if isinstance(value, str) else ""


def _record_datetime(record: dict[str, Any]) -> datetime | None:
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_codex_app_transcript_records(
    transcript_path: Path,
    *,
    start_turn: int,
    include_incomplete: bool = True,
) -> list[dict[str, Any]]:
    """Build synthetic OpenAI Responses records from a Codex App session JSONL."""
    rows = _read_jsonl(transcript_path)
    if not rows:
        return []
    parser = _TranscriptParser(transcript_path.stem)
    return parser.feed(rows, start_turn=start_turn, include_incomplete=include_incomplete)


async def import_codex_app_transcripts(
    writer: TraceWriter,
    *,
    since: float,
    home: Path | None = None,
    state: dict[Path, _TranscriptCursor] | None = None,
    include_incomplete: bool = True,
    transcript_paths: Iterable[Path] | None = None,
) -> int:
    """Append new Codex App transcript records to the active trace."""
    imported = 0
    state = state if state is not None else {}
    paths = (
        list(transcript_paths) if transcript_paths is not None else find_codex_app_transcripts(since=since, home=home)
    )
    for transcript_path in paths:
        cursor = state.get(transcript_path)
        if not isinstance(cursor, _TranscriptCursor):
            cursor = _TranscriptCursor(parser=_TranscriptParser(transcript_path.stem))
            state[transcript_path] = cursor
        records = cursor.parser.feed(
            _read_new_jsonl_rows(transcript_path, cursor),
            start_turn=1,
            include_incomplete=include_incomplete,
        )
        for record in records:
            await writer.write_next_turn(record)
            imported += 1
    return imported


async def import_codex_app_transcripts_to_sessions(
    registry: CodexAppTranscriptSessionRegistry,
    *,
    since: float,
    home: Path | None = None,
    state: dict[Path, _TranscriptCursor] | None = None,
    include_incomplete: bool = True,
    transcript_paths: Iterable[Path] | None = None,
) -> int:
    """Append new Codex App transcript records into one trace session per query."""
    imported = 0
    state = state if state is not None else {}
    paths = (
        list(transcript_paths) if transcript_paths is not None else find_codex_app_transcripts(since=since, home=home)
    )
    for transcript_path in paths:
        cursor = state.get(transcript_path)
        if not isinstance(cursor, _TranscriptCursor):
            cursor = _TranscriptCursor(parser=_TranscriptParser(transcript_path.stem))
            state[transcript_path] = cursor
        records = cursor.parser.feed(
            _read_new_jsonl_rows(transcript_path, cursor),
            start_turn=1,
            include_incomplete=include_incomplete,
        )
        for record in records:
            await registry.write_next_turn(transcript_path, record)
            imported += 1
    return imported


async def watch_codex_app_transcripts(
    writer: TraceWriter,
    *,
    since: float,
    home: Path | None = None,
    poll_interval: float = 1.0,
    discovery_interval: float = CODEX_APP_TRANSCRIPT_DISCOVERY_INTERVAL,
) -> None:
    """Poll Codex App session files and append live transcript records."""
    state: dict[Path, _TranscriptCursor] = {}
    transcript_paths: list[Path] = []
    last_discovery = 0.0
    try:
        while True:
            now = time.monotonic()
            if not transcript_paths or now - last_discovery >= discovery_interval:
                known = set(transcript_paths)
                for path in find_codex_app_transcripts(since=since, home=home):
                    if path not in known:
                        transcript_paths.append(path)
                        known.add(path)
                last_discovery = now
            await import_codex_app_transcripts(
                writer,
                since=since,
                home=home,
                state=state,
                include_incomplete=True,
                transcript_paths=transcript_paths,
            )
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        for path in find_codex_app_transcripts(since=since, home=home):
            if path not in transcript_paths:
                transcript_paths.append(path)
        await import_codex_app_transcripts(
            writer,
            since=since,
            home=home,
            state=state,
            include_incomplete=True,
            transcript_paths=transcript_paths,
        )
        raise


async def watch_codex_app_transcripts_to_sessions(
    registry: CodexAppTranscriptSessionRegistry,
    *,
    since: float,
    home: Path | None = None,
    poll_interval: float = 1.0,
    discovery_interval: float = CODEX_APP_TRANSCRIPT_DISCOVERY_INTERVAL,
) -> None:
    """Poll Codex App transcripts and keep each app query in its own trace session."""
    state: dict[Path, _TranscriptCursor] = {}
    transcript_paths: list[Path] = []
    last_discovery = 0.0
    try:
        while True:
            now = time.monotonic()
            if not transcript_paths or now - last_discovery >= discovery_interval:
                known = set(transcript_paths)
                for path in find_codex_app_transcripts(since=since, home=home):
                    if path not in known:
                        transcript_paths.append(path)
                        known.add(path)
                last_discovery = now
            await import_codex_app_transcripts_to_sessions(
                registry,
                since=since,
                home=home,
                state=state,
                include_incomplete=True,
                transcript_paths=transcript_paths,
            )
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        for path in find_codex_app_transcripts(since=since, home=home):
            if path not in transcript_paths:
                transcript_paths.append(path)
        await import_codex_app_transcripts_to_sessions(
            registry,
            since=since,
            home=home,
            state=state,
            include_incomplete=True,
            transcript_paths=transcript_paths,
        )
        raise
