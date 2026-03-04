#!/usr/bin/env python3
"""Fill missing i18n translations using OpenRouter."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"
LANG_ORDER = ["ja", "ko", "fr", "ar", "de", "ru"]

TARGET_CONFIG = {
    "viewer": {"file": "claude_tap/viewer.html", "object_name": "I18N"},
    # Future scope: if CLI gains i18n object, this target can be used directly.
    "cli": {"file": "claude_tap/cli.py", "object_name": "I18N"},
}


@dataclass
class ObjectBlock:
    source: str
    start: int
    end: int
    prefix: str
    body: str
    suffix: str


@dataclass
class LangBlock:
    lang: str
    span_start: int
    span_end: int
    body_start: int
    body_end: int
    body: str


def extract_object_block(source: str, object_name: str) -> ObjectBlock:
    pattern = re.compile(
        rf"(?P<prefix>(?:const|let|var)?\s*{re.escape(object_name)}\s*=\s*\{{)"
        rf"(?P<body>[\s\S]*?)"
        rf"(?P<suffix>^\s*\}};?\s*$)",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if not match:
        raise ValueError(f"Could not locate object '{object_name}' in source file.")
    return ObjectBlock(
        source=source,
        start=match.start(),
        end=match.end(),
        prefix=match.group("prefix"),
        body=match.group("body"),
        suffix=match.group("suffix"),
    )


def parse_lang_blocks(object_body: str) -> dict[str, LangBlock]:
    pattern = re.compile(
        r"^\s*(?P<name>\"[^\"]+\"|[A-Za-z0-9_-]+)\s*:\s*\{"
        r"(?P<body>[\s\S]*?)"
        r"^\s*\},\s*$",
        re.MULTILINE,
    )
    blocks: dict[str, LangBlock] = {}
    for match in pattern.finditer(object_body):
        raw_name = match.group("name")
        lang = raw_name[1:-1] if raw_name.startswith('"') and raw_name.endswith('"') else raw_name
        blocks[lang] = LangBlock(
            lang=lang,
            span_start=match.start(),
            span_end=match.end(),
            body_start=match.start("body"),
            body_end=match.end("body"),
            body=match.group("body"),
        )
    return blocks


def parse_lang_entries(lang_body: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    entry_pattern = re.compile(r"(?P<key>[A-Za-z0-9_]+)\s*:\s*\"(?P<value>(?:\\.|[^\"\\])*)\"")
    for match in entry_pattern.finditer(lang_body):
        key = match.group("key")
        raw_val = match.group("value")
        entries[key] = json.loads(f'"{raw_val}"')
    return entries


def collect_i18n_data(
    source: str, object_name: str
) -> tuple[ObjectBlock, dict[str, LangBlock], dict[str, dict[str, str]]]:
    object_block = extract_object_block(source, object_name)
    lang_blocks = parse_lang_blocks(object_block.body)
    lang_entries = {lang: parse_lang_entries(block.body) for lang, block in lang_blocks.items()}
    return object_block, lang_blocks, lang_entries


def find_missing_keys(lang_entries: dict[str, dict[str, str]], target_languages: list[str]) -> dict[str, list[str]]:
    if "en" not in lang_entries or "zh-CN" not in lang_entries:
        raise ValueError("Source i18n object must include both 'en' and 'zh-CN'.")

    en_keys = list(lang_entries["en"])
    zh_keys = set(lang_entries["zh-CN"])
    source_keys = [key for key in en_keys if key in zh_keys]
    missing: dict[str, list[str]] = {}
    for lang in target_languages:
        if lang not in lang_entries:
            continue
        lang_keys = set(lang_entries[lang])
        keys = [key for key in source_keys if key not in lang_keys]
        if keys:
            missing[lang] = keys
    return missing


def parse_json_response(text: str) -> dict[str, str]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object.")
    output: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Model response JSON must map string keys to string values.")
        output[key] = value
    return output


def request_openrouter_translation(
    api_key: str,
    model: str,
    lang: str,
    keys: list[str],
    en_map: dict[str, str],
    zh_map: dict[str, str],
    existing_lang_map: dict[str, str],
) -> dict[str, str]:
    request_items = [
        {
            "key": key,
            "en": en_map[key],
            "zh-CN": zh_map[key],
        }
        for key in keys
    ]

    fullwidth_examples: dict[str, dict[str, str]] = {}
    existing_examples = {
        key: value for key, value in existing_lang_map.items() if any(symbol in value for symbol in ("：", "！", "？"))
    }
    zh_examples = {key: value for key, value in zh_map.items() if any(symbol in value for symbol in ("：", "！", "？"))}
    if existing_examples:
        fullwidth_examples["target_existing"] = existing_examples
    if zh_examples:
        fullwidth_examples["zh-CN_reference"] = zh_examples

    prompt = {
        "task": "Translate missing UI i18n strings.",
        "context": "This is a developer tool (trace viewer) for inspecting LLM API calls.",
        "target_language": lang,
        "requirements": [
            "Return strict JSON object: key -> translated string.",
            "Do not include markdown or explanations.",
            "Preserve placeholders and symbols exactly (e.g., #, %s, {name}, ellipsis).",
            "Keep short UI label style and terminology consistent.",
        ],
        "existing_translations_for_consistency": existing_lang_map,
        "items_to_translate": request_items,
    }
    if lang in {"ja", "ko", "zh-CN"}:
        prompt["requirements"].append(
            "For ja/ko/zh-CN, preserve fullwidth punctuation style (e.g., ：！？) to match existing translations."
        )
        prompt["requirements"].append(
            "If zh-CN reference uses fullwidth punctuation for a key, mirror that punctuation width in translation."
        )
        if fullwidth_examples:
            prompt["fullwidth_punctuation_examples"] = fullwidth_examples

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "You are a precise software UI localization assistant. Output JSON only.",
            },
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False),
            },
        ],
    }

    request = Request(
        OPENROUTER_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/liaohch3/claude-tap",
            "X-Title": "claude-tap i18n helper",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=90) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenRouter request failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenRouter request failed: {exc.reason}") from exc

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenRouter response shape: {response_data}") from exc

    translations = parse_json_response(content)
    missing = [key for key in keys if key not in translations]
    if missing:
        raise RuntimeError(f"Model response missing keys for {lang}: {', '.join(missing)}")

    ordered = {key: translations[key] for key in keys}
    return normalize_punctuation_style(lang=lang, translations=ordered, zh_map=zh_map)


def normalize_punctuation_style(
    lang: str,
    translations: dict[str, str],
    zh_map: dict[str, str],
) -> dict[str, str]:
    if lang not in {"ja", "ko", "zh-CN"}:
        return translations

    replacements = {":": "：", "!": "！", "?": "？"}
    normalized: dict[str, str] = {}
    for key, value in translations.items():
        target = value
        zh_value = zh_map.get(key, "")
        for ascii_punc, fullwidth_punc in replacements.items():
            if fullwidth_punc in zh_value:
                target = target.replace(ascii_punc, fullwidth_punc)
        normalized[key] = target
    return normalized


def apply_translations_to_source(
    source: str,
    object_name: str,
    updates: dict[str, dict[str, str]],
) -> str:
    object_block, lang_blocks, _ = collect_i18n_data(source, object_name)
    body = object_block.body

    replacements: list[tuple[int, int, str]] = []
    for lang, translations in updates.items():
        if not translations:
            continue
        lang_block = lang_blocks.get(lang)
        if not lang_block:
            continue
        updated_lang_body = build_updated_lang_body(lang_block.body, translations)
        if updated_lang_body != lang_block.body:
            replacements.append((lang_block.body_start, lang_block.body_end, updated_lang_body))

    if not replacements:
        return source

    updated_body = body
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        updated_body = updated_body[:start] + replacement + updated_body[end:]

    updated_block = f"{object_block.prefix}{updated_body}{object_block.suffix}"
    return source[: object_block.start] + updated_block + source[object_block.end :]


def build_updated_lang_body(lang_body: str, translations: dict[str, str]) -> str:
    lines = lang_body.splitlines(keepends=True)
    key_line_indices = [i for i, line in enumerate(lines) if re.search(r"[A-Za-z0-9_]+\s*:\s*\"", line)]
    if not key_line_indices:
        return lang_body

    packed_style = any(len(re.findall(r"[A-Za-z0-9_]+\s*:\s*\"", lines[i])) > 1 for i in key_line_indices)
    last_key_idx = key_line_indices[-1]
    last_key_line = lines[last_key_idx]
    indent_match = re.match(r"(\s*)", last_key_line)
    indent = indent_match.group(1) if indent_match else "    "
    entries = [f"{key}: {json.dumps(value, ensure_ascii=False)}," for key, value in translations.items()]
    line_ending = "\r\n" if last_key_line.endswith("\r\n") else "\n" if last_key_line.endswith("\n") else ""

    if packed_style:
        inserted_line = f"{indent}{' '.join(entries)}{line_ending}"
        insert_at = last_key_idx + 1
        return "".join(lines[:insert_at] + [inserted_line] + lines[insert_at:])

    inserted_lines = "".join(f"{indent}{entry}{line_ending}" for entry in entries)
    insert_at = last_key_idx + 1
    return "".join(lines[:insert_at] + [inserted_lines] + lines[insert_at:])


def resolve_target(args: argparse.Namespace) -> tuple[Path, str]:
    if args.file:
        target_path = Path(args.file)
    else:
        target_path = Path(TARGET_CONFIG[args.target]["file"])

    object_name = args.object_name or TARGET_CONFIG[args.target]["object_name"]
    return target_path, object_name


def make_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate missing i18n keys with OpenRouter")
    parser.add_argument("--target", choices=sorted(TARGET_CONFIG), default="viewer", help="Translation target preset")
    parser.add_argument("--file", help="Override target file path")
    parser.add_argument("--object-name", help="Override JS/Python i18n object name")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model")
    parser.add_argument("--dry-run", action="store_true", help="Show missing keys without writing file")
    return parser


def print_summary(missing: dict[str, list[str]], translated: dict[str, list[str]], dry_run: bool) -> None:
    if not missing:
        print("No missing translations found.")
        return

    if dry_run:
        print("Dry run: missing keys that would be translated")
    else:
        print("Translation summary")

    for lang in LANG_ORDER:
        keys = missing.get(lang, [])
        if not keys:
            continue
        done = translated.get(lang, [])
        status = "planned" if dry_run else "translated"
        print(f"- {lang}: {status} {len(done or keys)} key(s)")
        for key in done or keys:
            print(f"  - {key}")


def main(argv: list[str] | None = None) -> int:
    parser = make_arg_parser()
    args = parser.parse_args(argv)

    target_path, object_name = resolve_target(args)
    if not target_path.exists():
        parser.error(f"Target file not found: {target_path}")

    source = target_path.read_text(encoding="utf-8")
    _, _, lang_entries = collect_i18n_data(source, object_name)
    missing = find_missing_keys(lang_entries, LANG_ORDER)

    if args.dry_run or not missing:
        print_summary(missing, {}, dry_run=True)
        return 0

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("OPENROUTER_API_KEY is required unless --dry-run is used")

    updates: dict[str, dict[str, str]] = {}
    translated_summary: dict[str, list[str]] = {}

    for lang in LANG_ORDER:
        keys = missing.get(lang, [])
        if not keys:
            continue
        print(f"Translating {len(keys)} key(s) for {lang}...")
        lang_update = request_openrouter_translation(
            api_key=api_key,
            model=args.model,
            lang=lang,
            keys=keys,
            en_map=lang_entries["en"],
            zh_map=lang_entries["zh-CN"],
            existing_lang_map=lang_entries[lang],
        )
        updates[lang] = lang_update
        translated_summary[lang] = list(lang_update)

    updated_source = apply_translations_to_source(source, object_name, updates)
    target_path.write_text(updated_source, encoding="utf-8")

    print_summary(missing, translated_summary, dry_run=False)
    print(f"Updated file: {target_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
