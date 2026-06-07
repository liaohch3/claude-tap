"""Codex App local session import for viewer-friendly trace records."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_tap.trace import TraceWriter

CODEX_APP_TRANSPORT = "codex-app-transcript"


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
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

    session_id = transcript_path.stem
    model = "codex-app"
    instructions = ""
    tools: list[dict[str, Any]] = []
    cwd = ""
    cli_version = ""
    source = "codex-app"
    history_input: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []
    current_output: list[dict[str, Any]] = []
    current_started_at: str | None = None
    records: list[dict[str, Any]] = []

    def flush(timestamp: str | None, usage: dict[str, int] | None = None) -> None:
        nonlocal current_output, current_started_at, pending_tool_results
        if not current_output:
            history_input.extend(pending_tool_results)
            pending_tool_results = []
            return

        response_index = len(records) + 1
        response_id = _response_id(session_id, response_index)
        request_body: dict[str, Any] = {
            "type": "response.create",
            "model": model,
            "input": _json_clone(history_input),
        }
        if instructions:
            request_body["instructions"] = instructions
        if tools:
            request_body["tools"] = _json_clone(tools)
        metadata = {
            "codex_app_session_id": session_id,
            "codex_app_source": source,
        }
        if cwd:
            metadata["cwd"] = cwd
        request_body["metadata"] = metadata

        response_body: dict[str, Any] = {
            "id": response_id,
            "object": "response",
            "status": "completed",
            "model": model,
            "output": _json_clone(current_output),
        }
        if usage:
            response_body["usage"] = usage

        headers = {"x-codex-app-session-id": session_id}
        if cli_version:
            headers["x-codex-version"] = cli_version

        records.append(
            {
                "timestamp": current_started_at or timestamp or datetime.now(timezone.utc).isoformat(),
                "request_id": f"codex_app_{uuid.uuid4().hex[:12]}",
                "turn": start_turn + len(records),
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
        )
        history_input.extend(_json_clone(current_output))
        history_input.extend(pending_tool_results)
        current_output = []
        pending_tool_results = []
        current_started_at = None

    for row in rows:
        timestamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
        row_type = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue

        if row_type == "session_meta":
            raw_session_id = payload.get("id")
            if isinstance(raw_session_id, str) and raw_session_id:
                session_id = raw_session_id
            cli_version_value = payload.get("cli_version")
            if isinstance(cli_version_value, str):
                cli_version = cli_version_value
            source_value = payload.get("source") or payload.get("originator") or payload.get("thread_source")
            if isinstance(source_value, str) and source_value:
                source = source_value
            instructions = _base_instruction_text(payload.get("base_instructions"))
            tools = _normalize_tools(payload.get("dynamic_tools"))
            cwd_value = payload.get("cwd")
            if isinstance(cwd_value, str):
                cwd = cwd_value
            continue

        if row_type == "turn_context":
            flush(timestamp)
            model_value = payload.get("model")
            if isinstance(model_value, str) and model_value:
                model = model_value
            cwd_value = payload.get("cwd")
            if isinstance(cwd_value, str):
                cwd = cwd_value
            continue

        if row_type == "event_msg" and payload.get("type") == "token_count":
            flush(timestamp, _usage_from_token_event(payload))
            continue

        if row_type != "response_item":
            continue

        if _is_message_input(payload):
            flush(timestamp)
            history_input.append(_json_clone(payload))
            continue
        if _is_call_output(payload):
            pending_tool_results.append(_json_clone(payload))
            continue
        if _is_model_output(payload):
            if current_started_at is None:
                current_started_at = timestamp
            current_output.append(_json_clone(payload))

    if include_incomplete:
        flush(None)

    return records


async def import_codex_app_transcripts(
    writer: TraceWriter,
    *,
    since: float,
    home: Path | None = None,
    state: dict[Path, int] | None = None,
    include_incomplete: bool = True,
) -> int:
    """Append new Codex App transcript records to the active trace."""
    imported = 0
    state = state if state is not None else {}
    start_turn = writer.count + 1
    for transcript_path in find_codex_app_transcripts(since=since, home=home):
        records = build_codex_app_transcript_records(
            transcript_path,
            start_turn=start_turn,
            include_incomplete=include_incomplete,
        )
        seen = state.get(transcript_path, 0)
        if seen > len(records):
            seen = 0
        for record in records[seen:]:
            await writer.write(record)
            imported += 1
        state[transcript_path] = len(records)
        start_turn += max(0, len(records) - seen)
    return imported


async def watch_codex_app_transcripts(
    writer: TraceWriter,
    *,
    since: float,
    home: Path | None = None,
    poll_interval: float = 1.0,
) -> None:
    """Poll Codex App session files and append completed transcript records."""
    state: dict[Path, int] = {}
    try:
        while True:
            await import_codex_app_transcripts(
                writer,
                since=since,
                home=home,
                state=state,
                include_incomplete=False,
            )
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        await import_codex_app_transcripts(
            writer,
            since=since,
            home=home,
            state=state,
            include_incomplete=True,
        )
        raise
