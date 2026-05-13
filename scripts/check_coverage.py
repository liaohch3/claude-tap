#!/usr/bin/env python3
"""Enforce project and incremental coverage targets.

Python coverage is read from a coverage.py JSON report. Viewer frontend coverage
is measured with Chromium V8 precise coverage against the cross-client viewer
contract traces. The frontend incremental metric is function-oriented: changed
viewer.html JavaScript functions must be exercised by V8 coverage.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "pyproject.toml"
DEFAULT_THRESHOLDS = {
    "python_total_min": 65.0,
    "python_diff_min": 80.0,
    "viewer_js_function_min": 50.0,
    "viewer_js_diff_min": 80.0,
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    percent: float | None
    minimum: float
    passed: bool
    detail: str


def _run_git_diff(base: str, paths: list[str]) -> str:
    cmd = ["git", "diff", "--unified=0", f"{base}...HEAD", "--", *paths]
    return subprocess.check_output(cmd, cwd=REPO_ROOT, text=True)


def changed_lines_from_diff(diff_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line: int | None = None
    hunk_re = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            new_line = None
            continue
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/") :]
            changed.setdefault(current_file, set())
            continue
        if line.startswith("@@ "):
            match = hunk_re.search(line)
            if not match:
                new_line = None
                continue
            new_line = int(match.group(1))
            continue
        if current_file is None or new_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            changed[current_file].add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            new_line += 1

    return {path: lines for path, lines in changed.items() if lines}


def load_thresholds(config_path: Path = DEFAULT_CONFIG) -> dict[str, float]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    configured = data.get("tool", {}).get("claude_tap", {}).get("coverage", {})
    thresholds = dict(DEFAULT_THRESHOLDS)
    for key in thresholds:
        if key in configured:
            thresholds[key] = float(configured[key])
    return thresholds


def check_python_coverage(
    coverage_json_path: Path,
    changed_lines: dict[str, set[int]],
    total_min: float,
    diff_min: float,
) -> list[CheckResult]:
    coverage = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    total_percent = float(coverage["totals"]["percent_covered"])
    results = [
        CheckResult(
            name="python_total",
            percent=total_percent,
            minimum=total_min,
            passed=total_percent >= total_min,
            detail=f"coverage.py total {total_percent:.2f}% >= {total_min:.2f}%",
        )
    ]

    executable_changed = 0
    covered_changed = 0
    files = coverage.get("files", {})
    for path, changed in changed_lines.items():
        if not path.startswith("claude_tap/") or not path.endswith(".py"):
            continue
        file_cov = files.get(path)
        if not file_cov:
            executable_changed += len(changed)
            continue
        executed = set(file_cov.get("executed_lines", []))
        missing = set(file_cov.get("missing_lines", []))
        executable = executed | missing
        relevant = changed & executable
        executable_changed += len(relevant)
        covered_changed += len(relevant & executed)

    if executable_changed == 0:
        results.append(
            CheckResult(
                name="python_diff",
                percent=None,
                minimum=diff_min,
                passed=True,
                detail="no changed executable Python package lines",
            )
        )
        return results

    diff_percent = covered_changed / executable_changed * 100
    results.append(
        CheckResult(
            name="python_diff",
            percent=diff_percent,
            minimum=diff_min,
            passed=diff_percent >= diff_min,
            detail=f"{covered_changed}/{executable_changed} changed executable Python lines covered",
        )
    )
    return results


def js_function_ranges(source: str) -> dict[str, tuple[int, int]]:
    lines = source.splitlines()
    ranges: dict[str, tuple[int, int]] = {}
    fn_re = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(")

    line_no = 1
    while line_no <= len(lines):
        line = lines[line_no - 1]
        match = fn_re.search(line)
        if not match:
            line_no += 1
            continue

        name = match.group(1)
        depth = 0
        seen_open = False
        end_line = line_no
        scan = line_no
        while scan <= len(lines):
            for char in lines[scan - 1]:
                if char == "{":
                    depth += 1
                    seen_open = True
                elif char == "}":
                    depth -= 1
                    if seen_open and depth <= 0:
                        end_line = scan
                        break
            if seen_open and depth <= 0:
                break
            scan += 1

        ranges[name] = (line_no, end_line)
        line_no = max(end_line + 1, line_no + 1)

    return ranges


def changed_viewer_functions(viewer_html: Path, changed_lines: dict[str, set[int]]) -> set[str]:
    changed = changed_lines.get("claude_tap/viewer.html", set())
    if not changed:
        return set()
    ranges = js_function_ranges(viewer_html.read_text(encoding="utf-8"))
    functions: set[str] = set()
    for name, (start, end) in ranges.items():
        if any(start <= line <= end for line in changed):
            functions.add(name)
    return functions


def _main_viewer_script(coverage: dict[str, Any], suffix: str) -> dict[str, Any]:
    candidates = [
        script
        for script in coverage["result"]
        if script.get("url", "").endswith(suffix) and len(script.get("functions", [])) > 50
    ]
    if not candidates:
        raise RuntimeError("Could not find viewer.html main script in V8 coverage output")
    return max(candidates, key=lambda script: len(script.get("functions", [])))


def _is_top_level_wrapper(function: dict[str, Any], script_end: int) -> bool:
    if function.get("functionName"):
        return False
    ranges = function.get("ranges", [])
    if not ranges:
        return False
    widest = max((item.get("endOffset", 0) - item.get("startOffset", 0) for item in ranges), default=0)
    return script_end > 0 and widest >= script_end * 0.8


def collect_viewer_js_coverage() -> tuple[float, set[str], int, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised in dependency-free environments
        raise RuntimeError("Playwright is required for viewer JS coverage") from exc

    contracts_path = REPO_ROOT / "tests" / "test_viewer_contracts.py"
    spec = importlib.util.spec_from_file_location("viewer_contracts_for_coverage", contracts_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load viewer contract helpers from {contracts_path}")
    contracts = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contracts
    spec.loader.exec_module(contracts)
    _contract_cases = contracts._contract_cases
    _generate_case_html = contracts._generate_case_html

    with tempfile.TemporaryDirectory() as tmp:
        html_path = _generate_case_html(
            Path(tmp),
            "v8_coverage",
            tuple(record for case in _contract_cases() for record in case.records),
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            session = page.context.new_cdp_session(page)
            session.send("Profiler.enable")
            session.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
            page.goto(html_path.resolve().as_uri(), timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)
            for index in range(page.evaluate("entries.length")):
                page.evaluate("entryIndex => renderDetail(entries[entryIndex])", index)
                page.wait_for_selector("#detail .section", timeout=5000)
                page.evaluate(
                    """(entryIndex) => {
                      const entry = entries[entryIndex];
                      const body = entry.request.body;
                      getMessages(body);
                      getRequestTools(body);
                      extractSystem(body);
                      getUsage(entry);
                      getResponseEvents(entry);
                      getResponseOutput(entry);
                    }""",
                    index,
                )
            coverage = session.send("Profiler.takePreciseCoverage")
            session.send("Profiler.stopPreciseCoverage")
            session.send("Profiler.disable")
            browser.close()

    script = _main_viewer_script(coverage, "v8_coverage.html")
    all_ranges = [item for function in script["functions"] for item in function.get("ranges", [])]
    script_end = max((item.get("endOffset", 0) for item in all_ranges), default=0)
    functions = [function for function in script["functions"] if not _is_top_level_wrapper(function, script_end)]
    covered_functions = [
        function for function in functions if any(item.get("count", 0) > 0 for item in function.get("ranges", []))
    ]
    covered_names = {function.get("functionName", "") for function in covered_functions if function.get("functionName")}
    percent = len(covered_functions) / len(functions) * 100 if functions else 100.0
    return percent, covered_names, len(covered_functions), len(functions)


def check_viewer_js_coverage(
    changed_functions: set[str],
    function_min: float,
    diff_min: float,
) -> list[CheckResult]:
    function_percent, covered_names, covered_count, total_count = collect_viewer_js_coverage()
    results = [
        CheckResult(
            name="viewer_js_functions",
            percent=function_percent,
            minimum=function_min,
            passed=function_percent >= function_min,
            detail=f"{covered_count}/{total_count} V8 functions executed",
        )
    ]

    if not changed_functions:
        results.append(
            CheckResult(
                name="viewer_js_diff",
                percent=None,
                minimum=diff_min,
                passed=True,
                detail="no changed viewer.html JavaScript functions",
            )
        )
        return results

    covered_changed = changed_functions & covered_names
    diff_percent = len(covered_changed) / len(changed_functions) * 100
    missing = ", ".join(sorted(changed_functions - covered_names)) or "none"
    results.append(
        CheckResult(
            name="viewer_js_diff",
            percent=diff_percent,
            minimum=diff_min,
            passed=diff_percent >= diff_min,
            detail=f"{len(covered_changed)}/{len(changed_functions)} changed JS functions covered; missing: {missing}",
        )
    )
    return results


def print_results(results: list[CheckResult]) -> None:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        if result.percent is None:
            print(f"{status} {result.name}: {result.detail} (target {result.minimum:.2f}%)")
        else:
            print(f"{status} {result.name}: {result.percent:.2f}% >= {result.minimum:.2f}% ({result.detail})")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Base ref for incremental coverage diff")
    parser.add_argument("--python-coverage", type=Path, default=Path(".coverage.json"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--skip-python", action="store_true")
    parser.add_argument("--skip-viewer-js", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thresholds = load_thresholds(args.config)
    changed_lines = changed_lines_from_diff(_run_git_diff(args.base, ["claude_tap/*.py", "claude_tap/viewer.html"]))

    results: list[CheckResult] = []
    if not args.skip_python:
        results.extend(
            check_python_coverage(
                args.python_coverage,
                changed_lines,
                thresholds["python_total_min"],
                thresholds["python_diff_min"],
            )
        )
    if not args.skip_viewer_js:
        results.extend(
            check_viewer_js_coverage(
                changed_viewer_functions(REPO_ROOT / "claude_tap" / "viewer.html", changed_lines),
                thresholds["viewer_js_function_min"],
                thresholds["viewer_js_diff_min"],
            )
        )

    print_results(results)
    return 0 if all(result.passed for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
