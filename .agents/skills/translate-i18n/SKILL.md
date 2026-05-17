---
name: translate-i18n
description: Fill missing i18n translations in the viewer source JSON. Run this after adding or modifying English or Chinese UI strings in claude_tap/viewer_i18n.json — it auto-translates to ja, ko, fr, ar, de, ru via OpenRouter.
user_invocable: true
---

# Translate i18n

Automatically fill missing translations for the viewer's `claude_tap/viewer_i18n.json` source file. The script uses English and Chinese as source languages and translates to Japanese, Korean, French, Arabic, German, and Russian.

## Prerequisites

- `OPENROUTER_API_KEY` must be set in the environment (it is in the user's `.zshrc`)
- Default model: `google/gemini-2.5-flash`

## Workflow

### 1. Check what's missing (dry run)

Always preview first to confirm which keys need translation:

```bash
uv run python scripts/translate_i18n.py --dry-run
```

This parses `claude_tap/viewer_i18n.json`, finds keys present in both `en` and `zh-CN` but missing in other languages, and lists them without modifying the file.

### 2. Run the translation

```bash
uv run python scripts/translate_i18n.py
```

The script calls OpenRouter once per target language, then writes the translations back into `viewer_i18n.json` in-place.

### 3. Verify the result

After translation, run the formatter and tests to make sure nothing broke:

```bash
uv run python -m json.tool claude_tap/viewer_i18n.json >/dev/null
uv run pytest tests/test_translate_i18n.py -v
```

## Options

| Flag | Purpose |
|------|---------|
| `--dry-run` | Show missing keys only, no file changes |
| `--model MODEL` | Override the OpenRouter model (default: `google/gemini-2.5-flash`) |
| `--target {viewer,cli}` | Translation target preset (default: `viewer`) |
| `--file PATH` | Override target file path |
| `--object-name NAME` | Override the legacy JS/Python i18n object name |

## How it works

The script:
1. Loads the viewer i18n JSON source file
2. Validates that every language block is a string-to-string map
3. Identifies keys present in `en` + `zh-CN` but missing in target languages
4. Sends a structured prompt to OpenRouter with existing translations for consistency
5. Normalizes fullwidth punctuation for CJK languages (matching zh-CN style)
6. Inserts new entries after the existing keys in each target language

## Common scenarios

**Added a new UI string**: Add the key to both `en` and `zh-CN` blocks in `claude_tap/viewer_i18n.json`, then run this skill. The other 6 languages will be filled automatically.

**Changed an existing string**: The script only fills *missing* keys. To re-translate an existing key, first delete it from the target language blocks, then run the script.
