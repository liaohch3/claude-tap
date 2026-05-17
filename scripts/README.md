# Scripts

## `check_coverage.py`

Enforce project and incremental coverage targets for backend Python code and the
inline JavaScript and CSS in `claude_tap/viewer.html`.

Targets are configured in `pyproject.toml` under `[tool.claude_tap.coverage]`:

- Python project coverage: `python_total_min`
- Python changed executable package lines: `python_diff_min`
- Viewer JavaScript function coverage: `viewer_js_function_min`
- Viewer changed JavaScript functions: `viewer_js_diff_min`
- Viewer CSS selector coverage: `viewer_css_selector_min`
- Viewer changed CSS selectors: `viewer_css_diff_min`

### Usage

```bash
python -m coverage run -m pytest tests/ -q
python -m coverage json -o .coverage.json
python scripts/check_coverage.py --python-coverage .coverage.json
```

## `translate_i18n.py`

Translate missing i18n strings in `claude_tap/viewer_i18n.json` using OpenRouter.

It parses the viewer i18n JSON source, finds keys present in both `en` and `zh-CN` but missing in other supported languages (`ja`, `ko`, `fr`, `ar`, `de`, `ru`), and writes new translations back into the same file.

### Requirements

- Set `OPENROUTER_API_KEY` in your environment
- Default model: `google/gemini-2.5-flash`

### Usage

```bash
# Show missing keys only (no file changes)
python scripts/translate_i18n.py --dry-run

# Translate missing keys and update viewer_i18n.json in place
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
