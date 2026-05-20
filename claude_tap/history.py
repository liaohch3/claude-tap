"""Trace history manifest and retention helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Iterable

_MANIFEST_FILE = ".cloudtap-manifest.json"


def _current_version() -> str:
    try:
        return package_version("claude-tap")
    except Exception:
        return "0.0.0"


def _rel_posix(path: Path, base: Path) -> str:
    # Forward slashes so manifests stay portable when `.traces` is synced across OSes.
    return path.relative_to(base).as_posix()


def _load_manifest(output_dir: Path) -> dict:
    """Load or create the trace manifest file."""
    manifest_path = output_dir / _MANIFEST_FILE
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if data.get("_cloudtap"):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    manifest = {"_cloudtap": True, "version": _current_version(), "traces": []}
    _maybe_migrate_existing(output_dir, manifest)
    _save_manifest(output_dir, manifest)
    return manifest


def _save_manifest(output_dir: Path, manifest: dict) -> None:
    """Save the trace manifest to disk."""
    manifest_path = output_dir / _MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _register_trace(output_dir: Path, ts: str, trace_files: list[str], metadata: dict[str, str] | None = None) -> dict:
    """Register a new trace session in the manifest."""
    manifest = _load_manifest(output_dir)
    entry = {
        "timestamp": ts,
        "files": trace_files,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        entry.update(metadata)
    manifest["traces"].append(entry)
    _save_manifest(output_dir, manifest)
    return manifest


def _cleanup_traces(output_dir: Path, max_traces: int) -> int:
    """Remove oldest traces exceeding max_traces. Returns count of deleted sessions."""
    if max_traces <= 0:
        return 0
    manifest = _load_manifest(output_dir)
    traces = manifest.get("traces", [])
    if len(traces) <= max_traces:
        return 0
    traces.sort(key=lambda t: t.get("timestamp", ""))
    to_remove = traces[: len(traces) - max_traces]
    removed = 0
    for entry in to_remove:
        parents_to_check: set[Path] = set()
        for fname in entry.get("files", []):
            fpath = output_dir / fname
            if fpath.exists():
                parents_to_check.add(fpath.parent)
                try:
                    fpath.unlink()
                except OSError:
                    pass
        _remove_empty_trace_dirs(output_dir, parents_to_check)
        traces.remove(entry)
        removed += 1
    manifest["traces"] = traces
    _save_manifest(output_dir, manifest)
    return removed


def _maybe_migrate_existing(output_dir: Path, manifest: dict) -> None:
    """Auto-register existing trace_*.jsonl files that are not yet in the manifest."""
    known_files: set[str] = {
        f.replace("\\", "/") for entry in manifest.get("traces", []) for f in entry.get("files", [])
    }

    for jsonl in sorted(output_dir.glob("**/trace_*.jsonl")):
        rel = _rel_posix(jsonl, output_dir)
        if rel in known_files or jsonl.name in known_files:
            continue
        stem = jsonl.stem
        ts = stem.replace("trace_", "", 1)
        if jsonl.parent != output_dir:
            ts = jsonl.parent.name.replace("-", "") + "_" + ts
        files = [rel]
        for suffix in [".log", ".html"]:
            companion = jsonl.with_suffix(suffix)
            if companion.exists():
                files.append(_rel_posix(companion, output_dir))
        manifest["traces"].append(
            {
                "timestamp": ts,
                "files": files,
                "created_at": datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )


def delete_trace_history(output_dir: Path, date_key: str, protected_paths: Iterable[Path] = ()) -> dict[str, int | str]:
    """Delete stored trace files for a date key while keeping protected active files."""
    if date_key == "legacy":
        trace_dir = output_dir
        date_prefix = ""
    elif _is_date_key(date_key):
        trace_dir = output_dir / date_key
        date_prefix = f"{date_key}/"
    else:
        raise ValueError("Invalid date format")

    if not output_dir.is_dir():
        return {"date": date_key, "deleted_files": 0, "deleted_traces": 0, "skipped_files": 0}

    manifest = _load_manifest(output_dir)
    traces = manifest.get("traces", [])
    protected = {_safe_resolve(path) for path in protected_paths}
    parents_to_check: set[Path] = set()

    candidates = _candidate_trace_files(output_dir, trace_dir, protected)
    entries_to_remove = []
    for entry in traces:
        files = [str(fname).replace("\\", "/") for fname in entry.get("files", [])]
        if not files:
            continue
        if _entry_has_protected_file(output_dir, files, protected):
            continue
        if _entry_matches_date(files, date_prefix):
            entries_to_remove.append(entry)
            candidates.update(files)

    deleted_files = 0
    skipped_files = 0
    for rel in sorted(candidates):
        fpath = output_dir / rel
        if _safe_resolve(fpath) in protected:
            skipped_files += 1
            continue
        if not _is_relative_to_output(output_dir, fpath):
            skipped_files += 1
            continue
        if not fpath.exists():
            continue
        if fpath.is_file():
            parents_to_check.add(fpath.parent)
            try:
                fpath.unlink()
                deleted_files += 1
            except OSError:
                skipped_files += 1

    for entry in entries_to_remove:
        if entry in traces:
            traces.remove(entry)
    manifest["traces"] = traces
    _save_manifest(output_dir, manifest)
    _remove_empty_trace_dirs(output_dir, parents_to_check)

    return {
        "date": date_key,
        "deleted_files": deleted_files,
        "deleted_traces": len(entries_to_remove),
        "skipped_files": skipped_files,
    }


def _is_date_key(value: str) -> bool:
    if len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _candidate_trace_files(output_dir: Path, trace_dir: Path, protected: set[Path]) -> set[str]:
    if not trace_dir.is_dir():
        return set()

    candidates: set[str] = set()
    for jsonl in sorted(trace_dir.glob("trace_*.jsonl")):
        if _safe_resolve(jsonl) in protected:
            continue
        for path in (jsonl, jsonl.with_suffix(".log"), jsonl.with_suffix(".html")):
            if path.exists() and _is_relative_to_output(output_dir, path):
                candidates.add(_rel_posix(path, output_dir))
    return candidates


def _entry_matches_date(files: list[str], date_prefix: str) -> bool:
    if date_prefix:
        return any(fname.startswith(date_prefix) for fname in files)
    return any("/" not in fname and Path(fname).name.startswith("trace_") for fname in files)


def _entry_has_protected_file(output_dir: Path, files: list[str], protected: set[Path]) -> bool:
    return any(_safe_resolve(output_dir / fname) in protected for fname in files)


def _remove_empty_trace_dirs(output_dir: Path, parents: set[Path]) -> None:
    for parent in parents:
        if (
            parent != output_dir
            and parent.is_dir()
            and _is_relative_to_output(output_dir, parent)
            and not any(parent.iterdir())
        ):
            try:
                parent.rmdir()
            except OSError:
                pass


def _safe_resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _is_relative_to_output(output_dir: Path, path: Path) -> bool:
    try:
        _safe_resolve(path).relative_to(_safe_resolve(output_dir))
    except ValueError:
        return False
    return True
