#!/usr/bin/env python3
"""Screenshot quality checks for PR evidence images."""

from __future__ import annotations

import argparse
import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

MIN_DESKTOP_WIDTH = 1280
MIN_DIMENSION = 400
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
BLANK_DOMINANT_RATIO = 0.90
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass
class ImageInfo:
    width: int
    height: int
    format_name: str


@dataclass
class FileResult:
    path: Path
    info: ImageInfo | None = None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failures:
            return "FAIL"
        if self.warnings:
            return "WARN"
        return "PASS"


def iter_image_files(paths: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.add(path)
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.add(child)
    return sorted(files)


def parse_png_info(raw: bytes) -> ImageInfo:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    if raw[12:16] != b"IHDR":
        raise ValueError("missing PNG IHDR")
    width, height = struct.unpack(">II", raw[16:24])
    return ImageInfo(width=width, height=height, format_name="png")


def parse_jpeg_info(raw: bytes) -> ImageInfo:
    if len(raw) < 4 or raw[0:2] != b"\xff\xd8":
        raise ValueError("not a JPEG file")
    i = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while i + 9 < len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        i += 2
        while marker == 0xFF and i < len(raw):
            marker = raw[i]
            i += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > len(raw):
            break
        segment_length = struct.unpack(">H", raw[i : i + 2])[0]
        if segment_length < 2 or i + segment_length > len(raw):
            break
        if marker in sof_markers:
            if i + 7 > len(raw):
                break
            height, width = struct.unpack(">HH", raw[i + 3 : i + 7])
            return ImageInfo(width=width, height=height, format_name="jpeg")
        i += segment_length
    raise ValueError("could not parse JPEG dimensions")


def parse_gif_info(raw: bytes) -> ImageInfo:
    if len(raw) < 10 or raw[0:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("not a GIF file")
    width, height = struct.unpack("<HH", raw[6:10])
    return ImageInfo(width=width, height=height, format_name="gif")


def parse_webp_info(raw: bytes) -> ImageInfo:
    if len(raw) < 30 or raw[0:4] != b"RIFF" or raw[8:12] != b"WEBP":
        raise ValueError("not a WEBP file")
    chunk = raw[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        return ImageInfo(width=width, height=height, format_name="webp")
    if chunk == b"VP8 ":
        if len(raw) < 30:
            raise ValueError("WEBP VP8 too short")
        width, height = struct.unpack("<HH", raw[26:30])
        return ImageInfo(width=width & 0x3FFF, height=height & 0x3FFF, format_name="webp")
    if chunk == b"VP8L":
        if len(raw) < 25:
            raise ValueError("WEBP VP8L too short")
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return ImageInfo(width=width, height=height, format_name="webp")
    raise ValueError("unsupported WEBP chunk")


def parse_image_info(raw: bytes, extension: str) -> ImageInfo:
    extension = extension.lower()
    if extension == ".png":
        return parse_png_info(raw)
    if extension in {".jpg", ".jpeg"}:
        return parse_jpeg_info(raw)
    if extension == ".gif":
        return parse_gif_info(raw)
    if extension == ".webp":
        return parse_webp_info(raw)
    raise ValueError(f"unsupported image format: {extension}")


def _parse_png_chunks(raw: bytes) -> tuple[dict[str, bytes], bytes]:
    i = 8
    chunks: dict[str, bytes] = {}
    idat_parts: list[bytes] = []
    while i + 8 <= len(raw):
        length = struct.unpack(">I", raw[i : i + 4])[0]
        chunk_type = raw[i + 4 : i + 8].decode("ascii", errors="replace")
        data_start = i + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(raw):
            raise ValueError("corrupt PNG chunk data")
        data = raw[data_start:data_end]
        if chunk_type == "IDAT":
            idat_parts.append(data)
        elif chunk_type not in chunks:
            chunks[chunk_type] = data
        i = crc_end
        if chunk_type == "IEND":
            break
    return chunks, b"".join(idat_parts)


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_png_scanlines(filtered: bytes, width: int, height: int, bpp: int) -> list[bytes]:
    stride = width * bpp
    expected_len = height * (stride + 1)
    if len(filtered) != expected_len:
        raise ValueError("unexpected PNG IDAT decompressed length")

    rows: list[bytes] = []
    prior = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = filtered[offset]
        offset += 1
        current = bytearray(filtered[offset : offset + stride])
        offset += stride

        if filter_type == 1:
            for x in range(stride):
                left = current[x - bpp] if x >= bpp else 0
                current[x] = (current[x] + left) & 0xFF
        elif filter_type == 2:
            for x in range(stride):
                current[x] = (current[x] + prior[x]) & 0xFF
        elif filter_type == 3:
            for x in range(stride):
                left = current[x - bpp] if x >= bpp else 0
                current[x] = (current[x] + ((left + prior[x]) // 2)) & 0xFF
        elif filter_type == 4:
            for x in range(stride):
                left = current[x - bpp] if x >= bpp else 0
                up = prior[x]
                up_left = prior[x - bpp] if x >= bpp else 0
                current[x] = (current[x] + _paeth_predictor(left, up, up_left)) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"unsupported PNG filter type: {filter_type}")

        rows.append(bytes(current))
        prior = current
    return rows


def parse_png_dominant_color_ratio(raw: bytes) -> tuple[float, tuple[int, int, int, int]]:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    chunks, idat = _parse_png_chunks(raw)
    ihdr = chunks.get("IHDR")
    if ihdr is None or len(ihdr) != 13:
        raise ValueError("missing PNG IHDR chunk")

    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", ihdr)
    if compression != 0 or filter_method != 0:
        raise ValueError("unsupported PNG compression/filter method")
    if interlace != 0:
        raise ValueError("interlaced PNG is not supported")
    if bit_depth != 8:
        raise ValueError("only PNG bit depth 8 is supported")

    channels_by_color_type = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    channels = channels_by_color_type.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported PNG color type: {color_type}")

    palette: bytes | None = chunks.get("PLTE")
    if color_type == 3 and (palette is None or len(palette) % 3 != 0):
        raise ValueError("invalid or missing PNG palette")

    decompressed = zlib.decompress(idat)
    rows = _unfilter_png_scanlines(decompressed, width, height, channels)
    color_counts: Counter[tuple[int, int, int, int]] = Counter()

    for row in rows:
        for i in range(0, len(row), channels):
            px = row[i : i + channels]
            if color_type == 0:
                color = (px[0], px[0], px[0], 255)
            elif color_type == 2:
                color = (px[0], px[1], px[2], 255)
            elif color_type == 3:
                idx = px[0]
                p = idx * 3
                if palette is None or p + 2 >= len(palette):
                    raise ValueError("PNG palette index out of bounds")
                color = (palette[p], palette[p + 1], palette[p + 2], 255)
            elif color_type == 4:
                color = (px[0], px[0], px[0], px[1])
            else:
                color = (px[0], px[1], px[2], px[3])
            color_counts[color] += 1

    if not color_counts:
        raise ValueError("PNG has no pixels")

    dominant_color, dominant_count = color_counts.most_common(1)[0]
    total_pixels = width * height
    return dominant_count / total_pixels, dominant_color


def _is_blankish_color(color: tuple[int, int, int, int]) -> bool:
    red, green, blue, alpha = color
    return alpha <= 10 or (red >= 245 and green >= 245 and blue >= 245 and alpha >= 245)


def analyze_file(path: Path) -> FileResult:
    result = FileResult(path=path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        result.failures.append(f"cannot read file ({exc})")
        return result

    file_size = len(raw)
    if file_size > MAX_FILE_SIZE_BYTES:
        size_mb = file_size / (1024 * 1024)
        result.warnings.append(f"large file ({size_mb:.2f}MB > 5.00MB)")

    try:
        info = parse_image_info(raw, path.suffix)
        result.info = info
    except ValueError as exc:
        result.failures.append(str(exc))
        return result

    if info.width < MIN_DIMENSION or info.height < MIN_DIMENSION:
        result.failures.append(f"very small image ({info.width}x{info.height}; minimum is 400x400)")

    if info.width < MIN_DESKTOP_WIDTH:
        result.warnings.append(f"narrow desktop viewport ({info.width}px < 1280px)")

    if info.format_name == "png":
        try:
            ratio, dominant_color = parse_png_dominant_color_ratio(raw)
            if ratio > BLANK_DOMINANT_RATIO and _is_blankish_color(dominant_color):
                result.failures.append(f"mostly blank/white image (dominant color ratio {ratio:.1%} > 90%)")
        except ValueError as exc:
            result.warnings.append(f"blankness check skipped ({exc})")
    else:
        result.warnings.append("blankness check skipped (only PNG analysis is supported)")

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Image file(s) or directories to scan")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = iter_image_files(args.paths)
    if not files:
        print("[WARN] No image files found for input paths.")
        print("Summary: PASS=0 WARN=1 FAIL=0")
        return 0

    pass_count = 0
    warn_count = 0
    fail_count = 0

    for file_path in files:
        result = analyze_file(file_path)
        reasons = result.failures + result.warnings
        info_suffix = ""
        if result.info is not None:
            info_suffix = f" ({result.info.width}x{result.info.height})"

        if reasons:
            print(f"[{result.status}] {file_path}{info_suffix}: {'; '.join(reasons)}")
        else:
            print(f"[{result.status}] {file_path}{info_suffix}")

        if result.status == "PASS":
            pass_count += 1
        elif result.status == "WARN":
            warn_count += 1
        else:
            fail_count += 1

    print(f"Summary: PASS={pass_count} WARN={warn_count} FAIL={fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
