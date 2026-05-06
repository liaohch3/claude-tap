from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claude_tap import parse_args
from claude_tap.cli import CLIENT_CONFIGS, run_client
from claude_tap.cursor_transcript import import_cursor_transcripts
from claude_tap.trace import TraceWriter


class _DummyProc:
    def __init__(self) -> None:
        self.pid = 12345
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def test_cursor_registered_in_client_configs() -> None:
    cfg = CLIENT_CONFIGS["cursor"]
    assert cfg.cmd == "cursor-agent"
    assert cfg.default_target == "https://api2.cursor.sh"
    assert cfg.default_proxy_mode == "forward"


def test_parse_args_cursor_defaults_to_forward_mode() -> None:
    args = parse_args(["--tap-client", "cursor"])
    assert args.client == "cursor"
    assert args.proxy_mode == "forward"


@pytest.mark.asyncio
async def test_run_client_cursor_forward_sets_proxy_ca_and_no_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}
    ca_path = Path("/tmp/test-ca.pem")

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _DummyProc()

    monkeypatch.setenv("NO_PROXY", "example.com")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/cursor-agent")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    code = await run_client(
        43123,
        ["-p", "--trust", "--model", "auto", "hello"],
        client="cursor",
        proxy_mode="forward",
        ca_cert_path=ca_path,
    )

    assert code == 0
    assert captured["cmd"] == ("/tmp/cursor-agent", "-p", "--trust", "--model", "auto", "hello")
    env = captured["env"]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:43123"
    assert env["NODE_EXTRA_CA_CERTS"] == str(ca_path)
    assert "example.com" in env["NO_PROXY"]
    assert "localhost" in env["NO_PROXY"]
    assert "127.0.0.1" in env["NO_PROXY"]
    assert "::1" in env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"]


@pytest.mark.asyncio
async def test_import_cursor_transcripts_appends_viewer_friendly_records(tmp_path: Path) -> None:
    session_id = "session-123"
    transcript = (
        tmp_path / ".cursor" / "projects" / "project-one" / "agent-transcripts" / session_id / f"{session_id}.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    rows = [
        {
            "role": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "<timestamp>now</timestamp>\n<user_query>\nhello cursor\n</user_query>",
                    }
                ]
            },
        },
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "hello back"}]}},
        {"role": "user", "message": {"content": [{"type": "text", "text": "second turn"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "second answer"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    writer = TraceWriter(tmp_path / "trace.jsonl")
    imported = await import_cursor_transcripts(writer, since=0, home=tmp_path)
    writer.close()

    assert imported == 2
    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert records[0]["transport"] == "cursor-transcript"
    assert records[0]["request"]["body"]["messages"][0]["content"] == "hello cursor"
    assert records[0]["response"]["body"]["content"][0]["text"] == "hello back"
    assert records[1]["request"]["body"]["messages"][0]["content"] == "second turn"
    assert records[1]["response"]["body"]["content"][0]["text"] == "second answer"


@pytest.mark.asyncio
async def test_import_cursor_transcripts_preserves_tool_uses(tmp_path: Path) -> None:
    session_id = "tool-session"
    transcript = (
        tmp_path / ".cursor" / "projects" / "project-one" / "agent-transcripts" / session_id / f"{session_id}.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    rows = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "inspect files"}]}},
        {
            "role": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I will inspect the workspace."},
                    {
                        "type": "tool_use",
                        "name": "Shell",
                        "input": {"command": "pwd && ls", "working_directory": "/tmp/work"},
                    },
                ]
            },
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "ReadFile", "input": {"path": "/tmp/work/sample.txt"}}]
            },
        },
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    writer = TraceWriter(tmp_path / "trace.jsonl")
    imported = await import_cursor_transcripts(writer, since=0, home=tmp_path)
    writer.close()

    assert imported == 3
    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]

    assert records[0]["request"]["path"].endswith("/turn/1/step/1")
    assert records[1]["request"]["path"].endswith("/turn/1/step/2")
    assert records[2]["request"]["path"].endswith("/turn/1/step/3")
    assert records[1]["request"]["body"]["messages"][0]["content"] == "inspect files"

    content = records[0]["response"]["body"]["content"]
    assert content[0] == {"type": "text", "text": "I will inspect the workspace."}
    assert content[1]["type"] == "tool_use"
    assert content[1]["name"] == "Shell"
    assert content[1]["id"] == "cursor_tool_1_2"

    assert records[1]["response"]["body"]["content"][0]["name"] == "ReadFile"
    assert records[2]["response"]["body"]["content"] == [{"type": "text", "text": "done"}]
