# Issue #474: reverse proxy bypass validation

## Change

All reverse clients reuse the existing `_extend_no_proxy` helper. Both case
variants retain inherited bypass entries and gain explicit localhost, IPv4,
and IPv6 loopback entries. Existing network proxy settings are preserved.

## Real socket regression

`tests/test_reverse_no_proxy.py` launches an actual subprocess through
`run_client`, sends an HTTP request through the real reverse handler, and
reads the resulting SQLite record. An independent HTTP proxy deliberately
returns 502 for misrouted requests. The upstream is a controlled local HTTP
fixture, not a real model. No HTTP client or subprocess launch is mocked;
only executable discovery points at the probe.

- Before the fix: the Claude case receives HTTP 502, and the assertion fails.
- After the fix: Claude, Codex, and Pi receive HTTP 200; the rejecting proxy
  receives zero requests; SQLite contains the successful request and response.
- The test asserts inherited entries from both variables survive, both
  case variants match, and the network proxy is unchanged.

## Authenticated real Codex run

Run on Linux on 2026-10-25, from `/tmp/claude-tap-474-20261025`, using
Codex CLI 0.159.0 and real ChatGPT OAuth. These are actual model responses,
not fixture responses. Each command ran in a dedicated tmux session.

Set all six uppercase/lowercase HTTP/HTTPS/ALL proxy variables to a local
CONNECT gateway, with deliberately conflicting bypass settings:

```text
NO_PROXY=upper.internal
no_proxy=172.31.*,127.*,localhost,lower.internal
```

The local CONNECT gateway returns HTTP 502 for loopback requests and forwards
remote connections over real TCP/TLS, recording destination hosts only.
The real upstream is `https://chatgpt.com/backend-api/codex`.

```bash
export CLOUDTAP_DB="$PWD/.traces/issue474-codex/traces.sqlite3"
uv run claude-tap --tap-client codex --tap-proxy-mode reverse \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-no-open --tap-no-live --tap-store-stream-events -- \
  exec -m gpt-6-luna -C /tmp/ct474-codex-workspace \
  --skip-git-repo-check --json --dangerously-bypass-approvals-and-sandbox \
  "Use separate shell calls to run pwd, list files, read project.toml, and write codex_report.txt."

uv run claude-tap --tap-client codex \
  --tap-target https://chatgpt.com/backend-api/codex \
  --tap-no-open --tap-no-live --tap-store-stream-events -- \
  exec resume 01a1734c-39f0-7b80-99e2-0397d8207aca -m gpt-6-luna \
  --skip-git-repo-check --json --dangerously-bypass-approvals-and-sandbox \
  "Read the report, compute its byte size, and assert only the six selected proxy environment variables; write followup.txt."
```

The prompts above are shortened for readability. The local real trace contains
the full prompts and actual tool calls. No credential contents were printed
or committed. All model-directed writes were restricted to the test workspace.

Results independently checked after the CLI completed:

- Both turns exited 0 and completed multiple real shell tool calls.
- The second turn resumed the exact first-turn Codex thread.
- The first turn wrote `codex_report.txt`; the second read its 416 bytes,
  asserted proxy settings inside a real child process, and wrote followup files.
- Both bypass variables equal
  `upper.internal,172.31.*,127.*,localhost,lower.internal,127.0.0.1,::1`.
- All four inspected HTTP/HTTPS proxy variables retain the test gateway URL.
- The network gateway recorded zero loopback requests, while real remote
  `chatgpt.com:443` connections went through it.
- First tap session `a065025e-0f43-4d56-b00d-a0230646f625`: 7 persisted
  HTTP 200 entries (2 models, 5 Responses).
- Resumed tap session `703be4a6-dbfa-4681-b5ce-c487faad6aec`: 6 persisted
  HTTP 200 entries (2 models, 4 Responses).

## Screenshot provenance

The trace store was read back and exported to real JSONL and HTML under
`.traces/issue474-codex/trace-first.*` and `trace-resume.*`. The HTML was served
on `http://127.0.0.1:39474/trace-resume.html` with Python's HTTP server.
Playwright selected the final real Responses entry, then scrolled to the
resumed user prompt and deeper tool/result content. Both screenshots show
the same actual viewer entry at different positions. No HTML or captured
trace records were fabricated or edited for these screenshots.

- `trace-viewer-resume.png`: resumed request and tool activity.
- `trace-viewer-resume-deep.png`: deeper tool execution and completion.
- Viewer checks: no browser JavaScript errors; both exported viewers pass
  `scripts/verify_screenshots.py`; images pass `scripts/check_screenshots.py`.
- Raw traces, generated viewer HTML, credentials, and full CLI logs stay local.

## Gates and limits

- Ruff lint and format checks pass.
- `uv lock --check` passes.
- Full suite: 1705 passed, 26 skipped, 34 warnings in 167.55 seconds.
- Focused networking/helper/MiMo suite: 25 passed.
- The default Codex model returned upstream HTTP 429 with
  `turn_rate_limit_exceeded` / capacity exhausted on two attempts. A model
  listed by the real models endpoint, `gpt-6-luna`, completed both turns.
- Claude's configured local backend was unavailable; a separate direct
  Anthropic attempt captured HTTP 401 stating the OAuth access token was
  invalid. This is not counted as a successful Claude conversation.
- This validates real inherited-variable routing on Linux and real Codex
  requests. It does not claim a full WSL2/Clash desktop reproduction or an
  authenticated Claude/Pi model conversation. The real-socket regression
  covers those launch paths; the POSIX probe is skipped on Windows.
