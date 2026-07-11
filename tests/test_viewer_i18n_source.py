from __future__ import annotations

from pathlib import Path

from claude_tap.viewer import VIEWER_JS_PATHS, _generate_html_viewer, _load_viewer_i18n, _read_viewer_template

EXPECTED_LANGUAGES = ["en", "zh-CN", "ja", "ko", "fr", "ar", "de", "ru"]
CRITICAL_KEYS = [
    "title",
    "section_system",
    "section_messages",
    "section_tools",
    "section_response",
    "section_sse",
    "section_json",
    "empty_trace_title",
    "diff_select_target",
]


def test_viewer_i18n_json_has_complete_language_key_sets() -> None:
    entries = _load_viewer_i18n()

    assert list(entries) == EXPECTED_LANGUAGES
    source_keys = set(entries["en"])
    assert len(source_keys) >= 40
    for lang in EXPECTED_LANGUAGES:
        assert set(entries[lang]) == source_keys
        for key in CRITICAL_KEYS:
            assert entries[lang][key]


def test_viewer_session_sort_label_uses_query_language() -> None:
    entries = _load_viewer_i18n()

    assert entries["en"]["sort_session"] == "Query"
    assert entries["zh-CN"]["sort_session"] == "用户输入"


def test_read_viewer_template_embeds_i18n_before_main_script() -> None:
    html = _read_viewer_template()

    assert "const __CLAUDE_TAP_I18N__ =" in html
    assert "const I18N = typeof __CLAUDE_TAP_I18N__" in html
    assert '"section_system":"System Prompt"' in html
    assert '"section_tools":"工具"' in html
    assert html.index("const __CLAUDE_TAP_I18N__ =") < html.index("const $ = s =>")
    assert "CLAUDE_TAP_VIEWER_STYLE" not in html
    assert "CLAUDE_TAP_VIEWER_SCRIPT" not in html
    assert "viewer_assets" not in html


def test_read_viewer_template_embeds_split_js_assets_in_order() -> None:
    html = _read_viewer_template()

    markers = [
        "const $ = s =>",
        "function expandWebSocketResponseEntries",
        "function renderApp",
        "function renderSidebar",
        "function renderDetail",
        "function renderContent",
        "function renderJSONTree",
        "function lineDiff",
        "function mobileNext",
    ]

    positions = [html.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_split_viewer_js_assets_use_semantic_filenames() -> None:
    names = [path.name for path in VIEWER_JS_PATHS]

    assert names == [
        "state.js",
        "responses.js",
        "lazy_loading.js",
        "i18n_ui.js",
        "live_bootstrap.js",
        "filters_search.js",
        "sidebar.js",
        "detail_trace.js",
        "renderers.js",
        "sections_json.js",
        "diff.js",
        "utilities_mobile.js",
    ]
    assert all(path.exists() for path in VIEWER_JS_PATHS)
    assert all(not name[0].isdigit() for name in names)


def test_generate_html_viewer_remains_self_contained_after_i18n_split(tmp_path: Path) -> None:
    trace_path = tmp_path / "empty.jsonl"
    html_path = tmp_path / "empty.html"
    trace_path.write_text("", encoding="utf-8")

    _generate_html_viewer(trace_path, html_path)

    html = html_path.read_text(encoding="utf-8")
    assert "const __CLAUDE_TAP_I18N__ =" in html
    assert "viewer_i18n.json" not in html
    assert "No API calls captured" in html
    assert "EMBEDDED_TRACE_COMPACT_DATA" in html
