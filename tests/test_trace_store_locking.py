"""Regression tests for SQLite trace-store lock contention."""

from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from claude_tap.cli import _create_trace_writer
from claude_tap.codex_app_transcript import CodexAppTranscriptSessionRegistry
from claude_tap.trace import TraceWriter
from claude_tap.trace_store import TraceStore
from tests.conftest import e2e_env, trace_db_path
from tests.test_e2e import PROJECT_ROOT, run_fake_upstream_in_thread

FAKE_LOCKING_CLIENT_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import sqlite3
import urllib.request

locker = None
if os.environ.get("LOCK_SQLITE_DURING_REQUEST") == "1":
    locker = sqlite3.connect(os.environ["CLOUDTAP_DB"], timeout=0.1)
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("UPDATE sessions SET status = status")

request = urllib.request.Request(
    os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages",
    data=json.dumps({
        "model": "claude-test-model",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "lock smoke"}],
    }).encode(),
    headers={
        "Content-Type": "application/json",
        "x-api-key": "sk-ant-test-key",
        "anthropic-version": "2023-06-01",
    },
)
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
finally:
    if locker is not None:
        locker.rollback()
        locker.close()
"""


def _record(index: int) -> dict:
    return {
        "timestamp": f"2026-07-12T08:00:{index:02d}+00:00",
        "turn": index,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {"model": "gpt-5", "input": f"lock test {index}"},
        },
        "response": {
            "status": 200,
            "body": {"output": [], "usage": {"input_tokens": 1, "output_tokens": 1}},
        },
    }


def _append_records_in_process(db_path: str, session_id: str, start: int, count: int) -> int:
    store = TraceStore(Path(db_path))
    try:
        for index in range(start, start + count):
            store.append_record(session_id, _record(index))
    finally:
        store.close()
    return count


def _hold_sqlite_write_lock(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=0.1)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE sessions SET status = status")
    return conn


def _run_locked_proxy_smoke(
    tmp_path: Path,
    upstream_port: int,
    *,
    lock_during_request: bool,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    trace_dir = tmp_path / "traces"
    fake_bin_dir = tmp_path / "bin"
    trace_dir.mkdir(exist_ok=True)
    fake_bin_dir.mkdir(exist_ok=True)
    fake_client = fake_bin_dir / "claude"
    fake_client.write_text(FAKE_LOCKING_CLIENT_SCRIPT, encoding="utf-8")
    fake_client.chmod(fake_client.stat().st_mode | stat.S_IEXEC)

    env = e2e_env(os.environ.copy(), trace_dir)
    db_path = trace_db_path(trace_dir)
    env["PATH"] = f"{fake_bin_dir}{os.pathsep}{env.get('PATH', '')}"
    if lock_during_request:
        env["LOCK_SQLITE_DURING_REQUEST"] = "1"
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "claude_tap",
            "--tap-output-dir",
            str(trace_dir),
            "--tap-no-live",
            "--tap-no-open",
            "--tap-target",
            f"http://127.0.0.1:{upstream_port}",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return process, db_path


def test_read_paths_use_short_lived_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "short-reads.sqlite3"
    writer_store = TraceStore(db_path)
    session_id = writer_store.create_session(client="codex", proxy_mode="reverse")
    writer_store.append_record(session_id, _record(1))
    writer_store.close()

    reader_store = TraceStore(db_path)
    assert [row["id"] for row in reader_store.list_session_rows()] == [session_id]
    assert reader_store.load_records(session_id) == [_record(1)]
    assert reader_store.dashboard_snapshot()[session_id][1] == 1
    assert getattr(reader_store._tls, "conn", None) is None


def test_failed_write_rolls_back_quickly(tmp_path: Path) -> None:
    db_path = tmp_path / "rollback.sqlite3"
    store = TraceStore(db_path)
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    writer_conn = store._connect()
    writer_conn.execute("PRAGMA busy_timeout = 50")
    locker = _hold_sqlite_write_lock(db_path)

    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.append_record(session_id, _record(1))
    finally:
        locker.rollback()
        locker.close()

    assert time.monotonic() - started < 0.5
    assert writer_conn.in_transaction is False
    store.append_record(session_id, _record(2))
    assert store.load_records(session_id) == [_record(2)]


@pytest.mark.asyncio
async def test_trace_writer_drops_locked_record_without_interrupting_capture(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "writer.sqlite3"
    store = TraceStore(db_path)
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    writer = TraceWriter(session_id, store=store)
    await writer.write(_record(1))

    store._connect().execute("PRAGMA busy_timeout = 50")
    locker = _hold_sqlite_write_lock(db_path)
    try:
        await writer.write(_record(2))
    finally:
        locker.rollback()
        locker.close()
    writer.close()

    summary = writer.get_summary()
    stored_summary = json.loads(store.load_session_row(session_id)["summary_json"])
    assert summary["api_calls"] == 2
    assert summary["trace_storage_errors"] == 1
    assert summary["dropped_trace_records"] == 1
    assert stored_summary["trace_storage_errors"] == 1
    assert stored_summary["dropped_trace_records"] == 1
    assert store.load_records(session_id) == [_record(1)]
    assert capsys.readouterr().err.count("continuing without blocking proxy") == 1


def test_cross_process_writes_share_one_serialized_record_sequence(tmp_path: Path) -> None:
    db_path = tmp_path / "multiprocess.sqlite3"
    store = TraceStore(db_path)
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    store.close()

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        futures = [
            executor.submit(_append_records_in_process, str(db_path), session_id, worker * 10, 10)
            for worker in range(4)
        ]
        assert [future.result(timeout=30) for future in futures] == [10, 10, 10, 10]

    reader = TraceStore(db_path)
    records = reader.load_records(session_id)
    assert len(records) == 40
    assert {record["request"]["body"]["input"] for record in records} == {f"lock test {index}" for index in range(40)}


def test_file_lock_failure_becomes_bounded_sqlite_error(tmp_path: Path, monkeypatch) -> None:
    from claude_tap import trace_store

    store = TraceStore(tmp_path / "file-lock.sqlite3")
    session_id = store.create_session(client="codex", proxy_mode="reverse")
    monkeypatch.setattr(trace_store, "WRITE_LOCK_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(trace_store, "WRITE_LOCK_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(trace_store, "_try_lock_file_exclusive", lambda _file: (_ for _ in ()).throw(OSError("busy")))

    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="trace write lock unavailable"):
        store.append_record(session_id, _record(1))
    assert time.monotonic() - started < 0.2


@pytest.mark.asyncio
async def test_startup_lock_creates_non_blocking_writer(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "startup.sqlite3"
    setup = TraceStore(db_path)
    setup.create_session(client="setup")
    setup.close()
    store = TraceStore(db_path)
    locker = _hold_sqlite_write_lock(db_path)
    started = time.monotonic()
    try:
        writer = _create_trace_writer(
            store=store,
            client="codex",
            proxy_mode="reverse",
            metadata={"client": "codex", "proxy_mode": "reverse"},
        )
    finally:
        locker.rollback()
        locker.close()

    assert time.monotonic() - started < 1.5
    await writer.write(_record(1))
    writer.close()
    summary = writer.get_summary()
    assert summary["api_calls"] == 1
    assert summary["trace_storage_errors"] == 2
    assert summary["dropped_trace_records"] == 1
    assert capsys.readouterr().err.count("continuing without blocking proxy") == 1


@pytest.mark.asyncio
async def test_codex_app_registry_does_not_stop_on_startup_lock(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "codex-app-startup.sqlite3"
    setup = TraceStore(db_path)
    setup.create_session(client="setup")
    setup.close()
    store = TraceStore(db_path)
    registry = CodexAppTranscriptSessionRegistry(
        store=store,
        metadata={"client": "codexapp", "proxy_mode": "transcript"},
    )
    locker = _hold_sqlite_write_lock(db_path)
    try:
        await registry.write_next_turn(tmp_path / "rollout.jsonl", _record(1))
    finally:
        locker.rollback()
        locker.close()
    registry.close()

    summary = registry.get_summary()
    assert summary["api_calls"] == 1
    assert summary["trace_storage_errors"] == 2
    assert summary["dropped_trace_records"] == 1
    assert capsys.readouterr().err.count("continuing without blocking proxy") == 1


def test_real_proxy_continues_when_database_is_locked_at_startup(tmp_path: Path) -> None:
    stop_upstream, upstream_port = run_fake_upstream_in_thread()
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "trace_legacy.jsonl").write_text(json.dumps(_record(0)) + "\n", encoding="utf-8")
    db_path = trace_db_path(trace_dir)
    setup = TraceStore(db_path)
    setup.create_session(client="setup")
    setup.close()
    locker = _hold_sqlite_write_lock(db_path)
    try:
        process, _ = _run_locked_proxy_smoke(tmp_path, upstream_port, lock_during_request=False)
    finally:
        locker.rollback()
        locker.close()
        stop_upstream()

    assert process.returncode == 0, process.stderr
    assert "API calls: 1" in process.stdout
    assert "legacy trace migration skipped" in process.stderr
    assert "continuing without blocking proxy" in process.stderr


def test_real_proxy_continues_when_database_locks_during_request(tmp_path: Path) -> None:
    stop_upstream, upstream_port = run_fake_upstream_in_thread()
    try:
        process, db_path = _run_locked_proxy_smoke(tmp_path, upstream_port, lock_during_request=True)
    finally:
        stop_upstream()

    assert process.returncode == 0, process.stderr
    assert "API calls: 1" in process.stdout
    assert "Trace storage errors: 1" in process.stdout
    assert "Dropped trace records: 1" in process.stdout
    assert "continuing without blocking proxy" in process.stderr

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT status, summary_json FROM sessions WHERE client = 'claude'").fetchone()
    finally:
        conn.close()
    assert row is not None
    summary = json.loads(row[1])
    assert row[0] == "complete"
    assert summary["trace_storage_errors"] == 1
    assert summary["dropped_trace_records"] == 1
