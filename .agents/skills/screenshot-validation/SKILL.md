---
name: screenshot-validation
description: Validate screenshot and viewer HTML quality for PR evidence. Run this after adding or modifying images under docs/evidence/ or docs/recordings/, or after generating a new viewer HTML file. Combines image quality checks (resolution, blankness, file size) with Playwright-based viewer rendering verification.
user_invocable: true
---

# Screenshot Validation

Validate that evidence images and viewer HTML files meet quality standards before committing. This catches issues that would otherwise fail CI or produce misleading PR evidence.

## Image quality check

Checks PNG/JPG/GIF/WEBP files for:
- **Minimum dimensions**: 400x400 pixels (hard fail)
- **Desktop viewport width**: >= 1280px (warning if narrower)
- **File size**: <= 5MB (warning if larger)
- **Blankness detection** (PNG only): fails if > 90% of pixels are white/transparent

### Run on specific files or directories

```bash
uv run python scripts/check_screenshots.py docs/evidence/
uv run python scripts/check_screenshots.py docs/recordings/
uv run python scripts/check_screenshots.py path/to/specific-image.png
```

### Run on git-staged images

```bash
scripts/check_screenshots.sh
```

This shell wrapper automatically finds staged PNG/JPG files and runs the quality check on them — useful as a pre-commit sanity check.

## Viewer HTML rendering verification

Uses Playwright (headless Chromium) to verify that generated viewer HTML files actually render correctly — not just raw JSON or Python errors.

Checks:
- No JavaScript errors on page load
- Sidebar element exists and has entries
- Detail panel exists
- Body text doesn't contain raw JSON dumps or Python tracebacks

### Run

```bash
uv run python scripts/verify_screenshots.py .traces/trace_*.html
```

Requires Playwright to be installed (`uv pip install playwright && playwright install chromium`).

## Typical workflow

After generating new evidence for a PR:

```bash
# 1. Check image quality
uv run python scripts/check_screenshots.py docs/evidence/

# 2. If you generated new viewer HTML, verify it renders
uv run python scripts/verify_screenshots.py .traces/trace_*.html

# 3. If all passes, stage and commit
git add docs/evidence/
```

## Fixing common failures

| Failure | Fix |
|---------|-----|
| `very small image (WxH; minimum is 400x400)` | Retake screenshot at a larger viewport or higher resolution |
| `narrow desktop viewport (Wpx < 1280px)` | Resize browser window to >= 1280px wide before capturing |
| `mostly blank/white image` | Ensure the screenshot captures actual content, not an empty page |
| `No sidebar — viewer not rendered` | Viewer HTML is broken; regenerate from trace JSONL |
| `JS errors` | Check viewer.html for syntax errors in embedded data |
