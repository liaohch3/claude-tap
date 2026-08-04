"""Enrich Cursor transcript records from local Cursor IDE / CLI state.

Agent transcript JSONL files do not include model or billed token usage.
Claude-tap recovers what it can from:

1. IDE ``state.vscdb`` ``composerData:{uuid}.modelConfig.modelName``
2. CLI ``~/.cursor/chats/*/{uuid}/store.db`` meta ``lastUsedModel``
3. ``~/.cursor/ai-tracking/ai-code-tracking.db`` ``ai_code_hashes.model``

Billed per-turn ``input_tokens`` / ``output_tokens`` are not available locally.
``context_tokens_used`` is a context-window estimate only, not API usage.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("claude-tap")


@dataclass(frozen=True)
class CursorConversationMeta:
    model: str = ""
    context_tokens_used: int | None = None
    context_token_limit: int | None = None
    source: str = ""


def _default_cursor_state_db() -> Path:
    """Return the Cursor IDE ``state.vscdb`` path for the current platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    # Linux / other Unix: Electron default under ~/.config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _macos_cursor_state_db() -> Path:
    """Backward-compatible alias for macOS Cursor state DB."""
    return Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _cursor_home(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".cursor"


def _connect_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        log.debug("Failed to open Cursor DB %s: %s", path, exc)
        return None


def _decode_maybe_hex_json(value: object) -> dict | None:
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str) or not value:
        return None
    text = value
    if all(ch in "0123456789abcdefABCDEF" for ch in text) and len(text) % 2 == 0:
        try:
            text = bytes.fromhex(text).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def lookup_composer_meta(
    conversation_id: str,
    *,
    state_db: Path | None = None,
) -> CursorConversationMeta | None:
    """Read IDE composerData for a conversation UUID."""
    if not conversation_id:
        return None
    conn = _connect_readonly(state_db or _default_cursor_state_db())
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?",
            (f"composerData:{conversation_id}",),
        ).fetchone()
    except sqlite3.Error as exc:
        log.debug("composerData lookup failed for %s: %s", conversation_id, exc)
        return None
    finally:
        conn.close()

    if not row:
        return None
    data = row[0]
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(data, str):
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    model = ""
    model_config = payload.get("modelConfig")
    if isinstance(model_config, dict):
        name = model_config.get("modelName")
        if isinstance(name, str) and name.strip():
            model = name.strip()
        if not model:
            selected = model_config.get("selectedModels")
            if isinstance(selected, list) and selected:
                first = selected[0]
                if isinstance(first, dict):
                    mid = first.get("modelId")
                    if isinstance(mid, str) and mid.strip():
                        model = mid.strip()

    context_used = payload.get("contextTokensUsed")
    context_limit = payload.get("contextTokenLimit")
    if not isinstance(context_used, int):
        breakdown = payload.get("promptTokenBreakdown")
        if isinstance(breakdown, dict) and isinstance(breakdown.get("totalUsedTokens"), int):
            context_used = breakdown["totalUsedTokens"]
        else:
            context_used = None
    if not isinstance(context_limit, int):
        breakdown = payload.get("promptTokenBreakdown")
        if isinstance(breakdown, dict) and isinstance(breakdown.get("maxTokens"), int):
            context_limit = breakdown["maxTokens"]
        else:
            context_limit = None

    if not model and context_used is None:
        return None
    return CursorConversationMeta(
        model=model,
        context_tokens_used=context_used,
        context_token_limit=context_limit,
        source="composerData",
    )


def lookup_chat_store_model(
    conversation_id: str,
    *,
    home: Path | None = None,
) -> CursorConversationMeta | None:
    """Read CLI chat store meta.lastUsedModel for a conversation UUID."""
    if not conversation_id:
        return None
    chats_root = _cursor_home(home) / "chats"
    if not chats_root.is_dir():
        return None
    for store in chats_root.glob(f"*/{conversation_id}/store.db"):
        conn = _connect_readonly(store)
        if conn is None:
            continue
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.Error as exc:
            log.debug("chat store meta lookup failed for %s: %s", store, exc)
            continue
        finally:
            conn.close()

        for _key, value in rows:
            payload = _decode_maybe_hex_json(value)
            if not payload:
                continue
            model = payload.get("lastUsedModel")
            if isinstance(model, str) and model.strip():
                return CursorConversationMeta(model=model.strip(), source="chat-store")
    return None


def lookup_ai_tracking_model(
    conversation_id: str,
    *,
    home: Path | None = None,
) -> CursorConversationMeta | None:
    """Best-effort model from ai-code-tracking hashes for a conversation."""
    if not conversation_id:
        return None
    db = _cursor_home(home) / "ai-tracking" / "ai-code-tracking.db"
    conn = _connect_readonly(db)
    if conn is None:
        return None
    try:
        row = conn.execute(
            """
            SELECT model, COUNT(*) AS n
            FROM ai_code_hashes
            WHERE conversationId = ? AND model IS NOT NULL AND TRIM(model) != ''
            GROUP BY model
            ORDER BY n DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        log.debug("ai-tracking lookup failed for %s: %s", conversation_id, exc)
        return None
    finally:
        conn.close()
    if not row or not isinstance(row[0], str) or not row[0].strip():
        return None
    return CursorConversationMeta(model=row[0].strip(), source="ai-tracking")


def resolve_cursor_conversation_meta(
    conversation_id: str,
    *,
    home: Path | None = None,
    state_db: Path | None = None,
) -> CursorConversationMeta:
    """Resolve the best available local metadata for a Cursor conversation UUID.

    Lookups are ordered by trust. Fields are merged so a source that only has
    context tokens does not block a later source from filling in the model.
    """
    model = ""
    model_source = ""
    context_tokens_used: int | None = None
    context_token_limit: int | None = None
    context_source = ""

    for lookup in (
        lambda: lookup_composer_meta(conversation_id, state_db=state_db),
        lambda: lookup_chat_store_model(conversation_id, home=home),
        lambda: lookup_ai_tracking_model(conversation_id, home=home),
    ):
        try:
            meta = lookup()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Cursor metadata lookup error for %s: %s", conversation_id, exc)
            continue
        if meta is None:
            continue
        if not model and meta.model:
            model = meta.model
            model_source = meta.source
        if context_tokens_used is None and meta.context_tokens_used is not None:
            context_tokens_used = meta.context_tokens_used
            context_token_limit = meta.context_token_limit
            context_source = meta.source
        if model and context_tokens_used is not None:
            break

    if not model and context_tokens_used is None:
        return CursorConversationMeta()
    return CursorConversationMeta(
        model=model,
        context_tokens_used=context_tokens_used,
        context_token_limit=context_token_limit,
        source=model_source or context_source,
    )
