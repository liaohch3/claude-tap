# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


## [0.1.42] - 2026-05-04

### Changed
- docs: fix README accuracy (#107)
- Add missing skill names to local skill metadata (#93)
## [0.1.41] - 2026-05-03

### Fixed
- Make auto-release open and auto-merge a changelog pull request when the main
  branch is protected, then publish after that release PR is merged.

## [0.1.40] - 2026-05-03

### Changed
- Package versions are now derived from git tags via `setuptools-scm`, so local
  builds and PyPI releases use the same version source.
- PyPI publishing no longer mutates `pyproject.toml` during the release job.
- Auto-release can insert missing changelog sections before tagging, and publish
  still verifies that the exact tag being published is documented.

## [0.1.39] - 2026-05-02

### Fixed
- Surface Codex forward WebSocket responses in traces and viewer output.

## [0.1.38] - 2026-04-29

### Changed
- Warn on stateful Codex Responses continuations.

## [0.1.37] - 2026-04-29

### Fixed
- Unbreak `claude-tap` on Windows.

## [0.1.36] - 2026-04-28

### Fixed
- Handle null trace bodies during export.

## [0.1.35] - 2026-04-28

### Added
- Add HTML viewer output to the `export` command.

## [0.1.34] - 2026-04-27

### Fixed
- Hide auxiliary Bedrock setup calls in the viewer.

## [0.1.33] - 2026-04-27

### Fixed
- Decode Bedrock EventStream traces for viewer rendering.

## [0.1.32] - 2026-04-21

### Added
- Auto-detect Codex ChatGPT targets and force HTTP transport when needed.

## [0.1.31] - 2026-04-21

### Fixed
- Honor environment proxy settings for Codex forward WebSocket upstreams.

## [0.1.30] - 2026-04-20

### Fixed
- Relay WebSocket traffic in forward proxy mode.

## [0.1.29] - 2026-04-19

### Fixed
- Improve Codex proxy compatibility and WebSocket trace reconstruction.

## [0.1.28] - 2026-04-19

### Added
- Add focused pull request templates.

## [0.1.27] - 2026-03-26

### Fixed
- Trigger publishing via `workflow_dispatch` from auto-release.
- Move auto-release publishing out of inline tag-trigger assumptions.

## [0.1.26] - 2026-03-26

### Added
- Auto-release on every merge to `main`.

## [0.1.25] - 2026-03-26

### Fixed
- Collapse path filter chips to prevent viewer header overflow.

## [0.1.24] - 2026-03-21

### Fixed
- Compact viewer layout, merge token stats into the header, and fix the date picker.

## [0.1.23] - 2026-03-21

### Fixed
- Persist section collapse state across turns.
- Add cross-midnight trace cleanup handling.

## [0.1.22] - 2026-03-21

### Fixed
- Refactor live viewer SSE handling to deduplicate records and simplify naming.

## [0.1.21] - 2026-03-20

### Added
- Date-based trace storage with a date picker in the live viewer.

## [0.1.20] - 2026-03-19

### Added
- Support OpenAI Responses API traces in the viewer and SSE parser.

### Changed
- Migrate skills to directory-based `SKILL.md` format.
- Improve CLI `--help` with argument groups and examples.

### Fixed
- Add a proxy path allowlist to block scanner and crawler requests.
- Correct Codex upstream URL construction for OAuth and API-key modes.

## [0.1.19] - 2026-03-10

### Added
- Add Codex client support for proxy tracing.
- Add WebSocket proxy support for Codex CLI.
- Add automated PR merge-readiness checks.
- Add OpenRouter-backed i18n translation helper.
- Add MVP agent legibility checks and standards index.
- Add enhanced viewer search and large-trace performance improvements.

### Changed
- Translate internal markdown docs to zh-CN.
- Add screenshot quality standards and automated screenshot checks.

### Fixed
- Make PyPI publishing more robust with GitHub Release and PyPI verification.
- Make the diff overlay scrollable for long diffs.

## [0.1.18] - 2026-02-26

### Fixed
- Ignore `SIGTTOU` before reclaiming the foreground process group to prevent suspend on exit.

## [0.1.17] - 2026-02-26

### Fixed
- Read the package version from package metadata instead of a hardcoded string.

## [0.1.16] - 2026-02-26

### Added
- Graceful `Ctrl+C` / `Ctrl+Z` shutdown.
- Open the generated HTML viewer by default.

## [0.1.15] - 2026-02-26

### Changed
- Bump `.python-version` to 3.13 to match the CI matrix ceiling.

## [0.1.14] - 2026-02-26

### Added
- Document the Python 3.13 SSL AKI requirement in error-experience notes.

## [0.1.13] - 2026-02-26

### Added
- Forward proxy mode with HTTP `CONNECT` tunneling and TLS termination.
- Real E2E scripts with tmux support for interactive and non-interactive flows.
- Engineering practice and compounding-engineering documentation for agent workflows.

### Changed
- CI and test hardening for real proxy/E2E scenarios and Python 3.13 certificate validation.
- Replace the `AGENTS.md` symlink with a regular file.
- Real E2E fixtures and OAuth preflight handling were stabilized.

### Fixed
- Add SKI/AKI extensions to generated certificates for Python 3.13 SSL compatibility.

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
