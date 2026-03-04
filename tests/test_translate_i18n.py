from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "translate_i18n.py"
SPEC = importlib.util.spec_from_file_location("translate_i18n", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SAMPLE_SOURCE = """
const I18N = {
  en: {
    title: "Trace Viewer",
    copy: "Copy",
    refresh: "Refresh",
  },
  "zh-CN": {
    title: "追踪查看器",
    copy: "复制",
    refresh: "刷新",
  },
  ja: {
    title: "トレースビューア",
    copy: "コピー",
  },
  ko: {
    title: "트레이스 뷰어",
    copy: "복사",
  },
  fr: {
    title: "Visionneuse de traces",
    copy: "Copier",
  },
  ar: {
    title: "عارض التتبع",
    copy: "نسخ",
  },
  de: {
    title: "Trace-Viewer",
    copy: "Kopieren",
  },
  ru: {
    title: "Просмотр трассировки",
    copy: "Копировать",
  },
};
"""


def test_find_missing_keys_from_en_and_zh_cn_intersection() -> None:
    _, _, entries = MODULE.collect_i18n_data(SAMPLE_SOURCE, "I18N")

    missing = MODULE.find_missing_keys(entries, MODULE.LANG_ORDER)

    assert missing == {
        "ja": ["refresh"],
        "ko": ["refresh"],
        "fr": ["refresh"],
        "ar": ["refresh"],
        "de": ["refresh"],
        "ru": ["refresh"],
    }


def test_apply_translations_to_source_inserts_without_reformatting() -> None:
    updates = {
        "ja": {"refresh": "更新"},
        "de": {"refresh": "Aktualisieren"},
    }

    updated = MODULE.apply_translations_to_source(SAMPLE_SOURCE, "I18N", updates)

    assert '    refresh: "更新",' in updated
    assert '    refresh: "Aktualisieren",' in updated
    assert 'title: "Trace Viewer"' in updated
    assert 'copy: "Copy"' in updated


def test_main_dry_run_exits_without_api_key(tmp_path: Path, capsys) -> None:
    test_file = tmp_path / "viewer.html"
    test_file.write_text(SAMPLE_SOURCE, encoding="utf-8")

    code = MODULE.main(["--dry-run", "--file", str(test_file), "--object-name", "I18N"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Dry run: missing keys that would be translated" in out
    assert "- ja: planned 1 key(s)" in out
    assert test_file.read_text(encoding="utf-8") == SAMPLE_SOURCE
