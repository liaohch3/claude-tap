# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


























## [0.1.116] - 2026-06-17

### Changed
- feat(viewer): add cache hit rate display in header stats bar
## [0.1.115] - 2026-06-15

### Changed
- feat: add Codex App transcript listener (#297)
## [0.1.114] - 2026-06-12

### Changed
- fix: decode double-serialized JSON request bodies
## [0.1.113] - 2026-06-12

### Changed
- docs: add Phistory ecosystem showcase (#321)
## [0.1.112] - 2026-06-12

### Changed
- fix: prevent Windows pip background update corruption (#319)
## [0.1.111] - 2026-06-12

### Changed
- feat: add Kimi Code tap client
## [0.1.110] - 2026-06-12

### Changed
- Add bulk session deletion and lazy dashboard paging (#298)
## [0.1.109] - 2026-06-12

### Changed
- perf: compact repeated message content in traces
## [0.1.108] - 2026-06-10

### Changed
- fix: render direct Gemini native traces (#312)
## [0.1.107] - 2026-06-10

### Changed
- fix: keep Gemini native traces visible
## [0.1.106] - 2026-06-10

### Changed
- feat: clarify shared dashboard lifecycle controls (#293)
## [0.1.105] - 2026-06-09

### Changed
- fix: guard against None values in usage token fields causing dashboard 500 (#303)
## [0.1.104] - 2026-06-09

### Changed
- Document local proxy upstream support
## [0.1.103] - 2026-06-08

### Changed
- Fix Codex display turn numbering (#266)
## [0.1.102] - 2026-06-06

### Changed
- Add capture-only prompt snapshot export
## [0.1.101] - 2026-06-03

### Changed
- Add dashboard session delete action (#236)
## [0.1.100] - 2026-06-02

### Changed
- feat: add AWS Bedrock support for Claude Code tracing
## [0.1.99] - 2026-06-02

### Changed
- refactor: split viewer assets (#271)
- docs: add agent trace viewer seo pages
## [0.1.98] - 2026-06-02

### Changed
- Fix dashboard first prompt extraction (#270)
## [0.1.97] - 2026-06-01

### Changed
- Add dashboard HTML export (#265)
## [0.1.96] - 2026-06-01

### Changed
- perf: compact repeated trace payload fields (#250)
- fix: hide Windows background subprocess consoles (#260)
## [0.1.95] - 2026-06-01

### Changed
- feat: add readable tool call parameters (#261)
- fix: support VSCode Claude wrapper browser controls (#244)
## [0.1.94] - 2026-05-31

### Changed
- Fix viewer session round grouping (#235)
## [0.1.93] - 2026-05-31

### Changed
- Refactor CLI module boundaries (#247)
## [0.1.92] - 2026-05-30

### Changed
- fix: count OpenAI usage when aliases are zero
### Fixed
- Count OpenAI-compatible `prompt_tokens` and `completion_tokens` when provider aliases report `input_tokens` and `output_tokens` as zero.

## [0.1.91] - 2026-05-30

### Changed
- perf: guard trace compaction and skip package noise (#227)
### Changed
- Skip persisted trace records for package registry metadata and archive downloads in forward proxy mode while still forwarding responses to clients.






## [0.1.90] - 2026-05-30

### Changed
- fix: render chat completion choices in viewer (#251)
## [0.1.89] - 2026-05-30

### Changed
- Fix Codex Responses viewer continuity (#245)
## [0.1.88] - 2026-05-30

### Changed
- Fix viewer global search navigation (#239)
## [0.1.87] - 2026-05-29

### Changed
- feat(viewer): support iframe embed query options (#246)
## [0.1.86] - 2026-05-28

### Changed
- feat!: omit raw stream events by default (#231)
### Changed
- **Breaking change:** raw SSE and WebSocket stream event arrays are no longer persisted by default. Pass `--tap-store-stream-events` when capturing a trace to store those raw event arrays in trace storage and viewer/export output; traces captured without the flag cannot recover the omitted raw events later.

## [0.1.85] - 2026-05-28

### Changed
- fix(codex): capture custom provider base URLs (#228)
### Fixed
- Capture Codex custom OpenAI-compatible providers by overriding the selected provider base URL, not only `openai_base_url`.










## [0.1.84] - 2026-05-26

### Changed
- feat: add sqlite trace history index (#210)
## [0.1.83] - 2026-05-26

### Changed
- ci: improve workflow caching and merge latency
## [0.1.82] - 2026-05-25

### Changed
- perf(ws): release websocket trace buffers without dropping trailing events
## [0.1.81] - 2026-05-25

### Changed
- feat: add CodeBuddy CLI client support (#190)
## [0.1.80] - 2026-05-24

### Changed
- fix(viewer): show message content block boundaries
## [0.1.79] - 2026-05-24

### Changed
- docs: document VS Code wrapper setup (#220)
## [0.1.78] - 2026-05-24

### Changed
- docs: add Codex review guidelines (#218)
- ui(viewer): 优化滚动条视觉效果和操作体验 (#206)
## [0.1.77] - 2026-05-24

### Changed
- fix: Make backports-zstd conditional for Python <3.14
## [0.1.76] - 2026-05-22

### Changed
- fix: unblock protected auto release (#208)
## [0.1.75] - 2026-05-22

### Added
- Add Antigravity CLI (agy) client support (#197).
- Add the session dashboard for browsing saved trace sessions (#200).
- Add `--tap-no-live` to disable the live viewer server and restore the pre-v0.1.75 default behavior for scripts, CI, remote shells, or other non-interactive runs.

### Changed
- **Breaking change:** the live viewer now starts by default when `claude-tap` runs a client, so users can watch trace records while the agent is still running (#192).
- `--tap-no-open` now prevents both the live viewer and the generated HTML viewer from auto-opening in a browser (#192).
- Add Gemini LLM paths to the primary viewer path filter (#196).
- Write completed WebSocket traces immediately (#195).
- Add trace log display support (#189).
- Enforce the PR policy gate in CI (#199).

## [0.1.74] - 2026-05-17

### Changed
- refactor: move viewer i18n strings to JSON source (#186)
## [0.1.73] - 2026-05-17

### Changed
- fix viewer empty trace evidence quality (#184)
## [0.1.72] - 2026-05-17

### Changed
- Add Qoder CLI support (#179)
## [0.1.71] - 2026-05-17

### Changed
- docs: expand messages in light viewer screenshot (#181)
## [0.1.70] - 2026-05-17

### Changed
- docs: refresh README and demo assets (#178)
## [0.1.69] - 2026-05-14

### Changed
- Detect Claude custom upstream target (#118)
## [0.1.68] - 2026-05-14

### Changed
- docs: show Anthropic Python SDK in proxy-only mode
## [0.1.67] - 2026-05-14

### Changed
- feat(viewer): add collapsible JSON tree view
## [0.1.66] - 2026-05-14

### Changed
- feat(cli): add update subcommand (#114)
## [0.1.65] - 2026-05-14

### Changed
- feat: support Pi CLI capture (#172)
## [0.1.64] - 2026-05-13

### Changed
- test(opencode): add real trace viewer evidence (#170)
### Added
- Add OpenCode real trace viewer evidence, OpenAI OAuth Responses evidence, and dedicated viewer contracts for multi-turn tool-call traces.

## [0.1.63] - 2026-05-13

### Changed
- test(viewer): add cross-client quality contracts (#168)
### Added
- Add cross-client HTML viewer contract tests for semantic sections, runtime errors, visual layout states, and V8 coverage of core inline JavaScript functions.
- Add backend/frontend project, incremental, and viewer CSS selector coverage targets enforced by CI.

## [0.1.62] - 2026-05-13

### Changed
- feat(cli): add Gemini CLI client (#166)
### Added
- Add Gemini CLI client support with forward proxy default.
- Render Gemini CLI system prompts, messages, tool calls, tool results, SSE output, and token usage in the viewer.





















## [0.1.61] - 2026-05-13

### Changed
- refactor(cli): support multiple reverse base URL envs (#160)
## [0.1.60] - 2026-05-11

### Changed
- add --tap-allow-path argument to support custom api prefixes (#122)
## [0.1.59] - 2026-05-11

### Changed
- feat(cli): add hermes-agent client (forward proxy by default) (#97)
## [0.1.58] - 2026-05-11

### Changed
- Fix dotted WebSocket turn ordering (#130)
## [0.1.57] - 2026-05-11

### Changed
- fix(viewer): improve diff targets and tool details (#148)
## [0.1.56] - 2026-05-11

### Changed
- fix(viewer): interleave Codex tool results (#151)
## [0.1.55] - 2026-05-09

### Changed
- fix(viewer): generalize Responses tool item normalization (#145)
## [0.1.54] - 2026-05-09

### Changed
- fix(viewer): show Codex cached tokens (#144)
## [0.1.53] - 2026-05-08

### Changed
- docs: strengthen Kimi evidence with real multiturn trace (#141)
## [0.1.52] - 2026-05-08

### Changed
- feat: add Kimi CLI client support (#139)
## [0.1.51] - 2026-05-08

### Changed
- Harden viewer metadata for Codex string bodies (#137)
## [0.1.50] - 2026-05-07

### Changed
- fix(viewer): clarify token summary label (#133)
## [0.1.49] - 2026-05-07

### Changed
- fix(viewer): count Responses function calls (#131)
## [0.1.48] - 2026-05-07

### Changed
- fix(proxy): support DeepSeek Claude Code metadata (#121)
## [0.1.47] - 2026-05-06

### Changed
- chore: split internal docs from public docs (#125)
- docs: collapse detailed README sections (#127)
## [0.1.46] - 2026-05-06

### Changed
- feat(cli): support cursor cli tracing (#119)
## [0.1.45] - 2026-05-05

### Changed
- feat(cli): add opencode client support
- fix(ci): use release bot token for auto release
## [0.1.44] - 2026-05-04

### Changed
- feat(cli): add standalone dashboard command
- fix(viewer): label Codex responses input as request context
## [0.1.43] - 2026-05-04

### Changed
- fix(cli): inject settings for Claude reverse proxy
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
