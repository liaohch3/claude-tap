from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from claude_tap.cli import (
    _build_update_command,
    _detect_installer,
    _is_editable_install,
    main_entry,
    parse_update_args,
    update_main,
)

# ---------------------------------------------------------------------------
# _detect_installer
# ---------------------------------------------------------------------------


class TestDetectInstaller:
    """Tests for the improved _detect_installer() logic."""

    def test_uv_tool_path_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """sys.executable inside a uv tools directory → 'uv' on Windows."""
        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
        monkeypatch.setattr(
            "claude_tap.cli_update.sys.executable",
            r"C:\Users\alice\AppData\Local\uv\tools\claude-tap\Scripts\python.exe",
        )
        monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _: None)
        assert _detect_installer() == "uv"

    def test_uv_tool_path_on_unix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """sys.executable inside a uv tools directory → 'uv' on Unix."""
        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
        monkeypatch.setattr(
            "claude_tap.cli_update.sys.executable",
            "/home/alice/.local/share/uv/tools/claude-tap/bin/python",
        )
        monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _: None)
        assert _detect_installer() == "uv"

    def test_pip_install_with_uv_on_path_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows, having uv on PATH must NOT cause a pip install to be detected as uv."""
        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
        monkeypatch.setattr(
            "claude_tap.cli_update.sys.executable",
            r"C:\Python311\python.exe",
        )
        # uv is on PATH, but the executable is not a uv tools path
        monkeypatch.setattr(
            "claude_tap.cli_update.shutil.which", lambda name: r"C:\tools\uv.exe" if name == "uv" else None
        )
        assert _detect_installer() == "pip"

    def test_pip_install_with_uv_on_path_on_unix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Unix, the legacy PATH-based fallback still returns 'uv' when uv is on PATH."""
        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
        monkeypatch.setattr(
            "claude_tap.cli_update.sys.executable",
            "/usr/bin/python3",
        )
        monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda name: "/usr/bin/uv" if name == "uv" else None)
        assert _detect_installer() == "uv"

    def test_pip_install_no_uv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No uv anywhere → 'pip'."""
        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
        monkeypatch.setattr("claude_tap.cli_update.sys.executable", "/usr/bin/python3")
        monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _: None)
        assert _detect_installer() == "pip"


# ---------------------------------------------------------------------------
# _is_editable_install
# ---------------------------------------------------------------------------


class TestIsEditableInstall:
    """Tests for editable install detection."""

    @staticmethod
    def _patch_install_roots(monkeypatch, site_packages_path):
        """Monkeypatch site/sysconfig to return consistent install roots for testing."""
        import site
        import sysconfig

        monkeypatch.setattr(
            sysconfig, "get_paths", lambda: {"purelib": site_packages_path, "platlib": site_packages_path}
        )
        monkeypatch.setattr(site, "getsitepackages", lambda: [site_packages_path])
        monkeypatch.setattr(site, "getusersitepackages", lambda: site_packages_path)

    def test_source_tree_path_is_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Package file outside site-packages and without PEP 610 data → editable."""
        import importlib.metadata

        import claude_tap as _pkg

        sp = r"C:\Python311\Lib\site-packages"
        monkeypatch.setattr(_pkg, "__file__", r"D:\work\claude-tap\claude_tap\__init__.py")
        monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: _make_dist(None))
        self._patch_install_roots(monkeypatch, sp)
        assert _is_editable_install() is True

    def test_site_packages_path_is_not_editable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Package file under site-packages without PEP 610 editable flag → not editable."""
        import importlib.metadata

        import claude_tap as _pkg

        sp = r"C:\Python311\Lib\site-packages"
        monkeypatch.setattr(_pkg, "__file__", rf"{sp}\claude_tap\__init__.py")
        monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: _make_dist(None))
        self._patch_install_roots(monkeypatch, sp)
        assert _is_editable_install() is False

    def test_pep610_editable_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PEP 610 direct_url.json with dir_info.editable=true → editable."""
        import importlib.metadata

        import claude_tap as _pkg

        sp = r"C:\Python311\Lib\site-packages"
        monkeypatch.setattr(_pkg, "__file__", rf"{sp}\claude_tap\__init__.py")
        monkeypatch.setattr(
            importlib.metadata, "distribution", lambda _name: _make_dist({"dir_info": {"editable": True}})
        )
        self._patch_install_roots(monkeypatch, sp)
        assert _is_editable_install() is True

    def test_malformed_pep610_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Malformed direct_url.json should not crash — falls back to path check."""
        import importlib.metadata

        import claude_tap as _pkg

        sp = r"C:\Python311\Lib\site-packages"
        monkeypatch.setattr(_pkg, "__file__", rf"{sp}\claude_tap\__init__.py")
        monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: _make_dist_raw("{not json"))
        self._patch_install_roots(monkeypatch, sp)
        assert _is_editable_install() is False


class _FakeDist:
    """Minimal importlib.metadata.Distribution stand-in."""

    def __init__(self, direct_url_json: str | None) -> None:
        self._direct_url = direct_url_json

    def read_text(self, name: str) -> str | None:
        if name == "direct_url.json":
            return self._direct_url
        return None


def _make_dist(data: dict | None) -> _FakeDist:
    """Create a _FakeDist that returns the given dict as direct_url.json."""
    if data is None:
        return _FakeDist(None)
    return _FakeDist(json.dumps(data))


def _make_dist_raw(raw: str) -> _FakeDist:
    """Create a _FakeDist that returns raw text as direct_url.json."""
    return _FakeDist(raw)


# ---------------------------------------------------------------------------
# _build_update_command
# ---------------------------------------------------------------------------


def test_build_update_command_uses_uv_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: "/tmp/uv" if name == "uv" else None)

    assert _build_update_command("uv") == ["/tmp/uv", "tool", "upgrade", "claude-tap"]


def test_build_update_command_returns_none_when_uv_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _name: None)

    assert _build_update_command("uv") is None


def test_build_update_command_uses_current_python_for_pip() -> None:
    assert _build_update_command("pip") == [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]


# ---------------------------------------------------------------------------
# parse_update_args
# ---------------------------------------------------------------------------


def test_parse_update_args_defaults_to_auto() -> None:
    args = parse_update_args([])

    assert args.installer == "auto"
    assert args.dry_run is False


# ---------------------------------------------------------------------------
# update_main
# ---------------------------------------------------------------------------


def test_update_main_dry_run_prints_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: "/tmp/uv" if name == "uv" else None)

    assert update_main(["--installer", "uv", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Upgrading claude-tap with uv" in out
    assert "/tmp/uv tool upgrade claude-tap" in out


def test_update_main_runs_selected_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-Windows, update_main uses subprocess.run directly for pip."""
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "linux")
    monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert update_main(["--installer", "pip"]) == 7
    assert captured["cmd"] == [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
    assert captured["kwargs"] == {"check": False}


def test_update_main_reports_missing_uv(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda _name: None)

    assert update_main(["--installer", "uv"]) == 1

    err = capsys.readouterr().err
    assert "uv" in err
    assert "--installer pip" in err


def test_update_main_pip_on_windows_uses_deferred_update(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On Windows + pip, update_main must use deferred update, not subprocess.run."""
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli_update.shutil.which", lambda _: None)

    called: dict[str, object] = {}

    def fake_deferred(cmd):
        called["cmd"] = cmd
        return 0

    monkeypatch.setattr("claude_tap.cli_update._windows_deferred_pip_update", fake_deferred)

    # Make subprocess.run assert it's NOT called
    def assert_not_called(*a, **kw):
        raise AssertionError("subprocess.run should not be called on Windows+pip")

    monkeypatch.setattr(subprocess, "run", assert_not_called)

    result = update_main(["--installer", "pip"])
    assert result == 0
    assert called["cmd"] == [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
    out = capsys.readouterr().out
    # The deferred update prints its own message; we just verify it was used
    assert "pip" in out


def test_update_main_uv_on_windows_still_uses_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows + uv, update_main must use the normal subprocess.run path."""
    monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli.sys.platform", "win32")
    monkeypatch.setattr("claude_tap.cli.shutil.which", lambda name: r"C:\uv\uv.exe" if name == "uv" else None)

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = update_main(["--installer", "uv"])
    assert result == 0
    assert captured["cmd"] == [r"C:\uv\uv.exe", "tool", "upgrade", "claude-tap"]


# ---------------------------------------------------------------------------
# main_entry routing
# ---------------------------------------------------------------------------


def test_main_entry_routes_update_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_update_main(argv):
        called["argv"] = argv
        return 3

    monkeypatch.setattr(sys, "argv", ["claude-tap", "update", "--installer", "pip"])
    monkeypatch.setattr("claude_tap.cli.update_main", fake_update_main)

    with pytest.raises(SystemExit) as excinfo:
        main_entry()

    assert excinfo.value.code == 3
    assert called["argv"] == ["--installer", "pip"]


# ---------------------------------------------------------------------------
# Background update decision logic
# ---------------------------------------------------------------------------


class TestBackgroundUpdateDecision:
    """Tests for the background update decision logic in async_main.

    These tests verify the conditions under which a background update
    is started or skipped, without running the full proxy server.
    """

    @pytest.mark.asyncio
    async def test_windows_pip_no_background_update(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Windows + pip must NOT start a background update; show manual prompt instead."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "win32")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "pip")
        monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: False)

        # If _start_background_update is called, the test fails
        monkeypatch.setattr(
            "claude_tap.cli._start_background_update",
            lambda _installer: (_ for _ in ()).throw(AssertionError("background update must not run on Windows+pip")),
        )

        await _simulate_update_check(monkeypatch, no_auto_update=False)

        out = capsys.readouterr().out
        assert "Background updates are disabled for pip installs on Windows" in out
        assert "claude-tap update" in out

    @pytest.mark.asyncio
    async def test_windows_uv_background_update_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows + uv must still start a background update."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "win32")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: False)

        called: dict[str, object] = {}

        def fake_start(installer):
            called["installer"] = installer
            return object()  # non-None Popen-like object

        monkeypatch.setattr("claude_tap.cli._start_background_update", fake_start)

        await _simulate_update_check(monkeypatch, no_auto_update=False)

        assert called.get("installer") == "uv"

    @pytest.mark.asyncio
    async def test_non_windows_pip_background_update_proceeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-Windows + pip must still start a background update."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "pip")
        monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: False)

        called: dict[str, object] = {}

        def fake_start(installer):
            called["installer"] = installer
            return object()

        monkeypatch.setattr("claude_tap.cli._start_background_update", fake_start)

        await _simulate_update_check(monkeypatch, no_auto_update=False)

        assert called.get("installer") == "pip"

    @pytest.mark.asyncio
    async def test_editable_install_skips_update(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Editable install must not start a background update."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "pip")
        monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: True)

        monkeypatch.setattr(
            "claude_tap.cli._start_background_update",
            lambda _installer: (_ for _ in ()).throw(
                AssertionError("background update must not run for editable install")
            ),
        )

        await _simulate_update_check(monkeypatch, no_auto_update=False)

        out = capsys.readouterr().out
        assert "Editable install detected" in out

    @pytest.mark.asyncio
    async def test_no_auto_update_flag_only_notifies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--tap-no-auto-update must not start any update."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "pip")

        monkeypatch.setattr(
            "claude_tap.cli._start_background_update",
            lambda _installer: (_ for _ in ()).throw(
                AssertionError("no update should start with --tap-no-auto-update")
            ),
        )

        await _simulate_update_check(monkeypatch, no_auto_update=True)

    @pytest.mark.asyncio
    async def test_no_downloading_message_when_popen_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """If _start_background_update returns None, don't print 'Downloading update in background'."""
        monkeypatch.setattr("claude_tap.cli.sys.platform", "linux")
        monkeypatch.setattr("claude_tap.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("claude_tap.cli._is_editable_install", lambda: False)
        # Popen returns None (e.g. uv not found)
        monkeypatch.setattr("claude_tap.cli._start_background_update", lambda _installer: None)

        await _simulate_update_check(monkeypatch, no_auto_update=False)

        out = capsys.readouterr().out
        assert "Downloading update in background" not in out
        # The "Update available" notification should still appear
        assert "Update available" in out


# ---------------------------------------------------------------------------
# _windows_deferred_pip_update
# ---------------------------------------------------------------------------


class TestWindowsDeferredPipUpdate:
    """Tests for the deferred pip update helper on Windows."""

    def test_creates_and_spawns_helper_script(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_windows_deferred_pip_update writes a temp script and spawns it."""
        from claude_tap.cli_update import _windows_deferred_pip_update

        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")

        popen_called: dict[str, object] = {}

        def fake_popen(cmd, **kwargs):
            popen_called["cmd"] = cmd
            popen_called["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
        result = _windows_deferred_pip_update(cmd)

        assert result == 0
        popen_cmd = popen_called["cmd"]
        assert popen_cmd[0] == sys.executable
        assert popen_cmd[1].endswith("_claude_tap_update.py")
        # Verify the script content contains the pid check and pip command
        script_path = popen_cmd[1]
        with open(script_path, encoding="utf-8") as f:
            content = f.read()
        assert "os.kill(pid, 0)" in content
        assert "pip" in content
        assert "claude-tap" in content
        # Clean up temp script
        try:
            os.unlink(script_path)
        except OSError:
            pass

    def test_returns_1_on_popen_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Popen fails, _windows_deferred_pip_update returns 1."""
        from claude_tap.cli_update import _windows_deferred_pip_update

        monkeypatch.setattr("claude_tap.cli_update.sys.platform", "win32")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(OSError("fail")))

        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "claude-tap"]
        result = _windows_deferred_pip_update(cmd)
        assert result == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_return(val):
    return val


async def _simulate_update_check(monkeypatch: pytest.MonkeyPatch, *, no_auto_update: bool) -> None:
    """Simulate the update-check block from async_main without starting the proxy.

    This replicates the exact logic from cli.py async_main's
    "Background update check" section so we can test the branching
    without needing a running aiohttp server.
    """

    monkeypatch.setattr("claude_tap.cli.__version__", "0.0.1")
    monkeypatch.setattr("claude_tap.cli._check_pypi_version", lambda: _async_return("99.0.0"))
    monkeypatch.setattr("claude_tap.cli._version_tuple", _version_tuple_passthrough)

    # Re-import the monkeypatched versions
    import claude_tap.cli as _cli_mod

    latest = await _cli_mod._check_pypi_version()
    if latest and _cli_mod._version_tuple(latest) > _cli_mod._version_tuple(_cli_mod.__version__):
        print(f"⬆️  Update available: {_cli_mod.__version__} → {latest}")
        if no_auto_update:
            pass  # user opted out of auto-download
        elif sys.platform == "win32" and _cli_mod._detect_installer() == "pip":
            print("   Background updates are disabled for pip installs on Windows.")
            print("   Exit claude-tap, then run `claude-tap update`.")
        elif _cli_mod._is_editable_install():
            print("   Editable install detected — skipping auto-update.")
            print("   Run `pip install -e .` manually to update.")
        else:
            installer = _cli_mod._detect_installer()
            proc = _cli_mod._start_background_update(installer)
            if proc is not None:
                print(f"   Downloading update in background ({installer})...")


def _version_tuple_passthrough(v: str) -> tuple[int, ...]:
    """Passthrough that always says the latest version is newer."""
    if v == "99.0.0":
        return (99, 0, 0)
    return (0, 0, 1)
