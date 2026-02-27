# Codex Support Handoff (Detailed)

Date: 2026-02-27
Branch: `feat/codex-client-support`
Repository: `liaohch3/claude-tap`

## 1. Goal, Scope, and Current Status

### Original Goal

Add support for Codex in addition to Claude, while preserving existing Claude behavior.

### Acceptance Constraint From User

Real Codex validation must be successful end-to-end. Any `403` is considered a hard failure.

### Current Status

- Core implementation for `--tap-client codex` is completed.
- Mock/unit/integration tests are passing locally.
- Real Codex run is still blocked by account/API permissions (`Missing scopes: api.model.read`) and upstream behavior in this environment.
- Work is committed on branch `feat/codex-client-support`.

## 2. What Was Done (and Why)

### A. CLI and Runtime Behavior

#### File: `claude_tap/cli.py`

Changes made:

- Added client selection support:
  - New flag `--tap-client` with values `claude|codex`, default `claude`.
- Extended launch path so we can run either `claude` or `codex`.
- Reverse proxy env injection is now client-specific:
  - Claude: `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>`
  - Codex: `OPENAI_BASE_URL=http://127.0.0.1:<port>/v1`
- `--tap-target` default is now derived from client when omitted:
  - Claude -> `https://api.anthropic.com`
  - Codex -> `https://api.openai.com`
- Updated user-facing startup/shutdown messages to reflect selected client.
- Preserved existing Claude forward-mode behavior, including `--settings` injection path only for Claude.

Reason:

- Existing implementation was Claude-only and hardcoded around Anthropic variables.
- Codex requires OpenAI-style base URL behavior in reverse mode.
- Keeping default `claude` preserves backward compatibility.

### B. Trace Model Enhancement for Viewer

#### File: `claude_tap/proxy.py`

Changes made:

- Added `upstream_base_url` into each trace record via `_build_record(...)`.
- Threaded `upstream_base_url` through streaming and non-streaming handlers.
- Simplified upstream encoding behavior by forcing `Accept-Encoding: identity` to avoid zstd-related client incompatibilities in this environment.

Reason:

- Viewer copy-curl was hardcoded to Anthropic domain; needed source-specific upstream reconstruction.
- Codex and some responses exhibited zstd decode failures in environment; identity encoding reduces this proxy-side compatibility risk.

### C. Forward Proxy Consistency

#### File: `claude_tap/forward_proxy.py`

Changes made:

- Also force `Accept-Encoding: identity` when forwarding upstream requests.

Reason:

- Keep behavior consistent with reverse proxy path and reduce compression-related failures.

### D. Viewer Behavior

#### File: `claude_tap/viewer.html`

Changes made:

- `copyCurl(...)` now uses `entry.upstream_base_url` when available.
- Falls back to `https://api.anthropic.com` for legacy traces.

Reason:

- Makes generated curl command accurate for Codex/OpenAI traces.
- Maintains backward compatibility for existing old trace files.

### E. Test Coverage Updates

#### File: `tests/test_e2e.py`

Changes made:

- Extended `_run_claude_tap(...)` helper with `tap_client` argument.
- `test_parse_args` now validates Codex defaults:
  - `--tap-client codex`
  - default target -> `https://api.openai.com`
- Added `test_codex_client_reverse_proxy` with fake `codex` executable:
  - fake codex uses `OPENAI_BASE_URL`
  - fake upstream expects `/v1/messages`
  - asserts trace contains expected path/model
  - asserts `upstream_base_url` is recorded
  - asserts startup output includes `OPENAI_BASE_URL=...`

Reason:

- Validate new client behavior without depending on real external credentials.
- Ensure no regressions in argument parsing and runtime wiring.

### F. Planning Docs

#### File: `docs/plans/2026-02-27-codex-support-plan.md`

Changes made:

- Added a scoped implementation plan and explicit acceptance/risk notes.

Reason:

- Follow repository guidance for plan documentation and clear handoff context.

## 3. What Was Not Done (or Not Fully Done)

### A. Real Codex E2E Success

Not achieved yet due environment/account limitations.

Observed blocker:

- Codex model refresh request failed:
  - `GET /v1/models?client_version=...`
  - `403 Forbidden`
  - message includes `Missing scopes: api.model.read`

Implication:

- Per user requirement, this means real validation is not complete.

### B. Dedicated `tests/e2e/` Real Codex Suite

Not added yet.

Reason:

- Given hard requirement that real run must succeed, adding real tests now would produce consistent failures in this environment until permissions are fixed.

### C. README / README_zh User-Facing Docs

Not updated in this round.

Reason:

- Priority was code path and correctness first.
- Follow-up should document new `--tap-client` behavior and examples.

## 4. Exact Test Execution and Results

The following were run locally and passed:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest tests/ -x --timeout=60`
  - Result: `48 passed, 18 skipped`

Additional targeted tests run:

- `uv run pytest tests/test_e2e.py -k "test_parse_args or test_codex_client_reverse_proxy" -x --timeout=120`
  - Passed
- `uv run pytest tests/test_e2e.py -k "test_e2e or test_forward_proxy_connect or test_codex_client_reverse_proxy" -x --timeout=120`
  - Passed

Real Codex smoke run attempted and failed (as expected in this env):

- Command pattern:
  - `uv run python -m claude_tap --tap-client codex --tap-target https://api.openai.com --tap-no-update-check --tap-no-open -- exec "Reply with exactly: CODEX_REAL_OK" --skip-git-repo-check --json`
- Failure indicators:
  - `403 Forbidden` with missing `api.model.read`
  - Codex exits non-zero

## 5. Working Tree Hygiene / Commit Scope

Important context:

- There was a pre-existing `uv.lock` modification before this work.
- `log/` contains runtime artifacts and is untracked.
- These were intentionally excluded from commit scope.

Files intended for this task commit:

- `claude_tap/cli.py`
- `claude_tap/proxy.py`
- `claude_tap/forward_proxy.py`
- `claude_tap/viewer.html`
- `tests/test_e2e.py`
- `docs/plans/2026-02-27-codex-support-plan.md`
- `docs/plans/2026-02-27-codex-support-handoff.md` (this file)

## 6. Recommended Next Actions for the Next Codex Process

### 1) Resolve Real Credential/Scope Blocker First

- Ensure API key/project/org has `api.model.read` (and required response scopes).
- Re-run real Codex smoke command to confirm non-403.

### 2) Add Real Codex E2E Coverage

Suggested additions:

- New test module under `tests/e2e/` for Codex real runs.
- Cover at least:
  - single turn success
  - multi-turn continuity
  - trace generation and path assertions
- Keep `403/401` as hard fail per user rule.

### 3) Update User Docs

- Update `README.md` and optionally `README_zh.md` with:
  - `--tap-client codex`
  - reverse mode example for Codex
  - known prerequisite: proper OpenAI scopes

### 4) Optional: Investigate zstd error path deeper

- Even after identity preference from proxy to upstream, Codex may still log zstd decode issues in failure scenarios.
- Verify whether this is tied to non-proxied traffic, fallback channels, or specific Codex internal endpoints.

## 7. Quick Technical Summary for Next Agent Prompt

If you need an abbreviated bootstrap prompt for another Codex process:

- Branch: `feat/codex-client-support`
- Core feature done: `--tap-client codex` + OpenAI reverse mode env wiring.
- Tests pass locally (`48 passed, 18 skipped`) for `tests/`.
- Real Codex still blocked by `403 missing scopes: api.model.read`.
- Continue by fixing credentials/scopes and adding real Codex E2E tests + README updates.

