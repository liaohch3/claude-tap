"""macOS menu bar app for claude-tap."""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_tap import global_inject
from claude_tap.dashboard import list_trace_sessions
from claude_tap.shared_dashboard import _sync_dashboard_healthy_for_current_db as _dashboard_is_healthy
from claude_tap.shared_dashboard import dashboard_url, resolve_dashboard_port

_NS_VARIABLE_STATUS_ITEM_LENGTH = -1.0
_NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1
_NS_IMAGE_LEFT = 2
_NS_ALERT_FIRST_BUTTON_RETURN = 1000
# Absolute default so the app works when launched from Finder (cwd is "/").
_DEFAULT_OUTPUT_DIR = Path.home() / ".claude-tap" / "traces"
_CALLBACKS: list[Any] = []
_ACTIVE_APP: MacOSMenuApp | None = None


def build_dashboard_command(
    *,
    python_executable: str,
    host: str,
    port: int,
    output_dir: Path,
) -> list[str]:
    """Build the subprocess command used by the menu bar monitor."""
    cmd = _claude_tap_command(
        python_executable,
        "dashboard",
        "--tap-live-port",
        str(port),
        "--tap-no-open",
        "--tap-output-dir",
        str(output_dir),
    )
    if host != "127.0.0.1":
        cmd.extend(["--tap-host", host])
    return cmd


def build_proxy_command(
    *,
    python_executable: str,
    client: str,
    host: str,
    port: int,
    output_dir: Path,
) -> list[str]:
    """Build a standalone reverse-proxy command for a globally routed client."""
    return _claude_tap_command(
        python_executable,
        "--tap-no-launch",
        "--tap-client",
        client,
        "--tap-port",
        str(port),
        "--tap-host",
        host,
        "--tap-no-live",
        "--tap-output-dir",
        str(output_dir),
    )


def _claude_tap_command(python_executable: str, *args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [python_executable, *args]
    return [python_executable, "-m", "claude_tap", *args]


class DashboardMonitorController:
    """Own the dashboard subprocess launched from the menu bar app."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        output_dir: Path,
        claude_proxy_port: int | None = None,
        codex_proxy_port: int | None = None,
        python_executable: str = sys.executable,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        is_healthy: Callable[[str, int], bool] = _dashboard_is_healthy,
        open_browser: Callable[[str], object] = webbrowser.open,
        enable_injection: Callable[..., None] = global_inject.enable,
        disable_injection: Callable[[], None] = global_inject.disable,
        injection_is_active: Callable[[], bool] = global_inject.is_active,
        startup_check_delay: float = 0.15,
        sleep: Callable[[float], object] = time.sleep,
    ) -> None:
        self.host = host
        self.port = port
        self.output_dir = output_dir
        self.claude_proxy_port = claude_proxy_port or port + 1
        self.codex_proxy_port = codex_proxy_port or port + 2
        self.python_executable = python_executable
        self._popen = popen
        self._is_healthy = is_healthy
        self._open_browser = open_browser
        self._enable_injection = enable_injection
        self._disable_injection = disable_injection
        self._injection_is_active = injection_is_active
        self._startup_check_delay = startup_check_delay
        self._sleep = sleep
        self._process: subprocess.Popen[bytes] | None = None
        self._proxy_processes: list[subprocess.Popen[bytes]] = []
        self._proxy_process_names: list[str] = []

    @property
    def url(self) -> str:
        return dashboard_url(self.host, self.port)

    def start(self) -> str:
        if self._monitor_is_running():
            return self.url

        try:
            if self._injection_is_active():
                self._disable_injection()
            if not self._is_healthy(self.host, self.port):
                cmd = build_dashboard_command(
                    python_executable=self.python_executable,
                    host=self.host,
                    port=self.port,
                    output_dir=self.output_dir,
                )
                self._process = self._popen(cmd, **self._subprocess_kwargs())
            self._start_proxy("claude", self.claude_proxy_port)
            self._start_proxy("codex", self.codex_proxy_port)
            self._verify_started_processes()
            self._enable_injection(
                claude_port=self.claude_proxy_port,
                codex_port=self.codex_proxy_port,
                processes=self._monitor_process_records(),
            )
        except Exception:
            self._terminate_owned_processes()
            self._disable_injection()
            raise
        return self.url

    def stop(self) -> bool:
        was_running = self._process_is_running() or self._proxy_processes_are_running() or self._injection_is_active()
        if not was_running:
            self._process = None
            self._proxy_processes = []
            self._proxy_process_names = []
            return False

        self._disable_injection()
        self._terminate_owned_processes()
        return True

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)

    def open_dashboard(self) -> None:
        self.start()
        self._open_browser(self.url)

    def is_running(self) -> bool:
        return self._monitor_is_running()

    def can_stop(self) -> bool:
        return self._process_is_running() or self._proxy_processes_are_running() or self._injection_is_active()

    def _process_is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _monitor_is_running(self) -> bool:
        return self._dashboard_is_running() and self._proxy_processes_are_running() and self._injection_is_active()

    def _dashboard_is_running(self) -> bool:
        return self._process_is_running() or self._is_healthy(self.host, self.port)

    def _proxy_processes_are_running(self) -> bool:
        return bool(self._proxy_processes) and all(process.poll() is None for process in self._proxy_processes)

    def _start_proxy(self, client: str, port: int) -> None:
        cmd = build_proxy_command(
            python_executable=self.python_executable,
            client=client,
            host=self.host,
            port=port,
            output_dir=self.output_dir,
        )
        self._proxy_processes.append(self._popen(cmd, **self._subprocess_kwargs()))
        self._proxy_process_names.append(client)

    def _monitor_process_records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        if self._process is not None and self._process.poll() is None:
            pid = getattr(self._process, "pid", None)
            if isinstance(pid, int):
                records.append({"pid": pid, "role": "dashboard"})
        for name, process in zip(self._proxy_process_names, self._proxy_processes, strict=True):
            if process.poll() is not None:
                continue
            pid = getattr(process, "pid", None)
            if isinstance(pid, int):
                records.append({"pid": pid, "role": f"{name} proxy"})
        return records

    def _verify_started_processes(self) -> None:
        if self._startup_check_delay > 0:
            self._sleep(self._startup_check_delay)

        exited: list[str] = []
        if self._process is not None and self._process.poll() is not None:
            exited.append(f"dashboard exited with code {self._process.returncode}")
        for name, process in zip(self._proxy_process_names, self._proxy_processes, strict=True):
            if process.poll() is not None:
                exited.append(f"{name} proxy exited with code {process.returncode}")
        if exited:
            raise RuntimeError("Monitor failed to start: " + "; ".join(exited))

    def _terminate_owned_processes(self) -> None:
        processes = [process for process in [self._process, *self._proxy_processes] if process is not None]
        for process in processes:
            if process.poll() is None:
                self._terminate_process(process)
        self._process = None
        self._proxy_processes = []
        self._proxy_process_names = []

    @staticmethod
    def _subprocess_kwargs() -> dict[str, object]:
        kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            from claude_tap.process_utils import windows_no_console_subprocess_kwargs

            kwargs.update(windows_no_console_subprocess_kwargs())
        else:
            kwargs["start_new_session"] = True
        return kwargs


class MacOSMenuApp:
    """Native macOS status item backed by Objective-C runtime calls."""

    def __init__(self, controller: DashboardMonitorController, *, auto_start: bool = True) -> None:
        self.controller = controller
        self.auto_start = auto_start
        self._objc = _ObjC()
        self._app = 0
        self._status_item = 0
        self._target = 0
        self._status_item_view = 0
        self._session_item = 0
        self._latest_item = 0
        self._start_item = 0
        self._stop_item = 0

    def run(self) -> int:
        global _ACTIVE_APP

        _ACTIVE_APP = self
        objc = self._objc
        pool = objc.alloc_init("NSAutoreleasePool")
        self._app = objc.msg(objc.cls("NSApplication"), "sharedApplication")
        objc.msg(
            self._app,
            "setActivationPolicy:",
            None,
            [ctypes.c_long],
            _NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY,
        )
        self._build_menu()
        if self.auto_start:
            self.start_monitor()
        else:
            self.refresh_menu()
        objc.msg(self._app, "run")
        if pool:
            objc.msg(pool, "drain")
        return 0

    def start_monitor(self) -> None:
        if not self._confirm_start_monitor():
            self.refresh_menu()
            return
        try:
            self.controller.start()
        except Exception as exc:
            self.refresh_menu()
            self._show_error("Unable to start Claude Tap monitor", _exception_text(exc))
            return
        self.refresh_menu()

    def stop_monitor(self) -> None:
        self.controller.stop()
        self.refresh_menu()

    def open_dashboard(self) -> None:
        self.controller.open_dashboard()
        self.refresh_menu()

    def quit(self) -> None:
        self.controller.stop()
        self._objc.msg(self._app, "terminate:", None, [ctypes.c_void_p], None)

    def refresh_menu(self) -> None:
        running = self.controller.is_running()
        sessions = _menu_sessions()
        latest = sessions[0] if sessions else None
        latest_text = _latest_session_text(latest)

        self._set_item_title(self._status_item_view, f"Monitor: {'Running' if running else 'Stopped'}")
        self._set_item_title(self._session_item, f"Sessions: {len(sessions)}")
        self._set_item_title(self._latest_item, latest_text)
        self._set_enabled(self._start_item, not running)
        self._set_enabled(self._stop_item, self.controller.can_stop())

    def _build_menu(self) -> None:
        objc = self._objc
        menu = objc.alloc_init("NSMenu")
        status_bar = objc.msg(objc.cls("NSStatusBar"), "systemStatusBar")
        self._status_item = objc.msg(
            status_bar,
            "statusItemWithLength:",
            ctypes.c_void_p,
            [ctypes.c_double],
            _NS_VARIABLE_STATUS_ITEM_LENGTH,
        )
        button = objc.msg(self._status_item, "button")
        self._configure_status_button(button)

        self._target = _new_menu_target(objc)
        self._status_item_view = self._add_menu_item(menu, "Monitor: Stopped", None, enabled=False)
        self._session_item = self._add_menu_item(menu, "Sessions: 0", None, enabled=False)
        self._latest_item = self._add_menu_item(menu, "Latest: No traces yet", None, enabled=False)
        self._add_separator(menu)
        self._add_menu_item(menu, "Open Dashboard", "openDashboard:")
        self._start_item = self._add_menu_item(menu, "Start Monitor", "startMonitor:")
        self._stop_item = self._add_menu_item(menu, "Stop Monitor", "stopMonitor:")
        self._add_separator(menu)
        self._add_menu_item(menu, "Refresh", "refreshMenu:")
        self._add_menu_item(menu, "Quit Claude Tap", "quit:")
        objc.msg(self._status_item, "setMenu:", None, [ctypes.c_void_p], menu)

    def _configure_status_button(self, button: int) -> None:
        objc = self._objc
        image = objc.msg(
            objc.cls("NSImage"),
            "imageWithSystemSymbolName:accessibilityDescription:",
            ctypes.c_void_p,
            [ctypes.c_void_p, ctypes.c_void_p],
            objc.nsstring("sparkles"),
            objc.nsstring("Claude Tap"),
        )
        if image:
            objc.msg(image, "setTemplate:", None, [ctypes.c_bool], True)
            objc.msg(button, "setImage:", None, [ctypes.c_void_p], image)
            objc.msg(button, "setImagePosition:", None, [ctypes.c_ulong], _NS_IMAGE_LEFT)
        objc.msg(button, "setTitle:", None, [ctypes.c_void_p], objc.nsstring("Claude"))
        objc.msg(button, "setToolTip:", None, [ctypes.c_void_p], objc.nsstring("Claude Tap"))

    def _add_menu_item(self, menu: int, title: str, action: str | None, *, enabled: bool = True) -> int:
        objc = self._objc
        selector = objc.sel(action) if action else None
        item = objc.msg(objc.cls("NSMenuItem"), "alloc")
        item = objc.msg(
            item,
            "initWithTitle:action:keyEquivalent:",
            ctypes.c_void_p,
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p],
            objc.nsstring(title),
            selector,
            objc.nsstring(""),
        )
        if action:
            objc.msg(item, "setTarget:", None, [ctypes.c_void_p], self._target)
        objc.msg(item, "setEnabled:", None, [ctypes.c_bool], enabled)
        objc.msg(menu, "addItem:", None, [ctypes.c_void_p], item)
        return item

    def _add_separator(self, menu: int) -> None:
        objc = self._objc
        item = objc.msg(objc.cls("NSMenuItem"), "separatorItem")
        objc.msg(menu, "addItem:", None, [ctypes.c_void_p], item)

    def _set_item_title(self, item: int, title: str) -> None:
        self._objc.msg(item, "setTitle:", None, [ctypes.c_void_p], self._objc.nsstring(title))

    def _set_enabled(self, item: int, enabled: bool) -> None:
        self._objc.msg(item, "setEnabled:", None, [ctypes.c_bool], enabled)

    def _show_error(self, message: str, details: str) -> None:
        objc = self._objc
        app = objc.msg(objc.cls("NSApplication"), "sharedApplication")
        objc.msg(app, "activateIgnoringOtherApps:", None, [ctypes.c_bool], True)
        alert = objc.alloc_init("NSAlert")
        objc.msg(alert, "setMessageText:", None, [ctypes.c_void_p], objc.nsstring(message))
        objc.msg(alert, "setInformativeText:", None, [ctypes.c_void_p], objc.nsstring(details))
        objc.msg(alert, "addButtonWithTitle:", None, [ctypes.c_void_p], objc.nsstring("OK"))
        objc.msg(alert, "runModal", ctypes.c_long)

    def _confirm_start_monitor(self) -> bool:
        objc = self._objc
        app = objc.msg(objc.cls("NSApplication"), "sharedApplication")
        objc.msg(app, "activateIgnoringOtherApps:", None, [ctypes.c_bool], True)
        alert = objc.alloc_init("NSAlert")
        objc.msg(alert, "setMessageText:", None, [ctypes.c_void_p], objc.nsstring("Start Claude Tap Monitor?"))
        objc.msg(
            alert,
            "setInformativeText:",
            None,
            [ctypes.c_void_p],
            objc.nsstring(
                "This starts local dashboard/proxy processes and temporarily writes base URL settings to "
                "~/.claude/settings.json and ~/.codex/config.toml. Stop Monitor restores the files; "
                "claude-tap monitor-restore recovers them after a force quit."
            ),
        )
        objc.msg(alert, "addButtonWithTitle:", None, [ctypes.c_void_p], objc.nsstring("Start Monitor"))
        objc.msg(alert, "addButtonWithTitle:", None, [ctypes.c_void_p], objc.nsstring("Cancel"))
        return objc.msg(alert, "runModal", ctypes.c_long) == _NS_ALERT_FIRST_BUTTON_RETURN


class _ObjC:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("The claude-tap macOS menu bar app only runs on macOS.")
        self.objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        ctypes.CDLL("/System/Library/Frameworks/Foundation.framework/Foundation")
        ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.objc.objc_getClass.restype = ctypes.c_void_p
        self.objc.objc_getClass.argtypes = [ctypes.c_char_p]
        self.objc.sel_registerName.restype = ctypes.c_void_p
        self.objc.sel_registerName.argtypes = [ctypes.c_char_p]
        self.objc.objc_allocateClassPair.restype = ctypes.c_void_p
        self.objc.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        self.objc.class_addMethod.restype = ctypes.c_bool
        self.objc.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        self.objc.objc_registerClassPair.restype = None
        self.objc.objc_registerClassPair.argtypes = [ctypes.c_void_p]

    def cls(self, name: str) -> int:
        value = self.objc.objc_getClass(name.encode("utf-8"))
        if not value:
            raise RuntimeError(f"Objective-C class not found: {name}")
        return value

    def sel(self, name: str | None) -> int | None:
        if name is None:
            return None
        return self.objc.sel_registerName(name.encode("utf-8"))

    def msg(
        self,
        receiver: int,
        selector: str,
        restype: Any = ctypes.c_void_p,
        argtypes: list[Any] | None = None,
        *args: object,
    ) -> Any:
        send = self.objc.objc_msgSend
        send.restype = restype
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *(argtypes or [])]
        return send(receiver, self.sel(selector), *args)

    def nsstring(self, value: str) -> int:
        return self.msg(
            self.cls("NSString"),
            "stringWithUTF8String:",
            ctypes.c_void_p,
            [ctypes.c_char_p],
            value.encode("utf-8"),
        )

    def alloc_init(self, class_name: str) -> int:
        allocated = self.msg(self.cls(class_name), "alloc")
        return self.msg(allocated, "init")


def _new_menu_target(objc: _ObjC) -> int:
    class_name = "ClaudeTapMenuTarget"
    target_class = objc.objc.objc_getClass(class_name.encode("utf-8"))
    if not target_class:
        target_class = objc.objc.objc_allocateClassPair(objc.cls("NSObject"), class_name.encode("utf-8"), 0)
        for selector, callback in {
            "startMonitor:": _start_monitor_callback,
            "stopMonitor:": _stop_monitor_callback,
            "openDashboard:": _open_dashboard_callback,
            "refreshMenu:": _refresh_menu_callback,
            "quit:": _quit_callback,
        }.items():
            objc.objc.class_addMethod(
                target_class,
                objc.sel(selector),
                ctypes.cast(callback, ctypes.c_void_p),
                b"v@:@",
            )
        objc.objc.objc_registerClassPair(target_class)
    target = objc.msg(target_class, "alloc")
    return objc.msg(target, "init")


def _callback(fn: Callable[[int, int, int], None]) -> Any:
    callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    wrapped = callback_type(fn)
    _CALLBACKS.append(wrapped)
    return wrapped


@_callback
def _start_monitor_callback(_self: int, _cmd: int, _sender: int) -> None:
    if _ACTIVE_APP is not None:
        _ACTIVE_APP.start_monitor()


@_callback
def _stop_monitor_callback(_self: int, _cmd: int, _sender: int) -> None:
    if _ACTIVE_APP is not None:
        _ACTIVE_APP.stop_monitor()


@_callback
def _open_dashboard_callback(_self: int, _cmd: int, _sender: int) -> None:
    if _ACTIVE_APP is not None:
        _ACTIVE_APP.open_dashboard()


@_callback
def _refresh_menu_callback(_self: int, _cmd: int, _sender: int) -> None:
    if _ACTIVE_APP is not None:
        _ACTIVE_APP.refresh_menu()


@_callback
def _quit_callback(_self: int, _cmd: int, _sender: int) -> None:
    if _ACTIVE_APP is not None:
        _ACTIVE_APP.quit()


def _menu_sessions() -> list[dict[str, Any]]:
    try:
        return list_trace_sessions()
    except Exception:
        return []


def _latest_session_text(session: dict[str, Any] | None) -> str:
    if not session:
        return "Latest: No traces yet"
    agent = str(session.get("agent") or "Unknown")
    record_count = int(session.get("record_count") or 0)
    first_user = str(session.get("first_user") or "").strip()
    if len(first_user) > 44:
        first_user = first_user[:41].rstrip() + "..."
    suffix = f" - {first_user}" if first_user else ""
    return f"Latest: {agent} ({record_count}){suffix}"


def _exception_text(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def parse_macos_app_args(argv: list[str] | None = None) -> argparse.Namespace:
    if argv is not None:
        argv = [arg for arg in argv if not arg.startswith("-psn_")]
    parser = argparse.ArgumentParser(
        prog="claude-tap macos-app",
        description="Run the claude-tap macOS menu bar app.",
    )
    parser.add_argument("--tap-output-dir", default=str(_DEFAULT_OUTPUT_DIR), dest="output_dir")
    parser.add_argument("--tap-live-port", type=int, default=0, dest="live_port")
    parser.add_argument("--tap-host", default="127.0.0.1", dest="host")
    parser.add_argument("--tap-no-auto-start", action="store_false", default=True, dest="auto_start")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("claude-tap macos-app is only supported on macOS.", file=sys.stderr)
        return 1

    try:
        args = parse_macos_app_args(argv)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        controller = DashboardMonitorController(
            host=args.host,
            port=resolve_dashboard_port(args.live_port),
            output_dir=output_dir,
        )
        return MacOSMenuApp(controller, auto_start=args.auto_start).run()
    except Exception:
        _write_crash_log()
        raise


def _write_crash_log() -> None:
    import traceback

    log_path = Path.home() / "Library" / "Logs" / "claude-tap-macos.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
    except OSError:
        pass
