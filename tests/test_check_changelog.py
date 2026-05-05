#!/usr/bin/env python3
"""Unit tests for scripts/check_changelog.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_changelog.py"
MODULE_NAME = "check_changelog"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_tag_accepts_semver_release_tags() -> None:
    module = _load_module()

    assert module.normalize_tag("v0.1.40") == "0.1.40"


def test_normalize_tag_rejects_non_release_tags() -> None:
    module = _load_module()

    assert module.normalize_tag("0.1.40") is None
    assert module.normalize_tag("v0.1.40rc1") is None
    assert module.normalize_tag("release-0.1.40") is None


def test_changelog_versions_reads_keep_a_changelog_headings() -> None:
    module = _load_module()
    text = """# Changelog

## [Unreleased]

## [0.1.40] - 2026-05-03

## [0.1.39]
"""

    assert module.changelog_versions(text) == {"0.1.40", "0.1.39"}
