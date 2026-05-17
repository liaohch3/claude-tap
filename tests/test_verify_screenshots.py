#!/usr/bin/env python3
"""Unit tests for scripts/verify_screenshots.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from claude_tap.viewer import _generate_html_viewer

pytest.importorskip("playwright.sync_api")

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify_screenshots.py"
MODULE_NAME = "verify_screenshots"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_verify_viewer_html_accepts_explicit_empty_trace_state(tmp_path: Path) -> None:
    module = _load_module()
    trace_path = tmp_path / "empty.jsonl"
    html_path = tmp_path / "empty.html"
    trace_path.write_text("", encoding="utf-8")
    _generate_html_viewer(trace_path, html_path)

    issues = module.verify_viewer_html(str(html_path))

    assert issues == []


def test_verify_viewer_html_still_rejects_unloaded_file_picker(tmp_path: Path) -> None:
    module = _load_module()
    html_path = tmp_path / "viewer.html"
    html_path.write_text((Path(__file__).resolve().parent.parent / "claude_tap" / "viewer.html").read_text())

    issues = module.verify_viewer_html(str(html_path))

    assert "No sidebar entries — viewer empty" in issues
