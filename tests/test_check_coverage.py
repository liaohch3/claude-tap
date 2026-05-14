from __future__ import annotations

import json

from scripts.check_coverage import (
    changed_lines_from_diff,
    changed_viewer_css_selectors,
    changed_viewer_functions,
    check_python_coverage,
    check_viewer_css_coverage,
    css_selector_ranges,
    js_function_ranges,
)


def test_changed_lines_from_diff_extracts_new_line_numbers() -> None:
    diff = """diff --git a/claude_tap/viewer.py b/claude_tap/viewer.py
--- a/claude_tap/viewer.py
+++ b/claude_tap/viewer.py
@@ -10,0 +11,2 @@
+def added():
+    return True
@@ -20,2 +22,2 @@
-old = 1
+new = 2
 context = True
"""

    assert changed_lines_from_diff(diff) == {"claude_tap/viewer.py": {11, 12, 22}}


def test_check_python_coverage_counts_only_changed_executable_package_lines(tmp_path) -> None:
    report = {
        "totals": {"percent_covered": 75.0},
        "files": {
            "claude_tap/viewer.py": {
                "executed_lines": [10, 11, 13],
                "missing_lines": [12],
                "excluded_lines": [],
            }
        },
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    results = check_python_coverage(
        report_path,
        {"claude_tap/viewer.py": {10, 12, 99}, "tests/test_viewer.py": {1}},
        total_min=65.0,
        diff_min=80.0,
    )

    assert results[0].name == "python_total"
    assert results[0].passed is True
    assert results[1].name == "python_diff"
    assert results[1].percent == 50.0
    assert results[1].passed is False
    assert results[1].detail == "1/2 changed executable Python lines covered"


def test_js_function_ranges_and_changed_viewer_functions_find_touched_functions(tmp_path) -> None:
    viewer = tmp_path / "viewer.html"
    viewer.write_text(
        """<script>
function untouched() {
  return 1;
}
function changedOne() {
  const value = 2;
  return value;
}
function changedTwo() { return 3; }
</script>
""",
        encoding="utf-8",
    )

    assert js_function_ranges(viewer.read_text(encoding="utf-8")) == {
        "untouched": (2, 4),
        "changedOne": (5, 8),
        "changedTwo": (9, 9),
    }
    assert changed_viewer_functions(
        viewer,
        {"claude_tap/viewer.html": {6, 9}},
    ) == {"changedOne", "changedTwo"}


def test_css_selector_ranges_and_changed_viewer_css_selectors_find_touched_rules(tmp_path) -> None:
    viewer = tmp_path / "viewer.html"
    viewer.write_text(
        """<style>
.header, .toolbar {
  display: flex;
}
.button:hover { color: blue; }
@media (max-width: 768px) {
  #detail.mobile-fullwidth { width: 100%; }
  .header { display: block; }
}
</style>
""",
        encoding="utf-8",
    )

    assert css_selector_ranges(viewer.read_text(encoding="utf-8")) == {
        ".header": [(2, 4), (8, 8)],
        ".toolbar": [(2, 4)],
        "#detail.mobile-fullwidth": [(7, 7)],
    }
    assert changed_viewer_css_selectors(
        viewer,
        {"claude_tap/viewer.html": {3, 7, 8}},
    ) == {".header", ".toolbar", "#detail.mobile-fullwidth"}
    assert changed_viewer_css_selectors(
        viewer,
        {"claude_tap/viewer.html": {8}},
    ) == {".header"}


def test_check_viewer_css_coverage_enforces_changed_selector_matches() -> None:
    results = check_viewer_css_coverage(
        {".covered", ".missing"},
        selector_min=60.0,
        diff_min=80.0,
        coverage=(75.0, {".covered", ".other"}, 3, 4, 1),
    )

    assert results[0].name == "viewer_css_selectors"
    assert results[0].passed is True
    assert results[0].detail == "3/4 queryable CSS selectors matched; 1 state/pseudo selectors skipped"
    assert results[1].name == "viewer_css_diff"
    assert results[1].percent == 50.0
    assert results[1].passed is False
    assert results[1].detail == "1/2 changed CSS selectors matched; missing: .missing"
