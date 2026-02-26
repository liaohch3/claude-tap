# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Forward proxy mode with HTTP `CONNECT` tunneling and TLS termination.
- Real E2E scripts with tmux support for interactive and non-interactive flows.
- Engineering practice and compounding-engineering documentation for agent workflows.

### Changed
- CI and test hardening for real proxy/E2E scenarios and Python 3.13 certificate validation.
- Real E2E fixtures and OAuth preflight handling were stabilized.

## [0.1.12] - 2026-02-25

### Added
- Sidebar task-type coloring and live-mode detail-scroll reset fix.

### Changed
- Viewer UX improvements: non-blocking browser open, sidebar timestamps, and scroll preservation.
- Task fingerprinting now uses the full system prompt instead of only the first line.
- Import order cleanup to satisfy ruff lint rules.

### Contributors
- WEIFENG2333 (#3, #4, #5, #6)

## [0.1.11] - 2026-02-25

### Changed
- Packaging and release progression toward the 0.1.12 viewer/community update series.

## [0.1.10] - 2026-02-25

### Changed
- Packaging and release progression toward the 0.1.12 viewer/community update series.

## [0.1.9] - 2026-02-25

### Fixed
- Removed 1MB request body size limit in proxy mode.

## [0.1.8] - 2026-02-24

### Added
- `--tap-host` flag to configure bind address.

## [0.1.7] - 2026-02-24

### Fixed
- Diff navigation button boundary logic in the viewer.
- aiohttp server noise in terminal output.
- Natural-language message rendering compatibility by using `div.pre-text`.

### Changed
- CI: auto-publish to PyPI on push to `main`.
- Repository policy documentation for local pre-commit checks.

## [0.1.6] - 2026-02-21

### Added
- Mobile responsive viewer improvements.
- Mobile previous/next request navigation.
- Diff fallback warning and manual diff-target selector.
- Smart update check and trace cleanup improvements.

### Fixed
- Keyboard/mobile navigation now follows visual sidebar order.
- Diff matching robustness for subagent-thread detection:
  - Strip `cache_control` from message hash inputs.
  - Increase message-hash truncation length for better separation.

## [0.1.5] - 2026-02-18

### Added
- `claude-tap export` command to export trace JSONL to Markdown or JSON format.
- `--tap-live` flag for SSE-based real-time trace viewer.
- `--tap-live-port` flag to choose the live-viewer port.
- `--tap-open` flag to auto-open HTML viewer after exit.
- Token summary bar with input/output/cache_read/cache_write breakdown.
- `py.typed` marker file for PEP 561 support.
- Coverage configuration in `pyproject.toml`.
- This `CHANGELOG.md` file.

### Changed
- Refactored monolithic `__init__.py` into focused modules (`sse.py`, `trace.py`, `live.py`, `proxy.py`, `viewer.py`, `cli.py`).
- Migrated tests to pytest with a structured `tests/` layout.
- Entry point changed to `claude_tap.cli:main_entry` (public API unchanged).

### Removed
- `anthropic` dependency (SSE reassembly uses built-in implementation).
- Cost estimation feature (pricing data maintenance overhead).

## [0.1.4] - 2026-02-16

### Added
- `--tap-live` real-time viewer with SSE updates.

### Changed
- Viewer UI improvements for image rendering, file path display, and live-mode behavior.

## [0.1.3] - 2026-02-16

### Added
- `-v/--version` CLI flag.
- PyPI badges in README.
- Pre-commit hooks configuration.
- pytest-based test infrastructure.

### Changed
- Applied ruff formatting to all Python files.

## [0.1.2] - 2026-02-15

### Added
- Structural diff view in HTML viewer.
- Side-by-side comparison for consecutive requests.
- Turn ordering fix.

## [0.1.1] - 2026-02-15

### Fixed
- Stdout buffering issue with uv tool.
- Transparent argument passthrough to claude.

## [0.1.0] - 2026-02-15

### Added
- Initial release.
- Local reverse proxy for Claude Code API requests.
- JSONL trace recording.
- Self-contained HTML viewer with:
  - Light/dark mode
  - i18n support (8 languages)
  - Token usage display
  - SSE event inspection
  - System prompt viewing
  - cURL export
