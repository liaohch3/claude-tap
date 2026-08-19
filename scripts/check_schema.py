#!/usr/bin/env python3
"""Reject schema-less production annotations.

The checker has two complementary modes: a full production scan keeps the
runtime tree clean, while the diff scan gives contributors a focused error
when a forbidden annotation is added. Test fixtures are intentionally outside
this gate because they construct malformed and provider-shaped payloads.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

_SCHEMALESS_NAMES = {
    "Any",
    "Dict",
    "TypedDict",
    "Mapping",
    "MutableMapping",
    "dict",
    "Map",
    "JsonObject",
    "JsonValue",
    "object",
    "list",
    "tuple",
    "set",
    "frozenset",
}
_ALLOWED_FILES = {Path("claude_tap/models.py"), Path("scripts/check_schema.py")}
_SOURCE_ROOTS = (Path("claude_tap"), Path("scripts"))


def _changed_lines(base: str) -> dict[Path, set[int]]:
    result = subprocess.run(
        ["git", "diff", "--unified=0", base, "--", "*.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    changed: dict[Path, set[int]] = {}
    current: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ b/"):
            current = Path(line[6:])
            if current.parts and current.parts[0] not in {root.name for root in _SOURCE_ROOTS}:
                current = None
                continue
            changed.setdefault(current, set())
            continue
        if not line.startswith("@@") or current is None:
            continue
        hunk = line.split("+")[1].split(" ", 1)[0]
        start, _, count = hunk.partition(",")
        first = int(start)
        length = int(count or "1")
        changed[current].update(range(first, first + length))
    return changed


def _annotation_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _find_violations(path: Path, lines: set[int] | None = None) -> list[str]:
    if any(path == allowed or path.resolve() == allowed.resolve() for allowed in _ALLOWED_FILES) or not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: unable to parse Python source: {exc.msg}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id == "TypedDict" for base in node.bases
        ):
            if lines is None or node.lineno in lines:
                violations.append(f"{path}:{node.lineno}: use a Pydantic BaseModel instead of TypedDict")
        annotation = None
        if isinstance(node, (ast.AnnAssign, ast.arg)):
            annotation = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        if annotation is None or (lines is not None and annotation.lineno not in lines):
            continue
        names = _annotation_names(annotation)
        forbidden = names & _SCHEMALESS_NAMES
        container_nodes = [
            node
            for node in ast.walk(annotation)
            if isinstance(node, ast.Name) and node.id in {"dict", "list", "tuple", "set", "frozenset"}
        ]
        parameterized_container_nodes = [
            node.value
            for node in ast.walk(annotation)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in {container.id for container in container_nodes}
        ]
        # Parameterized containers have explicit member contracts. The schema
        # gate rejects only bare containers and dynamically-valued members.
        for name in {container.id for container in container_nodes}:
            nodes = [container for container in container_nodes if container.id == name]
            if all(container in parameterized_container_nodes for container in nodes):
                forbidden.discard(name)
        if forbidden:
            name = sorted(forbidden)[0]
            if name == "Any":
                prefix = "new " if lines is not None else ""
                message = f"{prefix}Any annotation; use a Pydantic model or ProviderPayload"
            elif name in {"Mapping", "MutableMapping"}:
                message = f"{name} annotation; use a Pydantic model or explicit JSON boundary"
            elif name == "TypedDict":
                message = "TypedDict annotation; use a Pydantic BaseModel"
            elif name in {"list", "tuple", "set", "frozenset"}:
                message = f"bare {name} annotation; declare its member schema"
            else:
                message = "dict annotation; use a Pydantic model or ProviderPayload"
            violations.append(f"{path}:{annotation.lineno}: {message}")
    return violations


def check_paths(paths: dict[Path, set[int]]) -> list[str]:
    """Return violations for an already computed changed-line map."""
    return [violation for path, lines in paths.items() for violation in _find_violations(path, lines)]


def repository_paths(root: Path | None = None) -> list[Path]:
    """Return Python source files covered by the repository schema policy."""
    base = root or Path(__file__).resolve().parents[1]
    return [path for source_root in _SOURCE_ROOTS for path in (base / source_root).rglob("*.py")]


def check_repository(paths: list[Path]) -> list[str]:
    """Return all schema violations in the supplied repository files."""
    return [violation for path in paths for violation in _find_violations(path)]


def _ruff_any_violations(paths: dict[Path, set[int]]) -> list[str]:
    files = [str(path) for path in paths if path.exists() and path.suffix == ".py"]
    if not files:
        return []
    ruff = shutil.which("ruff")
    command = [ruff] if ruff else ["uv", "run", "ruff"]
    result = subprocess.run(
        [*command, "check", "--select", "ANN401", "--output-format", "json", *files],
        check=False,
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return []
    findings = json.loads(result.stdout)
    violations: list[str] = []
    for finding in findings:
        path = Path(finding["filename"])
        line = finding["location"]["row"]
        if line in paths.get(path, set()):
            violations.append(f"{path}:{line}: {finding['message']} (Ruff ANN401)")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main", help="Git base used to identify changed lines")
    args = parser.parse_args()
    existing = check_repository(repository_paths())
    if existing:
        print("Repository schema check failed:")
        print("\n".join(existing))
        return 1
    changed = _changed_lines(args.base)
    violations = check_paths(changed) + _ruff_any_violations(changed)
    if violations:
        print("Incremental schema check failed:")
        print("\n".join(violations))
        return 1
    print("Incremental schema check passed (new annotations are schema-defined or explicit JSON boundaries).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
