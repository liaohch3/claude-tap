from __future__ import annotations

import asyncio
import signal

import pytest

from claude_tap.cli import run_client


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP is Unix-only")
@pytest.mark.asyncio
async def test_run_client_escalates_sighup_to_kill_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, object] = {}
    removed: list[signal.Signals] = []

    def add_signal_handler(sig: signal.Signals, callback) -> None:
        handlers[sig] = callback

    def remove_signal_handler(sig: signal.Signals) -> bool:
        removed.append(sig)
        handlers.pop(sig, None)
        return True

    class DummyProc:
        pid = 12345
        returncode: int | None = None
        terminate_calls = 0
        kill_calls = 0
        killed = asyncio.Event()

        async def wait(self) -> int:
            assert signal.SIGHUP in handlers
            handlers[signal.SIGHUP]()
            assert self.terminate_calls == 1
            assert self.kill_calls == 0
            await asyncio.wait_for(self.killed.wait(), timeout=0.5)
            assert self.returncode is not None
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.kill_calls += 1
            self.returncode = -9
            self.killed.set()

    proc = DummyProc()

    async def fake_create_subprocess_exec(*_cmd, **_kwargs):
        return proc

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(signal, "signal", lambda _sig, _handler: signal.SIG_DFL)
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)
    monkeypatch.setattr("claude_tap.cli_clients._SIGHUP_TERMINATE_GRACE_SECONDS", 0.01, raising=False)

    code = await run_client(43123, ["--version"], client="claude", proxy_mode="reverse")

    assert code == -9
    assert proc.terminate_calls == 1
    assert proc.kill_calls == 1
    assert signal.SIGHUP in removed


@pytest.mark.skipif(not hasattr(signal, "SIGTSTP"), reason="SIGTSTP is Unix-only")
@pytest.mark.asyncio
async def test_run_client_removes_sigtstp_before_restoring_prior_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, object] = {}
    dispositions: dict[signal.Signals, object] = {}
    cleanup_order: list[str] = []

    def prior_sigtstp(_signum, _frame) -> None:
        pass

    dispositions[signal.SIGTSTP] = prior_sigtstp

    class DummyProc:
        pid = 12345
        returncode: int | None = 0

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*_cmd, **_kwargs):
        return DummyProc()

    def set_signal(sig: signal.Signals, handler) -> object:
        previous = dispositions.get(sig, signal.SIG_DFL)
        dispositions[sig] = handler
        if sig == signal.SIGTSTP and handler is prior_sigtstp:
            cleanup_order.append("restore")
        return previous

    def add_signal_handler(sig: signal.Signals, callback) -> None:
        handlers[sig] = callback

    def remove_signal_handler(sig: signal.Signals) -> bool:
        handlers.pop(sig, None)
        if sig == signal.SIGTSTP:
            cleanup_order.append("remove")
            dispositions[sig] = signal.SIG_DFL
        return True

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(signal, "signal", set_signal)
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)

    code = await run_client(43123, ["--version"], client="claude", proxy_mode="reverse")

    assert code == 0
    assert cleanup_order == ["remove", "restore"]
    assert dispositions[signal.SIGTSTP] is prior_sigtstp


@pytest.mark.skipif(
    not hasattr(signal, "SIGHUP") or not hasattr(signal, "SIGTSTP"),
    reason="SIGHUP and SIGTSTP are Unix-only",
)
@pytest.mark.parametrize(
    "wait_error",
    [RuntimeError("wait failed"), asyncio.CancelledError()],
    ids=["raises", "cancelled"],
)
@pytest.mark.asyncio
async def test_run_client_removes_signal_handlers_when_wait_fails(
    monkeypatch: pytest.MonkeyPatch, wait_error: BaseException
) -> None:
    loop = asyncio.get_running_loop()
    handlers: dict[signal.Signals, object] = {}
    removed: list[signal.Signals] = []

    class DummyProc:
        pid = 12345
        returncode: int | None = None

        async def wait(self) -> int:
            assert {signal.SIGINT, signal.SIGTSTP, signal.SIGHUP} <= handlers.keys()
            raise wait_error

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    async def fake_create_subprocess_exec(*_cmd, **_kwargs):
        return DummyProc()

    def add_signal_handler(sig: signal.Signals, callback) -> None:
        handlers[sig] = callback

    def remove_signal_handler(sig: signal.Signals) -> bool:
        removed.append(sig)
        handlers.pop(sig, None)
        return True

    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _: "/tmp/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(signal, "signal", lambda _sig, _handler: signal.SIG_DFL)
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    monkeypatch.setattr(loop, "remove_signal_handler", remove_signal_handler)

    with pytest.raises(type(wait_error)):
        await run_client(43123, ["--version"], client="claude", proxy_mode="reverse")

    assert {signal.SIGINT, signal.SIGTSTP, signal.SIGHUP} <= set(removed)
    assert not handlers
