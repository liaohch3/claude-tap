import sys
from unittest.mock import AsyncMock, Mock

import pytest

from claude_tap.manus_desktop import ManusDesktopError, ManusDesktopReader, _run_helper


async def test_helper_output_is_decoded_as_an_accessibility_snapshot(tmp_path, monkeypatch):
    helper = AsyncMock(return_value=b'{"status":"no_task"}')
    monkeypatch.setattr("claude_tap.manus_desktop._run_helper", helper)
    executable = tmp_path / "snapshot-helper"

    assert await ManusDesktopReader(executable).read() == {"status": "no_task"}
    assert helper.await_args.args == (str(executable),)


async def test_only_enrich_an_open_conversation(tmp_path, monkeypatch):
    helper = AsyncMock(
        side_effect=[
            b'{"status":"conversation_unavailable","task_id":"0123456789abcdefghijkl"}',
            b'{"status":"ok","task_id":"0123456789abcdefghijkl","conversation":{}}',
            b'{"status":"ok","task_id":"0123456789abcdefghijkl","conversation":{}}',
        ]
    )
    event = {"id": "tool-1", "type": "tool_call", "tool_name": "terminal", "command": "pwd", "output": "/tmp"}
    cache = Mock(side_effect=[[event], []])
    monkeypatch.setattr("claude_tap.manus_desktop._run_helper", helper)
    monkeypatch.setattr("claude_tap.manus_desktop.read_terminal_events", cache)
    reader = ManusDesktopReader(tmp_path / "helper")
    assert (await reader.read())["status"] == "conversation_unavailable"
    cache.assert_not_called()
    assert (await reader.read())["terminal_events"] == [event]
    cache.assert_called_once_with("0123456789abcdefghijkl")
    assert "terminal_events" not in await reader.read()


@pytest.mark.parametrize("output", [b"private task text", b'{"private task text":true}'])
async def test_helper_errors_do_not_expose_task_content(tmp_path, monkeypatch, output):
    monkeypatch.setattr("claude_tap.manus_desktop._run_helper", AsyncMock(return_value=output))

    with pytest.raises(ManusDesktopError, match="Could not read the Manus Studio Accessibility snapshot") as exc:
        await ManusDesktopReader(tmp_path / "bad-snapshot-helper").read()
    assert "private task text" not in str(exc.value)


async def test_helper_process_returns_output_without_printing_stderr():
    command = "import sys; print('snapshot'); print('private task text', file=sys.stderr)"
    output = await _run_helper(sys.executable, "-c", command, timeout=10, failure="helper did not start")

    assert output.splitlines() == [b"snapshot"]


async def test_timed_out_accessibility_helper_is_terminated():
    with pytest.raises(ManusDesktopError, match="helper timed out"):
        await _run_helper(sys.executable, "-c", "import time; time.sleep(60)", timeout=0.01, failure="helper timed out")
