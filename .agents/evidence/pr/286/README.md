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
