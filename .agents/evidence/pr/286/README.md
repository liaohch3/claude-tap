# PR 286 Kimi Code CLI Evidence

Real validation for `--tap-client kimi-code` (not fake upstream).

## Commands

```bash
cd /Users/civa/code/kimi-ws2/kimi-code-demo
claude-tap --tap-client kimi-code --tap-no-open --tap-no-update-check -- -p "Reply with exactly: KIMI_CODE_E2E_OK"
```

Direct control (no claude-tap):

```bash
kimi -p "Reply with exactly: KIMI_DIRECT_OK"
```

## Results

- `claude-tap --tap-client kimi-code`: exit 0, assistant text `KIMI_CODE_E2E_OK`, 1 API call captured
- Sandbox env: `KIMI_CODE_HOME` temp dir with patched `config.toml` `base_url` → `http://127.0.0.1:<port>`
- OAuth: `credentials/` and `oauth/` symlinked from real `~/.kimi-code`
- Migration prompt suppressed via `.skip-migration-from-kimi-cli` when `~/.kimi/.migrated-to-kimi-code` targets `~/.kimi-code`

## Trace source

- SQLite: `~/.local/share/claude-tap/traces.sqlite3`
- Example session: `3a09233f-f616-4f90-847a-d35178b7fa36` (`client=kimi-code`, `record_count=1`)
- Request path: `/chat/completions`, status `200`, upstream `https://api.kimi.com/coding/v1`

## Automated tests

```bash
uv run pytest tests/test_kimi_code_launch.py tests/test_e2e.py::test_kimi_code_client_reverse_proxy -x --timeout=60
```

## Maintainer validation

Conflict resolution and maintainer-side validation were run from a Linux worktree after rebasing the PR branch onto the latest `main`.

```bash
uv run --with ruff ruff check .
uv run --with ruff ruff format --check .
uv run --with pytest --with pytest-asyncio --with pytest-timeout pytest tests/ -x --timeout=60
uv run --with pytest --with pytest-asyncio pytest tests/test_client_config_framework.py tests/test_kimi_code_launch.py -q
env -u MOONSHOT_BASE_URL -u OPENAI_BASE_URL -u OPENROUTER_BASE_URL \
  uv run --with pytest --with pytest-asyncio pytest tests/test_kimi_launch.py tests/test_e2e.py -q -k 'kimi'
uv run --with pytest --with pytest-asyncio pytest tests/test_opencode_launch.py -q
```

Results:

- Full test suite: `696 passed, 25 skipped`
- Focused Kimi Code/client framework suite: `65 passed`
- Focused legacy Kimi suite: `9 passed`
- OpenCode launch suite: `8 passed`

Latest published Kimi Code CLI smoke check:

```bash
npm view @moonshot-ai/kimi-code version dist-tags.latest
npm exec --yes --package @moonshot-ai/kimi-code@latest -- kimi --version
PATH=/tmp/claude-tap-pr287-kimi-code-latest/node_modules/.bin:$PATH \
  KIMI_CODE_HOME=/tmp/claude-tap-pr287-kimi-smoke/home \
  uv run python -m claude_tap --tap-client kimi-code \
  --tap-output-dir /tmp/claude-tap-pr287-kimi-smoke/traces \
  --tap-no-live --tap-no-open -- --help
```

Results:

- Latest npm package: `@moonshot-ai/kimi-code@0.11.0`
- Latest `kimi --version`: `0.11.0`
- `claude-tap --tap-client kimi-code -- --help`: exit 0, with `KIMI_CODE_HOME` and `KIMI_CODE_BASE_URL` injected through the reverse-proxy sandbox
- A real prompt was attempted with the latest CLI, but the local Linux test environment has no Kimi Code OAuth/API key configured, so the CLI stopped before sending an upstream request. This validates launch/config wiring, while the real upstream request capture is covered by the contributor evidence above.
