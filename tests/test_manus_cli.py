from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_tap import cli


def test_manus_watch_defaults_to_loopback():
    args = cli.parse_args(["--tap-client", "manus", "--tap-no-launch"])

    assert args.host == "127.0.0.1"
    assert args.claude_args == []


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--tap-host", "0.0.0.0"], "requires a loopback"),
        (["--tap-target", "https://example.test"], "does not support proxy"),
        (["--tap-proxy-mode", "forward"], "does not support proxy"),
        (["--", "app-argument"], "does not launch an app"),
    ],
)
def test_manus_rejects_conflicting_listen_or_proxy_options(flags, message, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--tap-client", "manus", *flags])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


async def test_manus_watch_requires_macos_before_starting_dashboard(tmp_path, monkeypatch):
    args = cli.parse_args(["--tap-client", "manus", "--tap-output-dir", str(tmp_path / "output")])
    monkeypatch.setattr(cli.sys, "platform", "linux")
    dashboard = AsyncMock()
    monkeypatch.setattr(cli, "ensure_shared_dashboard", dashboard)

    assert await cli.async_main(args) == 1
    assert not (tmp_path / "output").exists()
    dashboard.assert_not_awaited()


async def test_manus_uses_watch_path_without_proxy_or_app_launch(tmp_path, monkeypatch):
    args = cli.parse_args(["--tap-client", "manus", "--tap-output-dir", str(tmp_path)])
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    store = object()
    monkeypatch.setattr(cli, "get_trace_store", lambda: store)
    dashboard = AsyncMock(return_value=("http://127.0.0.1:19527", False))
    watch = AsyncMock(return_value=0)
    create_ca = MagicMock()
    launch = AsyncMock()
    monkeypatch.setattr(cli, "ensure_shared_dashboard", dashboard)
    monkeypatch.setattr(cli, "_run_manus_watch", watch)
    monkeypatch.setattr(cli, "ensure_ca", create_ca)
    monkeypatch.setattr(cli, "run_client", launch)

    assert await cli.async_main(args) == 0
    assert dashboard.await_args.kwargs["host"] == "127.0.0.1"
    watch.assert_awaited_once_with(args, store)
    create_ca.assert_not_called()
    launch.assert_not_awaited()


async def test_manus_waits_for_open_task_and_handles_missing_accessibility_permission(monkeypatch, capsys):
    args = cli.parse_args(["--tap-client", "manus"])
    task = {"status": "ok", "task_id": "Abcdef0123456789abcdef", "conversation": {}}
    reader = AsyncMock()
    reader.read.side_effect = [
        {"status": "no_task"},
        {"status": "no_task"},
        task,
        {"status": "permission_denied"},
    ]

    @asynccontextmanager
    async def open_reader():
        yield reader

    watcher_factory = MagicMock()
    watcher = watcher_factory.return_value
    watcher.session_ids = set()
    monkeypatch.setattr(cli, "ManusWatcher", watcher_factory)
    monkeypatch.setattr(cli, "manus_desktop_reader", open_reader)
    monkeypatch.setattr(cli, "_MANUS_POLL_SECONDS", 0)

    assert await cli._run_manus_watch(args, object()) == 1
    assert [call.args[0] for call in watcher.sync_snapshot.call_args_list] == [
        {"status": "no_task"},
        {"status": "no_task"},
        task,
    ]
    watcher.close.assert_called_once_with()
    err = capsys.readouterr().err
    assert err.count("Open a task in Manus Studio") == 1
    assert "Privacy & Security → Accessibility" in err
