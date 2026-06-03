from __future__ import annotations

import json
import subprocess

import scripts.check_coverage as coverage_module
from scripts.check_coverage import (
    VIEWER_JS_SOURCES,
    _filter_pure_viewer_asset_split,
    _is_function_covered,
    _tag_content,
    _viewer_script_functions,
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


def test_filter_pure_viewer_asset_split_ignores_exact_extracted_assets(monkeypatch, tmp_path) -> None:
    base_html = """<html><head><style>
.x { color: red; }
</style></head><body><script>
function moved() { return true; }
</script></body></html>
"""
    assets = tmp_path / "claude_tap" / "viewer_assets"
    assets.mkdir(parents=True)
    (assets / "viewer.css").write_text(".x { color: red; }\n", encoding="utf-8")
    for source in VIEWER_JS_SOURCES:
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (tmp_path / VIEWER_JS_SOURCES[0]).write_text("function moved() { return true; }\n", encoding="utf-8")
    (tmp_path / "claude_tap" / "viewer.html").write_text(
        """<html><head><!-- CLAUDE_TAP_VIEWER_STYLE --></head><body><!-- CLAUDE_TAP_VIEWER_SCRIPT --></body></html>
""",
        encoding="utf-8",
    )

    def fake_check_output(cmd, **kwargs):
        assert cmd == ["git", "show", "origin/main:claude_tap/viewer.html"]
        return base_html

    monkeypatch.setattr(coverage_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "check_output", fake_check_output)

    filtered = _filter_pure_viewer_asset_split(
        {
            "claude_tap/viewer_assets/viewer.css": {1},
            VIEWER_JS_SOURCES[0]: {1},
            "claude_tap/viewer.html": {1},
            "claude_tap/viewer.py": {10},
        },
        "origin/main",
    )

    assert filtered == {"claude_tap/viewer.py": {10}}
    assert _tag_content(base_html, "style") == ".x { color: red; }"


def test_changed_viewer_functions_does_not_fallback_to_template_lines_for_split_asset(tmp_path) -> None:
    viewer = tmp_path / "state.js"
    viewer.write_text(
        """function realAssetFunction() {
  return true;
}
""",
        encoding="utf-8",
    )

    assert changed_viewer_functions(viewer, {"claude_tap/viewer.html": {1, 2}}) == set()


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
    viewer = tmp_path / "viewer.js"
    viewer.write_text(
        """function untouched() {
  return 1;
}
function changedOne() {
  const value = 2;
  return value;
}
function changedTwo() { return 3; }
""",
        encoding="utf-8",
    )

    assert js_function_ranges(viewer.read_text(encoding="utf-8")) == {
        "untouched": (1, 3),
        "changedOne": (4, 7),
        "changedTwo": (8, 8),
    }
    assert changed_viewer_functions(
        viewer,
        {"claude_tap/viewer_assets/viewer.js": {5, 8}},
    ) == {"changedOne", "changedTwo"}


def test_css_selector_ranges_and_changed_viewer_css_selectors_find_touched_rules(tmp_path) -> None:
    viewer = tmp_path / "viewer.css"
    viewer.write_text(
        """.header, .toolbar {
  display: flex;
}
.button:hover { color: blue; }
@media (max-width: 768px) {
  #detail.mobile-fullwidth { width: 100%; }
  .header { display: block; }
}
""",
        encoding="utf-8",
    )

    assert css_selector_ranges(viewer.read_text(encoding="utf-8")) == {
        ".header": [(1, 3), (7, 7)],
        ".toolbar": [(1, 3)],
        "#detail.mobile-fullwidth": [(6, 6)],
    }
    assert changed_viewer_css_selectors(
        viewer,
        {"claude_tap/viewer_assets/viewer.css": {2, 6, 7}},
    ) == {".header", ".toolbar", "#detail.mobile-fullwidth"}
    assert changed_viewer_css_selectors(
        viewer,
        {"claude_tap/viewer_assets/viewer.css": {7}},
    ) == {".header"}


def test_viewer_script_functions_filters_top_level_wrapper_and_detects_coverage() -> None:
    script = {
        "functions": [
            {"functionName": "", "ranges": [{"startOffset": 0, "endOffset": 1000, "count": 1}]},
            {"functionName": "renderEmptyTraceState", "ranges": [{"startOffset": 100, "endOffset": 220, "count": 1}]},
            {"functionName": "initFileDropZone", "ranges": [{"startOffset": 240, "endOffset": 320, "count": 0}]},
        ]
    }

    functions = _viewer_script_functions(script)

    assert [function["functionName"] for function in functions] == ["renderEmptyTraceState", "initFileDropZone"]
    assert _is_function_covered(functions[0]) is True
    assert _is_function_covered(functions[1]) is False


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
