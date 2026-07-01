"""Isolated E2E for the macOS monitor safety chain (PR #335).

Exercises the real ``claude_tap.global_inject`` code and the real
``claude-tap monitor-restore`` CLI entrypoint against a throwaway ``$HOME`` so
the developer's real ``~/.claude`` / ``~/.codex`` are never touched.

Covers the automatable portion of the maintainer's requested E2E:
  3. verify ~/.claude/settings.json and ~/.codex/config.toml are injected
  5. Stop Monitor -> verify both files restore byte-for-byte
  6. force-quit while active -> ``monitor-restore`` recovers files AND processes

Steps 1/2 (build + launch the .app from Finder, click Start Monitor) and step 4
(fresh Claude/Codex sessions captured by the dashboard) require a GUI macOS run
and are out of scope for this headless harness.

Run: uv run python .agents/evidence/pr/335/e2e_safety_chain.py
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLAUDE_PORT = 45871
CODEX_PORT = 45872

CLAUDE_SETTINGS = """\
{
  "model": "claude-sonnet-4",
  "env": {
    "SOME_EXISTING": "keep-me"
  }
}
"""

# Custom Codex provider selected -> exercises the provider base_url rewrite path.
CODEX_CONFIG = """\
model = "gpt-5"
model_provider = "myco"
openai_base_url = "https://api.example.com/v1"

[model_providers.myco]
name = "MyCo"
base_url = "https://api.example.com/v1"
wire_api = "responses"
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


results: list[tuple[bool, str]] = []


def check(cond: bool, msg: str) -> None:
    results.append((bool(cond), msg))
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="claude-tap-e2e-"))
    os.environ["HOME"] = str(tmp)
    os.environ["CODEX_HOME"] = str(tmp / ".codex")

    # Real code under test, imported after $HOME is redirected.
    from claude_tap import global_inject as gi

    claude_path = tmp / ".claude" / "settings.json"
    codex_path = tmp / ".codex" / "config.toml"
    claude_path.parent.mkdir(parents=True)
    codex_path.parent.mkdir(parents=True)
    claude_path.write_text(CLAUDE_SETTINGS)
    codex_path.write_text(CODEX_CONFIG)
    claude_path.chmod(0o600)
    codex_path.chmod(0o600)

    orig = {p: (sha(p), mode(p)) for p in (claude_path, codex_path)}
    print(f"HOME={tmp}")
    print(f"claude sha={orig[claude_path][0][:16]}… mode={oct(orig[claude_path][1])}")
    print(f"codex  sha={orig[codex_path][0][:16]}… mode={oct(orig[codex_path][1])}")

    # -- Step 3: inject ------------------------------------------------------
    print("\n== Step 3: Start Monitor -> inject base URLs ==")
    gi.enable(claude_port=CLAUDE_PORT, codex_port=CODEX_PORT)
    claude_txt = claude_path.read_text()
    codex_txt = codex_path.read_text()
    check(gi.is_active(), "monitor state is active after enable()")
    check(mode(gi._state_file()) == 0o600, "monitor-state.json written with 0o600")
    check(f"127.0.0.1:{CLAUDE_PORT}" in claude_txt, "claude settings.json points at local proxy")
    check('"SOME_EXISTING": "keep-me"' in claude_txt, "pre-existing claude env preserved")
    check(f"127.0.0.1:{CODEX_PORT}" in codex_txt, "codex openai_base_url points at local proxy")
    check(
        codex_txt.count(f"http://127.0.0.1:{CODEX_PORT}/v1") >= 2,
        "codex custom provider base_url ALSO rerouted (not just legacy key)",
    )
    for p in (claude_path, codex_path):
        b = p.with_name(p.name + ".tap-backup")
        check(b.exists() and mode(b) == orig[p][1], f"{p.name} backup exists with original 0o600 perms")

    # -- Step 5: stop -> byte-for-byte restore -------------------------------
    print("\n== Step 5: Stop Monitor -> restore ==")
    gi.disable()
    check(not gi.is_active(), "monitor state cleared after disable()")
    for p in (claude_path, codex_path):
        check(sha(p) == orig[p][0], f"{p.name} restored BYTE-FOR-BYTE")
        check(mode(p) == orig[p][1], f"{p.name} restored with original perms")
        check(not p.with_name(p.name + ".tap-backup").exists(), f"{p.name} backup removed")

    # -- Step 6: force-quit while active -> monitor-restore recovers ---------
    print("\n== Step 6: force-quit while active -> claude-tap monitor-restore ==")
    orphan = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", "claude_tap", "--tap-no-launch"]
    )
    time.sleep(0.3)
    # Enable with the orphan recorded, then simulate a force-quit by NOT calling
    # disable(): injected config + monitor-state remain on disk, app is gone.
    gi.enable(
        claude_port=CLAUDE_PORT,
        codex_port=CODEX_PORT,
        processes=[{"pid": orphan.pid, "command": "claude_tap --tap-no-launch"}],
    )
    check(gi.is_active(), "state present after simulated force-quit (injection left behind)")
    check(f"127.0.0.1:{CLAUDE_PORT}" in claude_path.read_text(), "config still injected pre-restore")

    # Real recovery command: `claude-tap monitor-restore`.
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv=['claude-tap','monitor-restore']; "
         "from claude_tap.cli import main_entry; main_entry()"],
        env={**os.environ, "HOME": str(tmp), "CODEX_HOME": str(tmp / ".codex")},
        capture_output=True,
        text=True,
    )
    check(proc.returncode == 0, f"monitor-restore exited 0 (got {proc.returncode})")
    check(not gi.is_active(), "monitor state cleared by monitor-restore")
    for p in (claude_path, codex_path):
        check(sha(p) == orig[p][0], f"{p.name} restored BYTE-FOR-BYTE by monitor-restore")

    # Orphan proxy must be killed (it "looks like" a monitor process).
    time.sleep(0.3)
    alive = orphan.poll() is None
    if alive:
        orphan.kill()
    check(not alive, "orphaned monitor proxy terminated by monitor-restore")

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n==== {passed}/{total} checks passed ====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
