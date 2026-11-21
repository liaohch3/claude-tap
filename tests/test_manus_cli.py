"""Contracts for the Manus Studio capture command."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_tap import cli
from claude_tap.trace_store import TraceStore

_TASK_ID = "0123456789abcdefghijkl"


def test_manus_capture_defaults_to_loopback_and_accepts_forward_proxy_options():
    args = cli.parse_args(["--tap-client", "manus", "--tap-no-launch", "--tap-port", "18877", "--tap-trust-ca"])

    assert (args.host, args.port, args.no_launch, args.trust_ca) == ("127.0.0.1", 18877, True, True)
    assert args.claude_args == []
    assert cli.parse_args(["--tap-client", "manus", "--tap-proxy-mode", "forward"]).host == "127.0.0.1"


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--tap-host", "0.0.0.0"], "requires a loopback"),
        (["--tap-target", "https://example.test"], "does not support reverse proxy"),
        (["--tap-proxy-mode", "reverse"], "does not support reverse proxy"),
        (["--", "app-argument"], "does not accept app arguments"),
    ],
)
def test_manus_rejects_exposed_listeners_and_unsupported_options(flags, message, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--tap-client", "manus", *flags])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


async def test_manus_capture_requires_macos_before_touching_storage_or_trust(tmp_path, monkeypatch):
    args = cli.parse_args(["--tap-client", "manus", "--tap-output-dir", str(tmp_path / "output")])
    monkeypatch.setattr(cli.sys, "platform", "linux")
    dashboard, create_ca = AsyncMock(), MagicMock()
    monkeypatch.setattr(cli, "ensure_shared_dashboard", dashboard)
    monkeypatch.setattr(cli, "ensure_ca", create_ca)

    assert await cli.async_main(args) == 1
    assert not (tmp_path / "output").exists()
    dashboard.assert_not_awaited()
    create_ca.assert_not_called()


async def test_manus_trusts_the_ca_then_captures_without_the_generic_client_launcher(tmp_path, monkeypatch):
    args = cli.parse_args(["--tap-client", "manus", "--tap-output-dir", str(tmp_path)])
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    store = object()
    cert, key = tmp_path / "ca.pem", tmp_path / "ca-key.pem"
    monkeypatch.setattr(cli, "get_trace_store", lambda: store)
    monkeypatch.setattr(cli, "ensure_ca", lambda: (cert, key))
    monkeypatch.setattr(cli, "is_macos_ca_trusted", lambda path: False)
    trust = MagicMock(return_value=0)
    capture = AsyncMock(return_value=0)
    launch = AsyncMock()
    monkeypatch.setattr(cli, "_trust_ca_for_current_user", trust)
    monkeypatch.setattr(cli, "ensure_shared_dashboard", AsyncMock(return_value=("http://127.0.0.1:19527", False)))
    monkeypatch.setattr(cli, "_run_manus_capture", capture)
    monkeypatch.setattr(cli, "run_client", launch)

    assert await cli.async_main(args) == 0
    trust.assert_called_once_with(cert)
    capture.assert_awaited_once_with(args, store, cert, key)
    launch.assert_not_awaited()

    trust.return_value = 1
    capture.reset_mock()
    assert await cli.async_main(args) == 1
    capture.assert_not_awaited()


@pytest.mark.parametrize(
    ("installed", "running", "message"),
    [(False, False, "was not found"), (True, True, "already open")],
)
async def test_manus_capture_refuses_a_missing_or_already_open_app(
    tmp_path, monkeypatch, capsys, installed, running, message
):
    app = tmp_path / "Manus Studio"
    if installed:
        app.write_text("")
    monkeypatch.setenv("CLAUDE_TAP_MANUS_APP", str(app))
    monkeypatch.setattr(cli, "_manus_is_running", lambda path: running)
    proxy = MagicMock()
    monkeypatch.setattr(cli, "ForwardProxyServer", proxy)
    args = cli.parse_args(["--tap-client", "manus"])

    assert await cli._run_manus_capture(args, object(), tmp_path / "ca.pem", tmp_path / "ca-key.pem") == 1
    assert message in capsys.readouterr().err
    proxy.assert_not_called()


async def test_manus_capture_routes_the_app_through_the_proxy_and_records_only_task_events(tmp_path, monkeypatch):
    app = tmp_path / "Manus Studio"
    app.write_text("")
    monkeypatch.setenv("CLAUDE_TAP_MANUS_APP", str(app))
    monkeypatch.setattr(cli, "_manus_is_running", lambda path: False)
    monkeypatch.setattr(cli, "CertificateAuthority", MagicMock())
    store = TraceStore(tmp_path / "traces.db")
    proxies = []

    class FakeProxy:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.stopped = False
            proxies.append(self)

        async def start(self):
            return 18877

        async def stop(self):
            self.stopped = True

    chat = {"id": "u1", "type": "chat", "sender": "user", "content": "hello", "timestamp": 1795269800000}
    packet = "42" + json.dumps(["message", {"type": "event", "sessionId": _TASK_ID, "event": chat}])

    async def launch(*command, **kwargs):
        observe = proxies[0].kwargs["ws_text_observer"]
        observe("api.manus.im", "/notifier-v2-socket-io/?EIO=4", packet)
        observe("api.manus.im", "/socket.io/?EIO=4", packet)
        process = MagicMock(returncode=0)
        process.wait = AsyncMock(return_value=0)
        launch.command = command
        return process

    monkeypatch.setattr(cli, "ForwardProxyServer", FakeProxy)
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", launch)
    cleanup = MagicMock()
    monkeypatch.setattr(cli, "cleanup_trace_sessions", cleanup)
    args = cli.parse_args(["--tap-client", "manus"])

    assert await cli._run_manus_capture(args, store, tmp_path / "ca.pem", tmp_path / "ca-key.pem") == 0

    assert launch.command == (str(app), "--proxy-server=http://127.0.0.1:18877")
    # No HTTP method matches the filter, so ordinary Manus traffic is never stored.
    assert proxies[0].kwargs["trace_methods"] == ("MANUS_TASK_EVENT",)
    assert proxies[0].stopped
    [session] = store.list_session_rows()
    assert (session["client"], session["record_count"], session["status"]) == ("manus", 1, "complete")
    cleanup.assert_called_once_with(args.max_traces, protected_session_ids={session["id"]})


async def test_manus_capture_stops_the_app_when_interrupted(tmp_path, monkeypatch):
    app = tmp_path / "Manus Studio"
    app.write_text("")
    monkeypatch.setenv("CLAUDE_TAP_MANUS_APP", str(app))
    monkeypatch.setattr(cli, "_manus_is_running", lambda path: False)
    monkeypatch.setattr(cli, "CertificateAuthority", MagicMock())
    process = MagicMock(returncode=None)
    exited = asyncio.Event()
    stop_order = []
    asyncio_log = cli.logging.getLogger("asyncio")
    logging_before = (asyncio_log.propagate, list(asyncio_log.handlers))

    async def stop_proxy():
        # The proxy cannot finish stopping while the app still holds connections open.
        stop_order.append(("proxy", exited.is_set()))

    proxy = MagicMock()
    proxy.return_value.start = AsyncMock(return_value=18877)
    proxy.return_value.stop = AsyncMock(side_effect=stop_proxy)
    monkeypatch.setattr(cli, "ForwardProxyServer", proxy)

    async def wait():
        await exited.wait()
        return 0

    process.wait = wait
    process.terminate = MagicMock(side_effect=exited.set)
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    args = cli.parse_args(["--tap-client", "manus"])

    capture = asyncio.create_task(
        cli._run_manus_capture(args, TraceStore(tmp_path / "traces.db"), tmp_path / "ca.pem", tmp_path / "ca-key.pem")
    )
    await asyncio.sleep(0.05)
    capture.cancel()

    assert await capture == 0
    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()
    assert stop_order == [("proxy", True)]
    assert (asyncio_log.propagate, asyncio_log.handlers) == logging_before


async def test_manus_no_launch_prints_the_command_instead_of_starting_the_app(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_TAP_MANUS_APP", str(tmp_path / "missing"))
    monkeypatch.setattr(cli, "CertificateAuthority", MagicMock())
    proxy = MagicMock()
    proxy.return_value.start = AsyncMock(return_value=18877)
    proxy.return_value.stop = AsyncMock()
    monkeypatch.setattr(cli, "ForwardProxyServer", proxy)
    launch = AsyncMock()
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", launch)
    args = cli.parse_args(["--tap-client", "manus", "--tap-no-launch"])

    capture = asyncio.create_task(
        cli._run_manus_capture(args, TraceStore(tmp_path / "traces.db"), tmp_path / "ca.pem", tmp_path / "ca-key.pem")
    )
    await asyncio.sleep(0.05)
    capture.cancel()

    assert await capture == 0
    assert "--proxy-server=http://127.0.0.1:18877" in capsys.readouterr().err
    launch.assert_not_awaited()
