"""Cursor agent transcript import for viewer-friendly trace records.

Cursor CLI and the Cursor IDE Agent persist readable user/assistant messages
under ``~/.cursor/projects/*/agent-transcripts/``. Network payloads are
Connect/protobuf and are not treated as the conversation source.

Local transcript JSONL rows only contain ``role`` + ``message`` blocks. Model is
recovered from Cursor IDE/CLI local state when available (see
``cursor_metadata``). Billed API token usage is not present locally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_tap.cursor_metadata import CursorConversationMeta, resolve_cursor_conversation_meta
from claude_tap.trace import TraceWriter, create_trace_writer
from claude_tap.trace_store import TraceStore, get_trace_store

log = logging.getLogger("claude-tap")

_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_SYNC_ERROR_LOG_EVERY = 10


def _cursor_projects_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".cursor" / "projects"


def _launch_model_hint(value: str) -> str:
    """Normalize a launch ``--model`` hint for transcript enrichment.

    Cursor's ``auto`` means "pick at runtime"; it is not a concrete model id and
    must not override richer local metadata such as ``grok-4.5``.
    """
    stripped = value.strip()
    if not stripped or stripped.lower() == "auto":
        return ""
    return stripped


def model_from_cursor_args(cmd_args: list[str] | tuple[str, ...] | None) -> str:
    """Return a concrete ``--model`` hint from cursor-agent argv, if present."""
    if not cmd_args:
        return ""
    args = list(cmd_args)
    for index, arg in enumerate(args):
        if arg == "--model" and index + 1 < len(args):
            return _launch_model_hint(str(args[index + 1]))
        if arg.startswith("--model="):
            return _launch_model_hint(arg.split("=", 1)[1])
    return ""


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
    except OSError as exc:
        log.debug("Failed to read Cursor transcript %s: %s", path, exc)
        return messages

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            log.debug("Skipping invalid JSON in %s: %s", path, exc)
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


def _transcript_request_path(session_id: str, cursor_turn: int, cursor_step: int) -> str:
    return f"/cursor/transcript/{session_id}/turn/{cursor_turn}/step/{cursor_step}"


def find_cursor_transcripts(
    *,
    since: float,
    home: Path | None = None,
) -> list[Path]:
    """Return Cursor agent transcripts modified at or after ``since``.

    Supports both layouts Cursor has used:

    - nested: ``projects/<slug>/agent-transcripts/<id>/<id>.jsonl``
    - flat: ``projects/<slug>/agent-transcripts/<id>.jsonl``
    """
    projects_dir = _cursor_projects_dir(home)
    if not projects_dir.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    seen: set[Path] = set()
    for pattern in (
        "*/agent-transcripts/*/*.jsonl",
        "*/agent-transcripts/*.jsonl",
    ):
        for path in projects_dir.glob(pattern):
            if path in seen:
                continue
            # Nested layout already covers ``<id>/<id>.jsonl``; skip subagent
            # dumps under ``.../subagents/*.jsonl`` (three levels below project).
            if "subagents" in path.parts:
                continue
            seen.add(path)
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
    skip_steps: int = 0,
    model: str = "",
    conversation_meta: CursorConversationMeta | None = None,
) -> tuple[int, list[dict]]:
    """Build Anthropic-shaped synthetic records from a Cursor transcript.

    Returns ``(total_assistant_steps, new_records)``. Turn numbers are assigned
    later by :meth:`TraceWriter.write_next_turn`.
    """
    session_id = transcript_path.stem
    steps = _assistant_steps(_load_transcript(transcript_path))
    total_steps = len(steps)
    if skip_steps > 0:
        steps = steps[skip_steps:]
    records: list[dict] = []
    timestamp = datetime.now(timezone.utc).isoformat()
    meta = conversation_meta or CursorConversationMeta()
    model_name = _launch_model_hint(model) or meta.model

    for index, (user_text, assistant_blocks, cursor_turn, cursor_step) in enumerate(steps, start=1):
        req_id = f"cursor_transcript_{uuid.uuid4().hex[:12]}"
        response_content = _normalize_assistant_blocks(assistant_blocks, turn_index=skip_steps + index)
        body: dict = {
            "cursor_turn": cursor_turn,
            "cursor_step": cursor_step,
            "messages": [{"role": "user", "content": user_text}],
        }
        if model_name:
            body["model"] = model_name
        if meta.context_tokens_used is not None:
            # Context-window estimate from Cursor IDE — not billed API usage.
            body["cursor_context_tokens_used"] = meta.context_tokens_used
        if meta.context_token_limit is not None:
            body["cursor_context_token_limit"] = meta.context_token_limit
        if meta.source:
            body["cursor_meta_source"] = meta.source
        response_body: dict = {
            "id": session_id,
            "type": "message",
            "role": "assistant",
            "content": response_content,
        }
        if model_name:
            response_body["model"] = model_name
        records.append(
            {
                "timestamp": timestamp,
                "request_id": req_id,
                "duration_ms": 0,
                "transport": "cursor-transcript",
                "request": {
                    "method": "CURSOR_TRANSCRIPT",
                    "path": _transcript_request_path(session_id, cursor_turn, cursor_step),
                    "headers": {},
                    "body": body,
                },
                "response": {
                    "status": 200,
                    "headers": {},
                    "body": response_body,
                },
            }
        )
    return total_steps, records


def _cursor_project_slug(transcript_path: Path) -> str:
    """Return the Cursor projects/<slug> directory name for a transcript path."""
    parts = transcript_path.parts
    try:
        idx = parts.index("agent-transcripts")
    except ValueError:
        return ""
    if idx == 0:
        return ""
    return parts[idx - 1]


class CursorTranscriptWatcher:
    """Poll local Cursor agent transcripts and append new steps to traces.

    Each Cursor transcript JSONL (one IDE/CLI conversation) maps to its own
    claude-tap session. Mixing conversations into one session is a bug.

    Incremental import assumes transcripts are append-only. If Cursor truncates
    or rewrites a JSONL file (size shrinks), the per-file skip cursor is reset
    and path-based dedupe still prevents duplicate turns for unchanged step ids.
    """

    def __init__(
        self,
        *,
        since: float,
        home: Path | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        model: str = "",
        store: TraceStore | None = None,
        client: str = "cursor",
        proxy_mode: str = "transcript",
        metadata: dict[str, str] | None = None,
    ) -> None:
        self._since = since
        self._home = home
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._model = model.strip()
        self._store = store or get_trace_store()
        self._client = client
        self._proxy_mode = proxy_mode
        self._metadata = dict(metadata or {})
        self._writers: dict[str, TraceWriter] = {}
        self._imported_steps: dict[str, int] = {}
        self._imported_paths: set[str] = set()
        self._seen_sizes: dict[str, int] = {}
        self._conversation_meta: dict[str, CursorConversationMeta] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._sync_errors = 0

    def _meta_for(self, transcript_path: Path) -> CursorConversationMeta:
        key = transcript_path.stem
        cached = self._conversation_meta.get(key)
        # Only a resolved model is cache-complete. Context-only results must be
        # retried so a later chat-store / ai-tracking model can still land.
        if cached is not None and cached.model:
            return cached
        meta = resolve_cursor_conversation_meta(key, home=self._home)
        if meta.model:
            self._conversation_meta[key] = meta
            log.debug(
                "Cursor meta for %s: model=%s source=%s context=%s/%s",
                key,
                meta.model or "-",
                meta.source or "-",
                meta.context_tokens_used,
                meta.context_token_limit,
            )
        return meta

    @property
    def session_ids(self) -> list[str]:
        """Tap session ids created for observed Cursor transcripts, in discovery order."""
        return [writer.session_id for writer in self._writers.values() if writer.session_id]

    def get_summary(self) -> dict:
        """Aggregate TraceWriter summaries across all Cursor conversations."""
        stats: dict = {
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_create_tokens": 0,
            "models_used": {},
            "has_error": False,
            "trace_storage_errors": 0,
            "dropped_trace_records": 0,
        }
        for writer in self._writers.values():
            part = writer.get_summary()
            stats["api_calls"] += int(part.get("api_calls") or 0)
            stats["input_tokens"] += int(part.get("input_tokens") or 0)
            stats["output_tokens"] += int(part.get("output_tokens") or 0)
            stats["cache_read_tokens"] += int(part.get("cache_read_tokens") or 0)
            stats["cache_create_tokens"] += int(part.get("cache_create_tokens") or 0)
            stats["trace_storage_errors"] += int(part.get("trace_storage_errors") or 0)
            stats["dropped_trace_records"] += int(part.get("dropped_trace_records") or 0)
            stats["has_error"] = bool(stats["has_error"] or part.get("has_error"))
            for model_name, count in (part.get("models_used") or {}).items():
                stats["models_used"][model_name] = stats["models_used"].get(model_name, 0) + int(count)
        return stats

    def close(self) -> None:
        """Finalize all tap sessions owned by this watcher."""
        for writer in self._writers.values():
            writer.close()

    def _writer_for(self, transcript_path: Path) -> TraceWriter:
        # Key by conversation UUID so flat + nested copies of the same chat
        # share one tap session even when path-dedupe lets newer steps through.
        cursor_session_id = transcript_path.stem
        writer = self._writers.get(cursor_session_id)
        if writer is not None:
            return writer
        project_slug = _cursor_project_slug(transcript_path)
        metadata = {
            **self._metadata,
            "client": self._client,
            "proxy_mode": self._proxy_mode,
            "cursor_transcript_id": cursor_session_id,
        }
        if project_slug:
            metadata["cursor_project"] = project_slug
        meta = self._meta_for(transcript_path)
        if meta.model:
            metadata["model"] = meta.model
        if meta.source:
            metadata["cursor_meta_source"] = meta.source
        writer = create_trace_writer(
            store=self._store,
            client=self._client,
            proxy_mode=self._proxy_mode,
            metadata=metadata,
        )
        self._writers[cursor_session_id] = writer
        log.info(
            "Opened tap session %s for Cursor transcript %s (%s)",
            writer.session_id,
            cursor_session_id,
            project_slug or "unknown-project",
        )
        return writer

    def _reset_file_state_if_rewritten(self, transcript_path: Path) -> None:
        key = str(transcript_path)
        try:
            size = transcript_path.stat().st_size
        except OSError:
            return
        previous = self._seen_sizes.get(key)
        self._seen_sizes[key] = size
        if previous is not None and size < previous:
            log.info(
                "Cursor transcript %s shrank (%s -> %s bytes); resetting incremental import cursor",
                transcript_path,
                previous,
                size,
            )
            self._imported_steps[key] = 0
            session_id = transcript_path.stem
            prefix = f"/cursor/transcript/{session_id}/"
            self._imported_paths = {path for path in self._imported_paths if not path.startswith(prefix)}

    async def sync_once(self) -> int:
        """Import any new transcript steps since the last sync."""
        imported = 0
        for transcript_path in find_cursor_transcripts(since=self._since, home=self._home):
            key = str(transcript_path)
            self._reset_file_state_if_rewritten(transcript_path)
            skip_steps = self._imported_steps.get(key, 0)
            total_steps, records = build_cursor_transcript_records(
                transcript_path,
                skip_steps=skip_steps,
                model=self._model,
                conversation_meta=self._meta_for(transcript_path),
            )
            if not records:
                # Keep skip count aligned even when the file has only unfinished user turns.
                # Do not open a tap session until there is at least one assistant step.
                self._imported_steps[key] = max(skip_steps, total_steps)
                continue
            new_records = []
            for record in records:
                path = str((record.get("request") or {}).get("path") or "")
                if path and path in self._imported_paths:
                    self._imported_steps[key] = self._imported_steps.get(key, skip_steps) + 1
                    continue
                new_records.append(record)
            if not new_records:
                # Same conversation UUID can appear as both flat and nested paths;
                # path-based dedupe must not open an empty tap session.
                continue
            writer = self._writers.get(transcript_path.stem) or self._writer_for(transcript_path)
            for record in new_records:
                path = str((record.get("request") or {}).get("path") or "")
                await writer.write_next_turn(record)
                if path:
                    self._imported_paths.add(path)
                self._imported_steps[key] = self._imported_steps.get(key, skip_steps) + 1
                imported += 1
        return imported

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="cursor-transcript-watcher")

    async def stop(self) -> int:
        """Stop polling, final-sync, and close tap sessions. Returns newly imported count."""
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        try:
            return await self.sync_once()
        finally:
            self.close()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sync_once()
            except Exception:
                self._sync_errors += 1
                # Keep watching through file races / transient IO, but do not stay silent.
                if self._sync_errors == 1 or self._sync_errors % _SYNC_ERROR_LOG_EVERY == 0:
                    log.exception(
                        "Cursor transcript sync failed (%s error(s) so far)",
                        self._sync_errors,
                    )
                else:
                    log.debug("Cursor transcript sync failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue


async def import_cursor_transcripts(
    *,
    since: float,
    home: Path | None = None,
    model: str = "",
    store: TraceStore | None = None,
    client: str = "cursor",
    proxy_mode: str = "transcript",
    metadata: dict[str, str] | None = None,
) -> CursorTranscriptWatcher:
    """Import recent Cursor transcripts into one tap session per JSONL file.

    Returns the watcher (already synced once) so callers can read ``session_ids``
    and must call ``close()`` when finished.
    """
    watcher = CursorTranscriptWatcher(
        since=since,
        home=home,
        model=model,
        store=store,
        client=client,
        proxy_mode=proxy_mode,
        metadata=metadata,
    )
    await watcher.sync_once()
    return watcher
