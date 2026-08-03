from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from claude_tap.cursor_metadata import (
    lookup_chat_store_model,
    lookup_composer_meta,
    resolve_cursor_conversation_meta,
)


def _write_composer_db(path: Path, conversation_id: str, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)",
        (f"composerData:{conversation_id}", json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def _write_chat_store(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("0", json.dumps(meta).encode("utf-8").hex()),
    )
    conn.commit()
    conn.close()


def test_lookup_composer_meta_reads_model_and_context(tmp_path: Path) -> None:
    conversation_id = "11111111-1111-1111-1111-111111111111"
    state_db = tmp_path / "state.vscdb"
    _write_composer_db(
        state_db,
        conversation_id,
        {
            "modelConfig": {
                "modelName": "grok-4.5",
                "selectedModels": [{"modelId": "grok-4.5"}],
            },
            "contextTokensUsed": 1200,
            "contextTokenLimit": 256000,
        },
    )

    meta = lookup_composer_meta(conversation_id, state_db=state_db)
    assert meta is not None
    assert meta.model == "grok-4.5"
    assert meta.context_tokens_used == 1200
    assert meta.context_token_limit == 256000
    assert meta.source == "composerData"


def test_lookup_chat_store_model_decodes_hex_meta(tmp_path: Path) -> None:
    conversation_id = "22222222-2222-2222-2222-222222222222"
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    _write_chat_store(store, {"agentId": conversation_id, "lastUsedModel": "claude-opus-5"})

    meta = lookup_chat_store_model(conversation_id, home=tmp_path)
    assert meta is not None
    assert meta.model == "claude-opus-5"
    assert meta.source == "chat-store"


def test_resolve_prefers_composer_then_chat_store(tmp_path: Path) -> None:
    conversation_id = "33333333-3333-3333-3333-333333333333"
    state_db = tmp_path / "state.vscdb"
    _write_composer_db(
        state_db,
        conversation_id,
        {"modelConfig": {"modelName": "composer-from-ide"}},
    )
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    _write_chat_store(store, {"lastUsedModel": "model-from-cli"})

    meta = resolve_cursor_conversation_meta(conversation_id, home=tmp_path, state_db=state_db)
    assert meta.model == "composer-from-ide"
    assert meta.source == "composerData"


def test_resolve_falls_back_to_chat_store(tmp_path: Path) -> None:
    conversation_id = "44444444-4444-4444-4444-444444444444"
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    _write_chat_store(store, {"lastUsedModel": "grok-4.5"})

    meta = resolve_cursor_conversation_meta(
        conversation_id,
        home=tmp_path,
        state_db=tmp_path / "missing.vscdb",
    )
    assert meta.model == "grok-4.5"
    assert meta.source == "chat-store"


def test_resolve_merges_composer_context_with_chat_store_model(tmp_path: Path) -> None:
    conversation_id = "55555555-5555-5555-5555-555555555555"
    state_db = tmp_path / "state.vscdb"
    _write_composer_db(
        state_db,
        conversation_id,
        {"contextTokensUsed": 2048, "contextTokenLimit": 256000},
    )
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    _write_chat_store(store, {"lastUsedModel": "grok-4.5"})

    meta = resolve_cursor_conversation_meta(conversation_id, home=tmp_path, state_db=state_db)
    assert meta.model == "grok-4.5"
    assert meta.context_tokens_used == 2048
    assert meta.context_token_limit == 256000
    assert meta.source == "chat-store"
