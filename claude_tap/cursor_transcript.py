"""Cursor CLI transcript import for viewer-friendly trace records."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_tap.trace import TraceWriter


def _cursor_projects_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".cursor" / "projects"


def _extract_content_blocks(message: object) -> list[dict]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                blocks.append({"type": "text", "text": text})
        elif item.get("type") == "tool_use":
            name = item.get("name")
            if not isinstance(name, str) or not name:
                name = "Tool"
            tool_input = item.get("input")
            if not isinstance(tool_input, dict):
                tool_input = {}
            block = {"type": "tool_use", "name": name, "input": tool_input}
            tool_id = item.get("id")
            if isinstance(tool_id, str) and tool_id:
                block["id"] = tool_id
            blocks.append(block)
    return blocks


def _text_from_blocks(blocks: list[dict]) -> str:
    return "\n".join(
        block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()


def _strip_cursor_wrappers(text: str) -> str:
    """Remove Cursor's timestamp/query XML wrappers from user transcript text."""
    match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return re.sub(r"<timestamp>.*?</timestamp>\s*", "", text, flags=re.DOTALL).strip()


def _load_transcript(path: Path) -> list[tuple[str, list[dict]]]:
    messages: list[tuple[str, list[dict]]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return messages

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        role = record.get("role")
        if role not in {"user", "assistant"}:
            continue
        blocks = _extract_content_blocks(record.get("message"))
        if not blocks:
            continue
        if role == "user":
            text = _strip_cursor_wrappers(_text_from_blocks(blocks))
            blocks = [{"type": "text", "text": text}] if text else []
        messages.append((role, blocks))
    return messages


def _assistant_steps(messages: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict], int, int]]:
    steps: list[tuple[str, list[dict], int, int]] = []
    pending_user: str | None = None
    cursor_turn = 0
    cursor_step = 0

    for role, blocks in messages:
        if role == "user":
            cursor_turn += 1
            cursor_step = 0
            pending_user = _text_from_blocks(blocks)
        elif role == "assistant" and pending_user is not None:
            cursor_step += 1
            steps.append((pending_user, blocks or [{"type": "text", "text": ""}], cursor_turn, cursor_step))
    return steps


def _normalize_assistant_blocks(blocks: list[dict], *, turn_index: int) -> list[dict]:
    normalized: list[dict] = []
    for index, block in enumerate(blocks, start=1):
        copied = dict(block)
        if copied.get("type") == "tool_use" and not copied.get("id"):
            copied["id"] = f"cursor_tool_{turn_index}_{index}"
        normalized.append(copied)
    return normalized


def find_cursor_transcripts(
    *,
    since: float,
    home: Path | None = None,
) -> list[Path]:
    """Return Cursor agent transcripts modified at or after ``since``."""
    projects_dir = _cursor_projects_dir(home)
    if not projects_dir.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for path in projects_dir.glob("*/agent-transcripts/*/*.jsonl"):
        try:
            mtime = path.stat().st_mtime
            if mtime >= since:
                candidates.append((mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(candidates, key=lambda item: item[0])]


def build_cursor_transcript_records(
    transcript_path: Path,
    *,
    start_turn: int,
) -> list[dict]:
    """Build Anthropic-shaped synthetic records from a Cursor transcript."""
    session_id = transcript_path.stem
    messages = _load_transcript(transcript_path)
    steps = _assistant_steps(messages)
    records: list[dict] = []
    timestamp = datetime.now(timezone.utc).isoformat()

    for index, (user_text, assistant_blocks, cursor_turn, cursor_step) in enumerate(steps, start=1):
        turn = start_turn + index - 1
        req_id = f"cursor_transcript_{uuid.uuid4().hex[:12]}"
        response_content = _normalize_assistant_blocks(assistant_blocks, turn_index=index)
        records.append(
            {
                "timestamp": timestamp,
                "request_id": req_id,
                "turn": turn,
                "duration_ms": 0,
                "transport": "cursor-transcript",
                "request": {
                    "method": "CURSOR_TRANSCRIPT",
                    "path": f"/cursor/transcript/{session_id}/turn/{cursor_turn}/step/{cursor_step}",
                    "headers": {},
                    "body": {
                        "model": "cursor-auto",
                        "cursor_turn": cursor_turn,
                        "cursor_step": cursor_step,
                        "messages": [{"role": "user", "content": user_text}],
                    },
                },
                "response": {
                    "status": 200,
                    "headers": {},
                    "body": {
                        "id": session_id,
                        "type": "message",
                        "role": "assistant",
                        "content": response_content,
                    },
                },
            }
        )
    return records


async def import_cursor_transcripts(
    writer: TraceWriter,
    *,
    since: float,
    home: Path | None = None,
) -> int:
    """Append recent Cursor transcripts to the active trace.

    Cursor CLI persists readable user/assistant messages locally, while its
    network payloads are protobuf-oriented. Importing transcripts gives the
    HTML viewer a readable multi-turn conversation without reverse-engineering
    Cursor's private wire schema.
    """
    imported = 0
    start_turn = writer.count + 1
    for transcript_path in find_cursor_transcripts(since=since, home=home):
        records = build_cursor_transcript_records(transcript_path, start_turn=start_turn + imported)
        for record in records:
            await writer.write(record)
            imported += 1
    return imported
