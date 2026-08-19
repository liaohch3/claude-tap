from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from claude_tap.cursor_metadata import (
    _decode_maybe_hex_json,
    _default_cursor_state_db,
    lookup_ai_tracking_model,
    lookup_chat_store_model,
    lookup_chat_store_system_prompt,
    lookup_composer_meta,
    resolve_cursor_conversation_meta,
)
from tests.schema_types import JsonObject


def _write_composer_db(path: Path, conversation_id: str, payload: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)",
        (f"composerData:{conversation_id}", json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def _write_chat_store(path: Path, meta: JsonObject, blobs: list[JsonObject | bytes] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?)",
        ("0", json.dumps(meta).encode("utf-8").hex()),
    )
    conn.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    for index, blob in enumerate(blobs or []):
        if isinstance(blob, (bytes, str)):
            data = blob
        else:
            data = json.dumps(blob).encode("utf-8")
        conn.execute("INSERT INTO blobs(id, data) VALUES (?, ?)", (f"blob-{index}", data))
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


def test_default_cursor_state_db_is_platform_specific(monkeypatch) -> None:
    monkeypatch.setattr("claude_tap.cursor_metadata.sys.platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    linux_path = _default_cursor_state_db()
    assert linux_path.as_posix().endswith(".config/Cursor/User/globalStorage/state.vscdb")

    monkeypatch.setattr("claude_tap.cursor_metadata.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    win_path = _default_cursor_state_db()
    assert "Cursor" in win_path.parts
    assert win_path.name == "state.vscdb"

    monkeypatch.setattr("claude_tap.cursor_metadata.sys.platform", "darwin")
    mac_path = _default_cursor_state_db()
    assert "Application Support" in mac_path.parts


def test_decode_maybe_hex_json_handles_bytes_and_invalid() -> None:
    assert _decode_maybe_hex_json(b'{"lastUsedModel":"x"}') == {"lastUsedModel": "x"}
    assert _decode_maybe_hex_json(b"\xff") is None
    assert _decode_maybe_hex_json("not-json") is None
    assert _decode_maybe_hex_json(["list"]) is None
    assert _decode_maybe_hex_json(json.dumps({"ok": 1}).encode().hex()) == {"ok": 1}


def test_lookup_composer_meta_selected_models_and_token_breakdown(tmp_path: Path) -> None:
    conversation_id = "66666666-6666-6666-6666-666666666666"
    state_db = tmp_path / "state.vscdb"
    _write_composer_db(
        state_db,
        conversation_id,
        {
            "modelConfig": {"selectedModels": [{"modelId": "from-selected"}]},
            "promptTokenBreakdown": {"totalUsedTokens": 99, "maxTokens": 1000},
        },
    )
    meta = lookup_composer_meta(conversation_id, state_db=state_db)
    assert meta is not None
    assert meta.model == "from-selected"
    assert meta.context_tokens_used == 99
    assert meta.context_token_limit == 1000


def test_lookup_composer_meta_skips_empty_payload(tmp_path: Path) -> None:
    conversation_id = "77777777-7777-7777-7777-777777777777"
    state_db = tmp_path / "state.vscdb"
    _write_composer_db(state_db, conversation_id, {"modelConfig": {}})
    assert lookup_composer_meta(conversation_id, state_db=state_db) is None
    assert lookup_composer_meta("", state_db=state_db) is None


def test_lookup_ai_tracking_model(tmp_path: Path) -> None:
    conversation_id = "88888888-8888-8888-8888-888888888888"
    db = tmp_path / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ai_code_hashes (conversationId TEXT, model TEXT)")
    conn.execute(
        "INSERT INTO ai_code_hashes(conversationId, model) VALUES (?, ?)",
        (conversation_id, "tracked-model"),
    )
    conn.execute(
        "INSERT INTO ai_code_hashes(conversationId, model) VALUES (?, ?)",
        (conversation_id, "tracked-model"),
    )
    conn.execute(
        "INSERT INTO ai_code_hashes(conversationId, model) VALUES (?, ?)",
        (conversation_id, "other"),
    )
    conn.commit()
    conn.close()

    meta = lookup_ai_tracking_model(conversation_id, home=tmp_path)
    assert meta is not None
    assert meta.model == "tracked-model"
    assert meta.source == "ai-tracking"
    assert lookup_ai_tracking_model("", home=tmp_path) is None


def test_resolve_falls_back_to_ai_tracking(tmp_path: Path) -> None:
    conversation_id = "99999999-9999-9999-9999-999999999999"
    db = tmp_path / ".cursor" / "ai-tracking" / "ai-code-tracking.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE ai_code_hashes (conversationId TEXT, model TEXT)")
    conn.execute(
        "INSERT INTO ai_code_hashes(conversationId, model) VALUES (?, ?)",
        (conversation_id, "ai-track-only"),
    )
    conn.commit()
    conn.close()

    meta = resolve_cursor_conversation_meta(
        conversation_id,
        home=tmp_path,
        state_db=tmp_path / "missing.vscdb",
    )
    assert meta.model == "ai-track-only"
    assert meta.source == "ai-tracking"


def test_lookup_chat_store_system_prompt_picks_longest_json_blob(tmp_path: Path) -> None:
    conversation_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    _write_chat_store(
        store,
        {"lastUsedModel": "grok-4.6"},
        blobs=[
            {"role": "user", "content": "ignore me"},
            {"role": "system", "content": "short"},
            {"role": "system", "content": [{"type": "text", "text": "You are a longer Cursor system prompt."}]},
            '{"role":"system","content":"also-string-blob"}',
            b"\x00not-json",
        ],
    )

    assert lookup_chat_store_system_prompt(conversation_id, home=tmp_path) == "You are a longer Cursor system prompt."
    assert lookup_chat_store_system_prompt("", home=tmp_path) == ""
    assert lookup_chat_store_system_prompt("missing", home=tmp_path) == ""


def test_lookup_chat_store_system_prompt_without_blobs_table(tmp_path: Path) -> None:
    conversation_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    store = tmp_path / ".cursor" / "chats" / "workspace" / conversation_id / "store.db"
    store.parent.mkdir(parents=True)
    conn = sqlite3.connect(store)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    assert lookup_chat_store_system_prompt(conversation_id, home=tmp_path) == ""
