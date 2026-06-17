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
from pathlib import Path

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


def enable(*, claude_port: int | None = None, codex_port: int | None = None) -> None:
    """Inject reverse-proxy base URLs for the given clients.

    Passing ``None`` for a port skips that client. Any previously-injected state
    is restored first so backups always capture the user's true originals.
    """
    if is_active():
        disable()

    files: list[dict[str, object]] = []
    if claude_port is not None:
        _inject_claude(_claude_settings_path(), claude_port, files)
    if codex_port is not None:
        _inject_codex(_codex_config_path(), codex_port, files)

    state_file = _state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"files": files}, indent=2) + "\n", encoding="utf-8")


def disable() -> None:
    """Restore every file injected by ``enable`` and clear the state file."""
    state_file = _state_file()
    if not state_file.exists():
        return
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        entries = state.get("files", []) if isinstance(state, dict) else []
    except (OSError, json.JSONDecodeError):
        entries = []

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

    state_file.unlink(missing_ok=True)


def _record_backup(path: Path, files: list[dict[str, object]]) -> bool:
    """Back up ``path`` if it exists, append a restore record, return existed."""
    existed = path.exists()
    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if existed:
        backup.write_bytes(path.read_bytes())
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
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    data["env"] = env
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _inject_codex(path: Path, port: int, files: list[dict[str, object]]) -> None:
    existed = _record_backup(path, files)
    text = path.read_text(encoding="utf-8") if existed else ""
    new_text = _set_toml_top_level_string(text, "openai_base_url", f"http://127.0.0.1:{port}/v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")


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
