---
status: completed
---

# Codex Support Plan

Date: 2026-02-27

## Goal

Add first-class Codex support to `claude-tap` while keeping Claude behavior fully backward compatible.

## Scope

- Add client selection: `--tap-client {claude,codex}`.
- Keep default client as `claude`.
- For `codex` in reverse proxy mode, inject `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`.
- Keep existing Claude behavior: `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`.
- Set default upstream target by client when `--tap-target` is omitted:
  - `claude` -> `https://api.anthropic.com`
  - `codex` -> `https://api.openai.com`
- Preserve existing forward proxy behavior.
- Add trace metadata `upstream_base_url` to improve viewer cURL reconstruction.

## Non-Goals

- Rename package/project branding (`claude-tap`) in this change.
- Add broad provider abstractions beyond Claude/Codex.
- Guarantee Codex ChatGPT login flow under forward proxy mode.

## Test Strategy

- Unit / mock E2E regression:
  - `uv run pytest tests/test_e2e.py -x --timeout=120`
- New Codex mock E2E:
  - fake `codex` binary + fake upstream API
  - assert reverse-mode request path and `upstream_base_url` trace field
- Repo gate checks:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run pytest tests/ -x --timeout=60`

## Success Criteria

- Claude default workflow unchanged.
- `--tap-client codex` launches `codex` and writes valid trace output.
- Viewer copy-cURL uses `upstream_base_url` if present.
- No regressions in existing test suite.

## Real E2E Acceptance Rule

For Codex real E2E validation, HTTP `403/401` is treated as a hard failure.
A run is considered successful only when the end-to-end request completes with successful upstream responses.

## Risks

- Codex account scopes may block model-list or response APIs (`403`) even if proxy wiring is correct.
- Codex ChatGPT-web route traffic may not be fully covered by current forward mode assumptions.
