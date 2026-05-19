"""Tests for deduplicated image assets in generated viewer HTML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.viewer import LAZY_THRESHOLD, _dedupe_record_image_assets, _generate_html_viewer

pw_missing = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
except ImportError:
    pw_missing = True

IMAGE_DATA = "aW1hZ2UtYXNzZXQtZGVkdXBlLXByb29m"
LARGE_IMAGE_DATA = "A" * 64_000


def _image_record(turn: int, image_data: str = IMAGE_DATA, media_type: str = "image/png") -> dict:
    return {
        "timestamp": "2026-05-19T10:00:00+00:00",
        "request_id": f"req_image_{turn}",
        "turn": turn,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "body": {
                "model": "claude-sonnet-4-6",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"image turn {turn}"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": image_data,
                                },
                            },
                        ],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "body": {
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    }


def _input_image_record(turn: int, image_data: str = IMAGE_DATA, media_type: str = "image/png") -> dict:
    return {
        "timestamp": "2026-05-19T10:00:00+00:00",
        "request_id": f"req_input_image_{turn}",
        "turn": turn,
        "duration_ms": 100,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "body": {
                "model": "gpt-5.5",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": f"input image turn {turn}"},
                            {
                                "type": "input_image",
                                "image_url": f"data:{media_type};base64,{image_data}",
                            },
                        ],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "body": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    }


def _write_trace(trace_path: Path, records: list[dict]) -> None:
    trace_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def test_image_asset_dedupe_rewrites_repeated_sources_to_refs() -> None:
    assets: dict[str, dict[str, str]] = {}
    record = _image_record(1)

    deduped = json.loads(_dedupe_record_image_assets(json.dumps(record), assets))

    source = deduped["request"]["body"]["messages"][0]["content"][1]["source"]
    assert source == {
        "type": "base64_ref",
        "asset_id": next(iter(assets)),
        "media_type": "image/png",
    }
    assert assets == {
        source["asset_id"]: {
            "media_type": "image/png",
            "data": IMAGE_DATA,
        }
    }


def test_image_asset_dedupe_rewrites_codex_input_image_data_urls_to_refs() -> None:
    assets: dict[str, dict[str, str]] = {}
    record = _input_image_record(1)

    deduped = json.loads(_dedupe_record_image_assets(json.dumps(record), assets))

    image_url = deduped["request"]["body"]["input"][0]["content"][1]["image_url"]
    assert image_url == {
        "type": "data_url_ref",
        "asset_id": next(iter(assets)),
        "media_type": "image/png",
    }
    assert assets == {
        image_url["asset_id"]: {
            "media_type": "image/png",
            "data": IMAGE_DATA,
        }
    }


def test_image_asset_dedupe_keeps_media_types_distinct() -> None:
    assets: dict[str, dict[str, str]] = {}

    _dedupe_record_image_assets(json.dumps(_image_record(1, IMAGE_DATA, "image/png")), assets)
    _dedupe_record_image_assets(json.dumps(_image_record(2, IMAGE_DATA, "image/jpeg")), assets)

    assert len(assets) == 2
    assert {asset["media_type"] for asset in assets.values()} == {"image/png", "image/jpeg"}


def test_html_export_deduplicates_inline_base64_images(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    _write_trace(trace_path, [_image_record(1), _image_record(2)])

    _generate_html_viewer(trace_path, html_path)

    html = html_path.read_text(encoding="utf-8")
    assert "const EMBEDDED_TRACE_ASSETS" in html
    assert html.count(IMAGE_DATA) == 1
    assert html.count('"type":"base64_ref"') == 2
    assert '"asset_id":"img_' in html


def test_html_export_deduplicates_codex_input_image_data_urls(tmp_path: Path) -> None:
    one_trace_path = tmp_path / "one.jsonl"
    one_html_path = tmp_path / "one.html"
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    records = [_input_image_record(turn, LARGE_IMAGE_DATA) for turn in range(1, 5)]
    _write_trace(one_trace_path, [_input_image_record(1, LARGE_IMAGE_DATA)])
    _write_trace(trace_path, records)

    _generate_html_viewer(one_trace_path, one_html_path)
    _generate_html_viewer(trace_path, html_path)

    html = html_path.read_text(encoding="utf-8")
    assert html.count(LARGE_IMAGE_DATA) == 1
    assert html.count('"type":"data_url_ref"') == len(records)
    assert html_path.stat().st_size - one_html_path.stat().st_size < len(LARGE_IMAGE_DATA)


def test_html_export_size_growth_is_not_linear_with_repeated_images(tmp_path: Path) -> None:
    one_trace_path = tmp_path / "one.jsonl"
    one_html_path = tmp_path / "one.html"
    repeated_trace_path = tmp_path / "repeated.jsonl"
    repeated_html_path = tmp_path / "repeated.html"
    repeated_records = [_image_record(turn, LARGE_IMAGE_DATA) for turn in range(1, 6)]

    _write_trace(one_trace_path, [_image_record(1, LARGE_IMAGE_DATA)])
    _write_trace(repeated_trace_path, repeated_records)
    _generate_html_viewer(one_trace_path, one_html_path)
    _generate_html_viewer(repeated_trace_path, repeated_html_path)

    one_html = one_html_path.read_text(encoding="utf-8")
    repeated_html = repeated_html_path.read_text(encoding="utf-8")

    assert repeated_trace_path.read_text(encoding="utf-8").count(LARGE_IMAGE_DATA) == len(repeated_records)
    assert repeated_html.count(LARGE_IMAGE_DATA) == 1
    assert repeated_html.count('"type":"base64_ref"') == len(repeated_records)
    assert repeated_html_path.stat().st_size - one_html_path.stat().st_size < len(LARGE_IMAGE_DATA)
    assert repeated_html_path.stat().st_size < repeated_trace_path.stat().st_size
    assert len(repeated_html) < len(one_html) + len(LARGE_IMAGE_DATA)


def test_html_export_deduplicates_lazy_base64_images(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    records = [_image_record(turn) for turn in range(LAZY_THRESHOLD + 2)]
    _write_trace(trace_path, records)

    _generate_html_viewer(trace_path, html_path)

    html = html_path.read_text(encoding="utf-8")
    assert "const EMBEDDED_TRACE_META" in html
    assert html.count(IMAGE_DATA) == 1
    assert html.count('"type":"base64_ref"') == LAZY_THRESHOLD + 2


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_html_viewer_renders_deduplicated_image_assets(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    _write_trace(trace_path, [_image_record(1)])
    _generate_html_viewer(trace_path, html_path)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=10000)
        page.wait_for_selector("#detail img", timeout=5000)

        src = page.locator("#detail img").first.get_attribute("src")
        hydrated_source = page.evaluate(
            "() => hydrateTraceAssets(entries[0].request.body).messages[0].content[1].source"
        )
        copied = page.evaluate(
            """async () => {
                window.__copiedText = [];
                writeClipboardText = text => {
                    window.__copiedText.push(text);
                    return Promise.resolve();
                };
                filtered = entries;
                activeIdx = 0;
                copyRequestBody(document.createElement('button'));
                copyCurl(document.createElement('button'));
                await new Promise(resolve => setTimeout(resolve, 0));
                return window.__copiedText;
            }"""
        )

        browser.close()

    assert src == f"data:image/png;base64,{IMAGE_DATA}"
    assert hydrated_source == {
        "type": "base64",
        "media_type": "image/png",
        "data": IMAGE_DATA,
    }
    assert len(copied) == 2
    assert '"type": "base64"' in copied[0]
    assert '"type": "base64_ref"' not in copied[0]
    assert IMAGE_DATA in copied[0]
    assert IMAGE_DATA in copied[1]
    assert "base64_ref" not in copied[1]


@pytest.mark.skipif(pw_missing, reason="playwright not installed")
def test_html_viewer_renders_deduplicated_codex_input_image_assets(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    html_path = tmp_path / "trace.html"
    _write_trace(trace_path, [_input_image_record(1)])
    _generate_html_viewer(trace_path, html_path)

    from playwright.sync_api import sync_playwright

    expected_src = f"data:image/png;base64,{IMAGE_DATA}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded", timeout=10000)
        page.wait_for_selector("#detail img", timeout=5000)

        src = page.locator("#detail img").first.get_attribute("src")
        hydrated_image_url = page.evaluate(
            "() => hydrateTraceAssets(entries[0].request.body).input[0].content[1].image_url"
        )
        copied = page.evaluate(
            """async () => {
                window.__copiedText = [];
                writeClipboardText = text => {
                    window.__copiedText.push(text);
                    return Promise.resolve();
                };
                filtered = entries;
                activeIdx = 0;
                copyRequestBody(document.createElement('button'));
                copyCurl(document.createElement('button'));
                await new Promise(resolve => setTimeout(resolve, 0));
                return window.__copiedText;
            }"""
        )

        browser.close()

    assert src == expected_src
    assert hydrated_image_url == expected_src
    assert len(copied) == 2
    assert expected_src in copied[0]
    assert '"type": "data_url_ref"' not in copied[0]
    assert expected_src in copied[1]
    assert "data_url_ref" not in copied[1]
