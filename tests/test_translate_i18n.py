from __future__ import annotations

import importlib.util
import json
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


def test_find_missing_keys_keeps_en_block_order() -> None:
    source = """
const I18N = {
  en: {
    a: "A",
    z: "Z",
    b: "B",
  },
  "zh-CN": {
    a: "甲",
    z: "乙",
    b: "丙",
  },
  ja: {
    a: "エー",
  },
};
"""
    _, _, entries = MODULE.collect_i18n_data(source, "I18N")
    missing = MODULE.find_missing_keys(entries, ["ja"])
    assert missing["ja"] == ["z", "b"]


def test_apply_translations_preserves_packed_line_style() -> None:
    source = """
const I18N = {
  ja: {
    title: "トレースビューア", copy: "コピー",
  },
};
"""
    updates = {"ja": {"diff_select_target": "比較対象：", "diff_select_auto": "自動"}}
    updated = MODULE.apply_translations_to_source(source, "I18N", updates)
    assert '    title: "トレースビューア", copy: "コピー",' in updated
    assert '    diff_select_target: "比較対象：", diff_select_auto: "自動",' in updated


def test_request_openrouter_translation_adds_fullwidth_instruction_for_ja(monkeypatch) -> None:
    captured_payload: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            body = {"choices": [{"message": {"content": '{"diff_select_target":"比較対象："}'}}]}
            return json.dumps(body).encode("utf-8")

    def fake_urlopen(request, timeout=90):  # type: ignore[no-untyped-def]
        del timeout
        captured_payload.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr(MODULE, "urlopen", fake_urlopen)
    result = MODULE.request_openrouter_translation(
        api_key="test-key",
        model="test-model",
        lang="ja",
        keys=["diff_select_target"],
        en_map={"diff_select_target": "Compare against:"},
        zh_map={"diff_select_target": "对比对象："},
        existing_lang_map={"diff_select_target": "比較対象：", "copied": "コピー済み！"},
    )

    assert result["diff_select_target"] == "比較対象："
    user_prompt = json.loads(captured_payload["messages"][1]["content"])
    assert any("fullwidth punctuation style" in req for req in user_prompt["requirements"])
    assert user_prompt["fullwidth_punctuation_examples"]["target_existing"]["diff_select_target"] == "比較対象："
    assert user_prompt["fullwidth_punctuation_examples"]["zh-CN_reference"]["diff_select_target"] == "对比对象："


def test_normalize_punctuation_style_for_ko_uses_zh_cn_reference() -> None:
    normalized = MODULE.normalize_punctuation_style(
        lang="ko",
        translations={"diff_select_target": "비교 대상:", "copied": "복사됨!"},
        zh_map={"diff_select_target": "对比对象：", "copied": "已复制！"},
    )
    assert normalized["diff_select_target"] == "비교 대상："
    assert normalized["copied"] == "복사됨！"


def test_main_dry_run_exits_without_api_key(tmp_path: Path, capsys) -> None:
    test_file = tmp_path / "viewer.html"
    test_file.write_text(SAMPLE_SOURCE, encoding="utf-8")

    code = MODULE.main(["--dry-run", "--file", str(test_file), "--object-name", "I18N"])
    out = capsys.readouterr().out

    assert code == 0
    assert "Dry run: missing keys that would be translated" in out
    assert "- ja: planned 1 key(s)" in out
    assert test_file.read_text(encoding="utf-8") == SAMPLE_SOURCE
