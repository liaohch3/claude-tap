#!/usr/bin/env python3
"""Unit tests for scripts/check_screenshots.py."""

from __future__ import annotations

import binascii
import importlib.util
import struct
import sys
import zlib
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_screenshots.py"
MODULE_NAME = "check_screenshots"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", crc)


def _write_png(path: Path, width: int, height: int, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes(color) * width
    raw_scanlines = b"".join(b"\x00" + row for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    png = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def test_analyze_file_pass_for_wide_desktop_png(tmp_path: Path) -> None:
    module = _load_module()
    image = tmp_path / "wide.png"
    _write_png(image, width=1440, height=900, color=(80, 120, 160))

    result = module.analyze_file(image)

    assert result.status == "PASS"
    assert result.failures == []
    assert result.warnings == []


def test_analyze_file_warns_for_narrow_desktop_png(tmp_path: Path) -> None:
    module = _load_module()
    image = tmp_path / "narrow.png"
    _write_png(image, width=1024, height=900, color=(80, 120, 160))

    result = module.analyze_file(image)

    assert result.status == "WARN"
    assert any("narrow desktop viewport" in warning for warning in result.warnings)
    assert result.failures == []


def test_analyze_file_fails_for_very_small_png(tmp_path: Path) -> None:
    module = _load_module()
    image = tmp_path / "small.png"
    _write_png(image, width=399, height=600, color=(80, 120, 160))

    result = module.analyze_file(image)

    assert result.status == "FAIL"
    assert any("very small image" in failure for failure in result.failures)


def test_analyze_file_fails_for_mostly_blank_white_png(tmp_path: Path) -> None:
    module = _load_module()
    image = tmp_path / "blank.png"
    _write_png(image, width=1440, height=900, color=(255, 255, 255))

    result = module.analyze_file(image)

    assert result.status == "FAIL"
    assert any("mostly blank/white image" in failure for failure in result.failures)


def test_main_warns_when_no_images_are_found(tmp_path: Path, capsys) -> None:
    module = _load_module()
    empty_dir = tmp_path / "no-images"
    empty_dir.mkdir(parents=True, exist_ok=True)

    exit_code = module.main([str(empty_dir)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[WARN] No image files found for input paths." in output
