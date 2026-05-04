---
name: translate-i18n
description: Fill missing i18n translations in the viewer HTML. Run this after adding or modifying English or Chinese UI strings in the I18N object inside claude_tap/viewer.html — it auto-translates to ja, ko, fr, ar, de, ru via OpenRouter.
user_invocable: true
---

# Translate i18n

Automatically fill missing translations for the viewer's `I18N` object. The script uses English and Chinese as source languages and translates to Japanese, Korean, French, Arabic, German, and Russian.

## Prerequisites

- `OPENROUTER_API_KEY` must be set in the environment (it is in the user's `.zshrc`)
- Default model: `google/gemini-2.5-flash`

## Workflow

### 1. Check what's missing (dry run)

Always preview first to confirm which keys need translation:

```bash
uv run python scripts/translate_i18n.py --dry-run
```

This parses the `I18N` object in `claude_tap/viewer.html`, finds keys present in both `en` and `zh-CN` but missing in other languages, and lists them without modifying the file.

### 2. Run the translation

```bash
uv run python scripts/translate_i18n.py
```

The script calls OpenRouter once per target language, then writes the translations back into `viewer.html` in-place — preserving the existing code style (indentation, line endings, packed vs expanded format).

### 3. Verify the result

After translation, run the formatter and tests to make sure nothing broke:

```bash
uv run ruff format claude_tap/viewer.html
uv run pytest tests/test_translate_i18n.py -v
```

## Options

| Flag | Purpose |
|------|---------|
| `--dry-run` | Show missing keys only, no file changes |
| `--model MODEL` | Override the OpenRouter model (default: `google/gemini-2.5-flash`) |
| `--target {viewer,cli}` | Translation target preset (default: `viewer`) |
| `--file PATH` | Override target file path |
| `--object-name NAME` | Override the JS/Python i18n object name |

## How it works

The script:
1. Extracts the `I18N` JavaScript object from the HTML file using regex
2. Parses each language block to find existing key-value pairs
3. Identifies keys present in `en` + `zh-CN` but missing in target languages
4. Sends a structured prompt to OpenRouter with existing translations for consistency
5. Normalizes fullwidth punctuation for CJK languages (matching zh-CN style)
6. Inserts new entries after the last existing key in each language block

## Common scenarios

**Added a new UI string**: Add the key to both `en` and `zh-CN` blocks in the `I18N` object, then run this skill. The other 6 languages will be filled automatically.

**Changed an existing string**: The script only fills *missing* keys. To re-translate an existing key, first delete it from the target language blocks, then run the script.
