# Viewer i18n Source Split Evidence

- Source trace: `.traces/codex-tools-20260509/2026-05-09/trace_081725.jsonl`
- Generated viewer: `/tmp/claude-tap-viewer-i18n-source.html`
- Screenshot: `viewer-i18n-source-codex-trace.png`
- Browser assertions: sidebar rendered, i18n bootstrap script embedded, and System Prompt, Messages, Tools, and Response sections were visible.
- Validation:
  - `uv run python scripts/check_screenshots.py .agents/evidence/pr/viewer-i18n-source`
  - `uv run python scripts/verify_screenshots.py /tmp/claude-tap-viewer-i18n-source.html`
