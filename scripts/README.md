# Scripts

## `translate_i18n.py`

Translate missing i18n strings in `claude_tap/viewer.html` using OpenRouter.

It parses the `I18N` object, finds keys present in both `en` and `zh-CN` but missing in other supported languages (`ja`, `ko`, `fr`, `ar`, `de`, `ru`), and writes new translations back into the same object.

### Requirements

- Set `OPENROUTER_API_KEY` in your environment
- Default model: `google/gemini-2.5-flash`

### Usage

```bash
# Show missing keys only (no file changes)
python scripts/translate_i18n.py --dry-run

# Translate missing keys and update viewer.html in place
python scripts/translate_i18n.py

# Use a specific model
python scripts/translate_i18n.py --model google/gemini-2.5-flash
```

### Advanced usage

```bash
# Use a custom file/object name (future CLI i18n support)
python scripts/translate_i18n.py --target cli --dry-run
python scripts/translate_i18n.py --file claude_tap/cli.py --object-name I18N --dry-run
```

## `check_changelog.py`

Ensure release tags are documented in `CHANGELOG.md`.

Publish checks the exact tag being published.

### Usage

```bash
# Check latest release tag known to git
python scripts/check_changelog.py

# Check an explicit release tag
python scripts/check_changelog.py --tag v0.1.40
```

## `update_changelog.py`

Insert a release section in `CHANGELOG.md` when one is missing.

Auto-release uses this before tagging so normal feature/fix PRs are not blocked by changelog bookkeeping. If the main branch is protected, auto-release opens a changelog PR, enables auto-merge, and publishes after that PR is merged.

### Usage

```bash
python scripts/update_changelog.py --version 0.1.40
python scripts/update_changelog.py --version 0.1.40 --date 2026-05-03
```
