from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.history import _load_manifest, _register_trace, _save_manifest, delete_trace_history


def _write_trace_files(base: Path, stem: str) -> list[str]:
    jsonl = base / f"{stem}.jsonl"
    jsonl.write_text(json.dumps({"request_id": stem}) + "\n", encoding="utf-8")
    files = [jsonl]
    for suffix in (".log", ".html"):
        companion = base / f"{stem}{suffix}"
        companion.write_text(stem, encoding="utf-8")
        files.append(companion)
    return [path.relative_to(base.parents[0]).as_posix() for path in files]


def test_delete_trace_history_removes_selected_date_and_updates_manifest(tmp_path: Path) -> None:
    output_dir = tmp_path
    date_dir = output_dir / "2026-05-01"
    date_dir.mkdir()
    other_date_dir = output_dir / "2026-05-02"
    other_date_dir.mkdir()
    _save_manifest(output_dir, {"_cloudtap": True, "version": "test", "traces": []})

    old_files = _write_trace_files(date_dir, "trace_old")
    protected_files = _write_trace_files(date_dir, "trace_active")
    other_files = _write_trace_files(other_date_dir, "trace_other")
    note = date_dir / "notes.jsonl"
    note.write_text("not a claude-tap trace", encoding="utf-8")

    _register_trace(output_dir, "20260501_old", old_files)
    _register_trace(output_dir, "20260501_active", protected_files)
    _register_trace(output_dir, "20260502_other", other_files)

    result = delete_trace_history(output_dir, "2026-05-01", protected_paths=[date_dir / "trace_active.jsonl"])

    assert result["deleted_files"] == 3
    assert result["deleted_traces"] == 1
    assert not (date_dir / "trace_old.jsonl").exists()
    assert not (date_dir / "trace_old.log").exists()
    assert not (date_dir / "trace_old.html").exists()
    assert (date_dir / "trace_active.jsonl").exists()
    assert (other_date_dir / "trace_other.jsonl").exists()
    assert note.exists()

    manifest = _load_manifest(output_dir)
    timestamps = {entry["timestamp"] for entry in manifest["traces"]}
    assert "20260501_old" not in timestamps
    assert "20260501_active" in timestamps
    assert "20260502_other" in timestamps


def test_delete_trace_history_removes_legacy_flat_files(tmp_path: Path) -> None:
    output_dir = tmp_path
    _save_manifest(output_dir, {"_cloudtap": True, "version": "test", "traces": []})
    legacy_files = []
    for suffix in (".jsonl", ".log", ".html"):
        path = output_dir / f"trace_legacy{suffix}"
        path.write_text("legacy", encoding="utf-8")
        legacy_files.append(path.name)
    keep = output_dir / "manual_export.jsonl"
    keep.write_text("keep", encoding="utf-8")
    _register_trace(output_dir, "legacy", legacy_files)

    result = delete_trace_history(output_dir, "legacy")

    assert result["deleted_files"] == 3
    assert not (output_dir / "trace_legacy.jsonl").exists()
    assert keep.exists()
    assert _load_manifest(output_dir)["traces"] == []


def test_register_trace_stores_client_metadata(tmp_path: Path) -> None:
    output_dir = tmp_path
    _save_manifest(output_dir, {"_cloudtap": True, "version": "test", "traces": []})

    manifest = _register_trace(
        output_dir,
        "20260520_120000",
        ["2026-05-20/trace_120000.jsonl"],
        metadata={"client": "agy", "proxy_mode": "reverse"},
    )

    assert manifest["traces"][0]["client"] == "agy"
    assert manifest["traces"][0]["proxy_mode"] == "reverse"


@pytest.mark.asyncio
async def test_live_viewer_delete_history_endpoint(tmp_path: Path) -> None:
    import aiohttp

    from claude_tap import LiveViewerServer

    output_dir = tmp_path
    date_dir = output_dir / "2026-05-01"
    date_dir.mkdir()
    _save_manifest(output_dir, {"_cloudtap": True, "version": "test", "traces": []})
    files = _write_trace_files(date_dir, "trace_delete_me")
    _register_trace(output_dir, "20260501_delete_me", files)

    server = LiveViewerServer(output_dir / "2026-05-18" / "trace_current.jsonl", port=0, output_dir=output_dir)
    port = await server.start()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"http://127.0.0.1:{port}/api/traces/2026-05-01") as resp:
                assert resp.status == 200
                payload = await resp.json()
                assert payload["deleted_files"] == 3

            async with session.get(f"http://127.0.0.1:{port}/api/traces/2026-05-01") as resp:
                assert resp.status == 200
                assert await resp.json() == []

            async with session.delete(f"http://127.0.0.1:{port}/api/traces/not-a-date") as resp:
                assert resp.status == 400
    finally:
        await server.stop()
