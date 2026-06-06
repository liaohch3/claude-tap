"""Reconstruct a resumable Claude Code session from captured trace traffic.

claude-tap records the wire traffic exchanged with the Anthropic Messages API.
The largest request body in a session already carries the entire conversation
as ``messages[]`` (every user turn, assistant turn with ``tool_use`` blocks, and
``tool_result`` reply). Claude Code's own resume log
(``~/.claude/projects/<slug>/<session-uuid>.jsonl``) stores the same
conversation in a different shape: one JSONL event per content block, threaded
through ``uuid``/``parentUuid`` into a tree where each ``tool_result`` points at
the ``tool_use`` it answers.

This module bridges the two so a session captured on machine A can be re-homed
and resumed on machine B with ``claude --resume <id>``. Only the conversation is
transplanted; Claude Code rebuilds the system prompt and tool definitions itself
at launch from its own version, which is the desired behaviour.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_VERSION = "2.1.167"
_USER = "user"
_ASSISTANT = "assistant"


@dataclass
class TransplantEnv:
    """Machine-local context stamped onto reconstructed session events."""

    cwd: str
    session_id: str
    version: str = DEFAULT_VERSION
    git_branch: str = ""
    user_type: str = "external"
    entrypoint: str = "cli"
    new_uuid: Callable[[], str] = field(default=lambda: str(uuid.uuid4()))
    timestamp: str = "1970-01-01T00:00:00.000Z"


def detect_claude_version(default: str = DEFAULT_VERSION) -> str:
    """Best-effort read of the installed Claude Code version (``X.Y.Z``)."""

    import shutil
    import subprocess  # noqa: S404 - reading a local version string

    binary = shutil.which("claude")
    if not binary:
        return default
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return default
    match = re.search(r"\d+\.\d+\.\d+", out.stdout or "")
    return match.group(0) if match else default


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _request_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("request")).get("body"))


def _response_body(record: dict) -> dict:
    return _as_dict(_as_dict(record.get("response")).get("body"))


def _is_anthropic(record: dict) -> bool:
    path = str(_as_dict(record.get("request")).get("path") or "").split("?", 1)[0]
    body = _request_body(record)
    # count_tokens shares the /v1/messages prefix but never produces an
    # assistant turn, so it must not win the "fullest request" selection.
    if "count_tokens" in path:
        return False
    if path.startswith(("/v1/messages", "/model/")):
        return True
    return "messages" in body and ("system" in body or "anthropic_version" in body)


def _strip_unsigned_thinking(content: list) -> list:
    """Drop thinking blocks that lack a signature.

    Anthropic streams the signature as a separate ``signature_delta`` and
    rejects later turns that resend a thinking block with a missing or modified
    signature. Thinking blocks inside a captured request body were already
    accepted (signed); only a freshly reconstructed response tail is at risk, so
    we drop any unsigned thinking rather than ship a log the API will reject.
    """

    kept: list = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking" and not block.get("signature"):
            continue
        kept.append(block)
    return kept


def extract_conversation(records: Iterable[dict]) -> list[dict]:
    """Pick the fullest request and return the complete linear conversation.

    The request carrying the most messages is the most complete history; its
    own response is the latest assistant turn that no later request echoes back,
    so it is appended to close the tail.
    """

    best: dict | None = None
    best_key: tuple[int, int] = (-1, -1)
    for record in records:
        if not isinstance(record, dict) or not _is_anthropic(record):
            continue
        messages = _request_body(record).get("messages")
        if not isinstance(messages, list) or not messages:
            continue
        turn = record.get("turn") if isinstance(record.get("turn"), int) else 0
        key = (len(messages), turn)
        if key > best_key:
            best_key = key
            best = record

    if best is None:
        raise ValueError("no Anthropic conversation found in trace")

    conversation = [dict(msg) for msg in _request_body(best).get("messages", []) if isinstance(msg, dict)]
    response_content = _response_body(best).get("content")
    if isinstance(response_content, list) and response_content:
        tail = _strip_unsigned_thinking(response_content)
        if tail:
            conversation.append({"role": _ASSISTANT, "content": tail})
    return conversation


def _envelope(env: TransplantEnv, uuid_str: str, parent: str | None, *, source_uuid: str | None = None) -> dict:
    event: dict[str, Any] = {
        "parentUuid": parent,
        "isSidechain": False,
        "uuid": uuid_str,
        "timestamp": env.timestamp,
        "userType": env.user_type,
        "entrypoint": env.entrypoint,
        "cwd": env.cwd,
        "sessionId": env.session_id,
        "version": env.version,
        "gitBranch": env.git_branch,
    }
    if source_uuid is not None:
        event["sourceToolAssistantUUID"] = source_uuid
    return event


def _content_blocks(content: object) -> list[Any]:
    if isinstance(content, list):
        return content
    if content is None:
        return []
    return [content]


def conversation_to_events(messages: list[dict], env: TransplantEnv) -> list[dict]:
    """Explode API messages into Claude Code resume events.

    Mirrors Claude Code's native layout: one event per content block, assistant
    blocks sharing a single ``message.id``, and every ``tool_result`` threaded as
    a child of the ``tool_use`` event that produced its ``tool_use_id``.
    """

    events: list[dict] = []
    parent: str | None = None
    tool_use_uuid: dict[str, str] = {}

    for message in messages:
        role = message.get("role")
        blocks = _content_blocks(message.get("content"))

        if role == _ASSISTANT:
            message_id = str(message.get("id") or f"msg_{env.new_uuid()}")
            model = message.get("model") or "claude-opus-4-8"
            for block in blocks:
                uuid_str = env.new_uuid()
                event = _envelope(env, uuid_str, parent)
                event["type"] = _ASSISTANT
                event["requestId"] = message_id
                event["message"] = {
                    "id": message_id,
                    "type": "message",
                    "role": _ASSISTANT,
                    "model": model,
                    "content": [block],
                    "stop_reason": None,
                    "stop_sequence": None,
                }
                events.append(event)
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    tool_use_uuid[str(block["id"])] = uuid_str
                parent = uuid_str
            continue

        # user (real prompt) or tool results returned to the model
        if isinstance(message.get("content"), str):
            uuid_str = env.new_uuid()
            event = _envelope(env, uuid_str, parent)
            event["type"] = _USER
            event["message"] = {"role": _USER, "content": message["content"]}
            events.append(event)
            parent = uuid_str
            continue

        plain: list[Any] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                source = tool_use_uuid.get(str(block.get("tool_use_id") or ""))
                uuid_str = env.new_uuid()
                event = _envelope(env, uuid_str, source if source is not None else parent, source_uuid=source)
                event["type"] = _USER
                event["message"] = {"role": _USER, "content": [block]}
                event["toolUseResult"] = block.get("content")
                events.append(event)
                parent = uuid_str
            else:
                plain.append(block)
        if plain:
            uuid_str = env.new_uuid()
            event = _envelope(env, uuid_str, parent)
            event["type"] = _USER
            event["message"] = {"role": _USER, "content": plain}
            events.append(event)
            parent = uuid_str

    return events


def build_session_jsonl(messages: list[dict], env: TransplantEnv, *, last_prompt: str = "") -> str:
    """Render a complete, resumable session JSONL document."""

    events = conversation_to_events(messages, env)
    leaf_uuid = events[-1]["uuid"] if events else None

    lines: list[dict] = [
        {"type": "mode", "mode": "normal", "sessionId": env.session_id},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": env.session_id},
    ]
    lines.extend(events)
    if leaf_uuid is not None:
        lines.append(
            {
                "type": "last-prompt",
                "lastPrompt": last_prompt or _derive_last_prompt(messages),
                "leafUuid": leaf_uuid,
                "sessionId": env.session_id,
            }
        )
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)


def _derive_last_prompt(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") != _USER:
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content[:200]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))[:200]
    return ""


def project_slug(cwd: str) -> str:
    """Reproduce Claude Code's project directory slug for a working directory.

    Every character outside ``[A-Za-z0-9]`` is collapsed to ``-`` (drive letters,
    separators, and dots included), matching dirs such as ``G--project-claude-tap``.
    """

    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def claude_home(home: Path | None = None) -> Path:
    """Resolve the ``.claude`` store directory.

    An explicit ``home`` is treated as the directory *containing* ``.claude``.
    Otherwise honor ``CLAUDE_CONFIG_DIR`` (which Claude Code itself reads as the
    config dir) before falling back to ``~/.claude``.
    """

    if home is not None:
        return home / ".claude"
    import os

    configured = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_session_id(session_id: str) -> str:
    """Reject session ids that could escape the project directory.

    The id becomes a filename (``<id>.jsonl``); separators or ``..`` would let
    an explicit ``--session-id`` write outside the Claude project store.
    """

    if not session_id or not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session id (allowed: letters, digits, '-', '_'): {session_id!r}")
    return session_id


@dataclass
class InstalledSession:
    session_id: str
    path: Path
    project_dir: Path
    resume_command: str
    message_count: int
    target: str = "claude"


@dataclass(frozen=True)
class ResumeTarget:
    """A pluggable destination CLI for a transplanted conversation.

    Extension point: other agent CLIs (Codex, Gemini, ...) store resumable
    sessions in their own layout. Register one here to support a new
    ``--target`` without touching the export/import plumbing. Each target owns
    how a conversation is serialized, where it lives, and how the user resumes
    it; the conversation extraction stays provider-aware in
    ``extract_conversation``.
    """

    name: str
    label: str
    build: Callable[[list[dict], TransplantEnv, str], str]
    project_dir: Callable[[Path | None, str], Path]
    filename: Callable[[str], str]
    resume_command: Callable[[str], str]


def _claude_project_dir(home: Path | None, cwd: str) -> Path:
    return claude_home(home) / "projects" / project_slug(cwd)


RESUME_TARGETS: dict[str, ResumeTarget] = {
    "claude": ResumeTarget(
        name="claude",
        label="Claude Code",
        build=lambda messages, env, last_prompt: build_session_jsonl(messages, env, last_prompt=last_prompt),
        project_dir=_claude_project_dir,
        filename=lambda sid: f"{sid}.jsonl",
        resume_command=lambda sid: f"claude --resume {sid}",
    ),
}

DEFAULT_TARGET = "claude"


def get_resume_target(name: str) -> ResumeTarget:
    try:
        return RESUME_TARGETS[name]
    except KeyError:
        supported = ", ".join(sorted(RESUME_TARGETS))
        raise ValueError(f"unknown resume target {name!r}; supported: {supported}") from None


def install_resume_session(
    messages: list[dict],
    target_cwd: str,
    *,
    target: str = DEFAULT_TARGET,
    home: Path | None = None,
    version: str = DEFAULT_VERSION,
    git_branch: str = "",
    session_id: str | None = None,
    timestamp: str = "1970-01-01T00:00:00.000Z",
    last_prompt: str = "",
) -> InstalledSession:
    """Write a resumable session into the target CLI's store for ``target_cwd``."""

    resume_target = get_resume_target(target)
    sid = validate_session_id(session_id) if session_id else str(uuid.uuid4())
    env = TransplantEnv(
        cwd=target_cwd,
        session_id=sid,
        version=version,
        git_branch=git_branch,
        timestamp=timestamp,
    )
    document = resume_target.build(messages, env, last_prompt)
    project_dir = resume_target.project_dir(home, target_cwd)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / resume_target.filename(sid)
    path.write_text(document, encoding="utf-8")
    return InstalledSession(
        session_id=sid,
        path=path,
        project_dir=project_dir,
        resume_command=resume_target.resume_command(sid),
        message_count=len(messages),
        target=resume_target.name,
    )


def parse_jsonl_conversation(text: str) -> list[dict]:
    """Rebuild API messages from a Claude Code-shaped session JSONL.

    Lets ``import-resume`` consume either a claude-tap transplant file or a
    native Claude Code session log, merging consecutive same-role blocks back
    into the Messages API ``messages[]`` shape.
    """

    messages: list[dict] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") not in (_USER, _ASSISTANT):
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        blocks = (
            content
            if isinstance(content, list)
            else [{"type": "text", "text": content}]
            if isinstance(content, str)
            else []
        )
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"].extend(blocks)
        else:
            messages.append({"role": role, "content": list(blocks)})
    return messages
