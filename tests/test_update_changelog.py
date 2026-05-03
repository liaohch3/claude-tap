#!/usr/bin/env python3
"""Unit tests for scripts/update_changelog.py."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "update_changelog.py"
MODULE_NAME = "update_changelog"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_render_release_section_uses_commit_subjects() -> None:
    module = _load_module()

    section = module.render_release_section(
        "0.1.40",
        dt.date(2026, 5, 3),
        ["fix: derive release versions from git tags", "docs: update changelog"],
    )

    assert "## [0.1.40] - 2026-05-03" in section
    assert "- fix: derive release versions from git tags" in section
    assert "- docs: update changelog" in section


def test_render_release_section_handles_empty_subjects() -> None:
    module = _load_module()

    section = module.render_release_section("0.1.40", dt.date(2026, 5, 3), [])

    assert "- Maintenance release." in section


def test_insert_release_section_after_unreleased() -> None:
    module = _load_module()
    changelog = "# Changelog\n\n## [Unreleased]\n\n## [0.1.39] - 2026-05-02\n"

    updated = module.insert_release_section(changelog, "## [0.1.40] - 2026-05-03\n\n### Changed\n- test\n")

    assert updated.index("## [Unreleased]") < updated.index("## [0.1.40]")
    assert updated.index("## [0.1.40]") < updated.index("## [0.1.39]")
