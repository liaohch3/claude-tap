# Contributing

Thanks for considering a contribution to `claude-tap`.

This project is a local proxy and trace viewer for AI coding clients. Changes can affect request routing, credential handling, trace files, and generated HTML output, so small, well-scoped pull requests are easiest to review.

## Start Here

1. Open an issue for bugs, feature requests, and behavior changes unless the fix is obvious.
2. Keep each pull request focused on one concern.
3. Include the commands you ran and any relevant trace, screenshot, or recording evidence.
4. Do not include private prompts, API keys, auth tokens, local file contents, or unredacted `.traces/` output in issues or pull requests.

Maintainer and automation-specific workflow notes live in `AGENTS.md`. External contributors do not need to follow agent-only steps such as opening PRs with `gh` or using repository skills.

## Development Setup

```bash
git clone https://github.com/liaohch3/claude-tap.git
cd claude-tap
uv sync --extra dev
```

Install the local CLI during development:

```bash
uv run python -m claude_tap --help
```

## Local Checks

Run the focused checks that match your change:

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run --extra dev pytest tests/ -x --timeout=60
```

For viewer or browser-facing changes, install Playwright browsers before running browser tests:

```bash
uv run playwright install chromium
uv run --extra dev pytest tests/test_nav_browser.py tests/test_responses_browser.py -x --timeout=60
```

Real end-to-end tests require a working Claude CLI or Codex CLI account and are opt-in:

```bash
uv run --extra dev pytest tests/e2e/ --run-real-e2e --timeout=300
```

## Pull Request Checklist

- Explain the problem and the user-visible behavior change.
- List validation commands and results.
- Add or update tests when behavior changes.
- Update `README.md`, `README_zh.md`, or `CHANGELOG.md` when user-facing behavior changes.
- Include screenshots or recordings for viewer UI changes.
- Redact private data from all trace evidence.

## Release Notes

Published versions are documented in `CHANGELOG.md`. If a change should appear in the next release, add it under `## [Unreleased]` or the release section requested by maintainers.

## Reporting Security Issues

Do not open a public issue for security-sensitive reports. Contact the maintainers privately before sharing exploit details, private traces, credentials, or other sensitive material.
