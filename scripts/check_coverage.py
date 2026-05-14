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
    "viewer_css_selector_min": 65.0,
    "viewer_css_diff_min": 80.0,
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


def _split_selectors(selector_text: str) -> list[str]:
    return [part.strip() for part in selector_text.split(",") if part.strip()]


def _is_queryable_selector(selector: str) -> bool:
    return not re.search(
        r"::|:(hover|active|focus|focus-visible|focus-within|visited|target|checked|disabled|enabled|placeholder-shown)\b",
        selector,
    )


def _style_block_ranges(source: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for idx, line in enumerate(source.splitlines(), start=1):
        if "<style>" in line:
            start = idx + 1
            continue
        if "</style>" in line and start is not None:
            ranges.append((start, idx - 1))
            start = None
    return ranges


def css_selector_ranges(source: str) -> dict[str, list[tuple[int, int]]]:
    lines = source.splitlines()
    ranges: dict[str, list[tuple[int, int]]] = {}

    for style_start, style_end in _style_block_ranges(source):
        line_no = style_start
        while line_no <= style_end:
            line = lines[line_no - 1]
            if "{" not in line:
                line_no += 1
                continue
            selector_text = line.split("{", 1)[0].strip()
            if not selector_text or selector_text.startswith("@"):
                line_no += 1
                continue

            depth = 0
            end_line = line_no
            for scan in range(line_no, style_end + 1):
                for char in lines[scan - 1]:
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                if depth <= 0:
                    end_line = scan
                    break

            for selector in _split_selectors(selector_text):
                if _is_queryable_selector(selector):
                    ranges.setdefault(selector, []).append((line_no, end_line))
            line_no = max(end_line + 1, line_no + 1)

    return ranges


def changed_viewer_css_selectors(viewer_html: Path, changed_lines: dict[str, set[int]]) -> set[str]:
    changed = changed_lines.get("claude_tap/viewer.html", set())
    if not changed:
        return set()
    ranges = css_selector_ranges(viewer_html.read_text(encoding="utf-8"))
    selectors: set[str] = set()
    for selector, selector_ranges in ranges.items():
        if any(start <= line <= end for start, end in selector_ranges for line in changed):
            selectors.add(selector)
    return selectors


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


def _load_viewer_contract_helpers() -> tuple[Any, Any]:
    contracts_path = REPO_ROOT / "tests" / "test_viewer_contracts.py"
    spec = importlib.util.spec_from_file_location("viewer_contracts_for_coverage", contracts_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load viewer contract helpers from {contracts_path}")
    contracts = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = contracts
    spec.loader.exec_module(contracts)
    return contracts._contract_cases, contracts._generate_case_html


def collect_viewer_js_coverage() -> tuple[float, set[str], int, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised in dependency-free environments
        raise RuntimeError("Playwright is required for viewer JS coverage") from exc

    _contract_cases, _generate_case_html = _load_viewer_contract_helpers()

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
                      const jsonSection = Array.from(document.querySelectorAll('#detail .section'))
                        .find(el => el.querySelector('.title')?.textContent === t('section_json'));
                      const jsonToggle = jsonSection?.querySelector('.jt-toggle');
                      if (jsonToggle) {
                        jsonToggle.click();
                        jsonToggle.click();
                      }
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


def collect_viewer_css_coverage() -> tuple[float, set[str], int, int, int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - exercised in dependency-free environments
        raise RuntimeError("Playwright is required for viewer CSS coverage") from exc

    _contract_cases, _generate_case_html = _load_viewer_contract_helpers()
    collect_css_script = r"""() => {
      const skipped = [];
      const used = new Set();
      const all = [];
      const skipRe = /::|:(hover|active|focus|focus-visible|focus-within|visited|target|checked|disabled|enabled|placeholder-shown)\b/;
      const splitSelectors = text => text.split(',').map(s => s.trim()).filter(Boolean);
      const visit = rules => {
        for (const rule of Array.from(rules || [])) {
          if (rule.type === CSSRule.STYLE_RULE) {
            for (const selector of splitSelectors(rule.selectorText)) {
              if (skipRe.test(selector)) {
                skipped.push(selector);
                continue;
              }
              try {
                all.push(selector);
                if (document.querySelector(selector)) used.add(selector);
              } catch (e) {
                skipped.push(selector);
              }
            }
          } else if (rule.cssRules) {
            visit(rule.cssRules);
          }
        }
      };
      for (const sheet of Array.from(document.styleSheets)) {
        try { visit(sheet.cssRules); } catch (e) {}
      }
      return { used: [...used], all: [...new Set(all)], skipped: [...new Set(skipped)] };
    }"""

    used_selectors: set[str] = set()
    all_selectors: set[str] = set()
    skipped_selectors: set[str] = set()

    def merge(snapshot: dict[str, list[str]]) -> None:
        used_selectors.update(snapshot["used"])
        all_selectors.update(snapshot["all"])
        skipped_selectors.update(snapshot["skipped"])

    with tempfile.TemporaryDirectory() as tmp:
        html_path = _generate_case_html(
            Path(tmp),
            "css_usage",
            tuple(record for case in _contract_cases() for record in case.records),
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(html_path.resolve().as_uri(), timeout=10000)
            page.wait_for_selector(".sidebar-item", timeout=5000)

            for index in range(page.evaluate("entries.length")):
                page.evaluate("entryIndex => renderDetail(entries[entryIndex])", index)
                merge(page.evaluate(collect_css_script))

            page.evaluate(
                """() => {
                  openGlobalSearch();
                  const input = document.querySelector('#global-search-input');
                  input.value = 'contract';
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                }"""
            )
            merge(page.evaluate(collect_css_script))

            if page.evaluate("filtered.length") > 1:
                page.evaluate("showDiffForIdx(1, null, 0)")
                merge(page.evaluate(collect_css_script))

            page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
            merge(page.evaluate(collect_css_script))
            page.set_viewport_size({"width": 390, "height": 900})
            page.evaluate("mobileShowDetail()")
            merge(page.evaluate(collect_css_script))
            browser.close()

    percent = len(used_selectors) / len(all_selectors) * 100 if all_selectors else 100.0
    return percent, used_selectors, len(used_selectors), len(all_selectors), len(skipped_selectors)


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


def check_viewer_css_coverage(
    changed_selectors: set[str],
    selector_min: float,
    diff_min: float,
    coverage: tuple[float, set[str], int, int, int] | None = None,
) -> list[CheckResult]:
    selector_percent, used_selectors, used_count, total_count, skipped_count = coverage or collect_viewer_css_coverage()
    results = [
        CheckResult(
            name="viewer_css_selectors",
            percent=selector_percent,
            minimum=selector_min,
            passed=selector_percent >= selector_min,
            detail=f"{used_count}/{total_count} queryable CSS selectors matched; {skipped_count} state/pseudo selectors skipped",
        )
    ]

    if not changed_selectors:
        results.append(
            CheckResult(
                name="viewer_css_diff",
                percent=None,
                minimum=diff_min,
                passed=True,
                detail="no changed viewer.html CSS selectors",
            )
        )
        return results

    covered_changed = changed_selectors & used_selectors
    diff_percent = len(covered_changed) / len(changed_selectors) * 100
    missing = ", ".join(sorted(changed_selectors - used_selectors)) or "none"
    results.append(
        CheckResult(
            name="viewer_css_diff",
            percent=diff_percent,
            minimum=diff_min,
            passed=diff_percent >= diff_min,
            detail=f"{len(covered_changed)}/{len(changed_selectors)} changed CSS selectors matched; missing: {missing}",
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
    parser.add_argument("--skip-viewer-css", action="store_true")
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
    if not args.skip_viewer_css:
        results.extend(
            check_viewer_css_coverage(
                changed_viewer_css_selectors(REPO_ROOT / "claude_tap" / "viewer.html", changed_lines),
                thresholds["viewer_css_selector_min"],
                thresholds["viewer_css_diff_min"],
            )
        )

    print_results(results)
    return 0 if all(result.passed for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
