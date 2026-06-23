"""Toggle global Claude/Codex interception by editing their config files.

``enable`` points *newly launched* Claude Code and Codex CLI sessions at the
local claude-tap reverse proxies by writing the base-URL keys into
``~/.claude/settings.json`` and ``~/.codex/config.toml``. ``disable`` restores
the originals byte-for-byte from backups taken at enable time.

Reverse-proxy interception needs no CA cert, so this is just a base-URL edit.
Already-running sessions are unaffected (these configs are read at launch).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from claude_tap.cli_clients import CLIENT_CONFIGS, _codex_selected_provider_base_url_key

_BACKUP_SUFFIX = ".tap-backup"


def _state_file() -> Path:
    return Path.home() / ".claude-tap" / "monitor-state.json"


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def claude_home_exists() -> bool:
    return _claude_settings_path().parent.is_dir()


def codex_home_exists() -> bool:
    return _codex_config_path().parent.is_dir()


def is_active() -> bool:
    """True if interception config is currently injected."""
    return _state_file().exists()


def enable(
    *,
    claude_port: int | None = None,
    codex_port: int | None = None,
    processes: list[dict[str, object]] | None = None,
) -> None:
    """Inject reverse-proxy base URLs for the given clients.

    Passing ``None`` for a port skips that client. Any previously-injected state
    is restored first so backups always capture the user's true originals.
    """
    if is_active():
        disable()

    files: list[dict[str, object]] = []
    state_file = _state_file()
    try:
        if claude_port is not None:
            _inject_claude(_claude_settings_path(), claude_port, files)
        if codex_port is not None:
            _inject_codex(_codex_config_path(), codex_port, files)

        state_file.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(
            state_file,
            json.dumps({"files": files, "processes": processes or []}, indent=2) + "\n",
            mode=0o600,
        )
    except Exception:
        _restore_files(files)
        state_file.unlink(missing_ok=True)
        raise


def disable(*, terminate_processes: bool = False) -> None:
    """Restore every file injected by ``enable`` and clear the state file."""
    state_file = _state_file()
    if not state_file.exists():
        return
    state: object = {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        entries = state.get("files", []) if isinstance(state, dict) else []
    except (OSError, json.JSONDecodeError):
        entries = []
    processes = state.get("processes", []) if isinstance(state, dict) else []

    _restore_files(entries)
    if terminate_processes and isinstance(processes, list):
        _terminate_recorded_processes(processes)

    state_file.unlink(missing_ok=True)


def _restore_files(entries: list[object]) -> None:
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = Path(str(entry.get("path", "")))
        if entry.get("existed"):
            backup = entry.get("backup")
            backup_path = Path(str(backup)) if backup else None
            if backup_path and backup_path.exists():
                path.write_bytes(backup_path.read_bytes())
                backup_path.unlink()
        elif path.exists():
            path.unlink()


def _record_backup(path: Path, files: list[dict[str, object]]) -> bool:
    """Back up ``path`` if it exists, append a restore record, return existed."""
    existed = path.exists()
    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if existed:
        shutil.copy2(path, backup)
    files.append({"path": str(path), "existed": existed, "backup": str(backup) if existed else None})
    return existed


def _inject_claude(path: Path, port: int, files: list[dict[str, object]]) -> None:
    existed = _record_backup(path, files)
    data: object = {}
    if existed:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    env.update(CLIENT_CONFIGS["claude"].reverse_base_url_env_map(port))
    data["env"] = env
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, json.dumps(data, indent=2) + "\n", mode=_write_mode(path, existed))


def _inject_codex(path: Path, port: int, files: list[dict[str, object]]) -> None:
    existed = _record_backup(path, files)
    text = path.read_text(encoding="utf-8") if existed else ""
    new_text = _set_toml_top_level_string(text, "openai_base_url", f"http://127.0.0.1:{port}/v1")
    provider_key = _codex_selected_provider_base_url_key()
    if provider_key:
        new_text = _set_toml_dotted_string(new_text, provider_key, f"http://127.0.0.1:{port}/v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, new_text, mode=_write_mode(path, existed))


def _set_toml_top_level_string(text: str, key: str, value: str) -> str:
    """Set a top-level string ``key`` in TOML text, preserving the rest.

    Replaces an existing top-level assignment, or inserts one before the first
    table header (``[...]``). Top-level keys must precede any table section.
    """
    new_line = f'{key} = "{value}"'
    lines = text.splitlines()

    header_idx = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), None)
    region_end = header_idx if header_idx is not None else len(lines)

    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i in range(region_end):
        if key_re.match(lines[i]):
            lines[i] = new_line
            return "\n".join(lines) + "\n"

    lines.insert(region_end, new_line)
    result = "\n".join(lines)
    return result if result.endswith("\n") else result + "\n"


def _set_toml_dotted_string(text: str, dotted_key: str, value: str) -> str:
    table, key = dotted_key.rsplit(".", 1)
    lines = text.splitlines()
    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")

    table_start: int | None = None
    table_end = len(lines)
    for i, line in enumerate(lines):
        match = header_re.match(line)
        if not match:
            continue
        if table_start is not None:
            table_end = i
            break
        if match.group(1).strip() == table:
            table_start = i

    new_line = f'{key} = "{value}"'
    if table_start is None:
        return _set_toml_top_level_string(text, dotted_key, value)

    for i in range(table_start + 1, table_end):
        if key_re.match(lines[i]):
            lines[i] = new_line
            return "\n".join(lines) + "\n"
    lines.insert(table_end, new_line)
    return "\n".join(lines) + "\n"


def _write_mode(path: Path, existed: bool) -> int:
    return path.stat().st_mode & 0o777 if existed and path.exists() else 0o600


def _write_text_atomic(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _terminate_recorded_processes(processes: list[object]) -> None:
    for entry in processes:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            continue
        command = _monitor_process_command(pid)
        if not _looks_like_monitor_process(command):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not _pid_exists(pid):
                break
            time.sleep(0.05)
        if _pid_exists(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _monitor_process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _looks_like_monitor_process(command: str) -> bool:
    if not command:
        return False
    has_claude_tap = "claude_tap" in command or "claude-tap" in command
    has_monitor_role = " dashboard" in command or "--tap-no-launch" in command
    return has_claude_tap and has_monitor_role


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
