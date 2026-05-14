---
owner: claude-tap-maintainers
last_reviewed: 2026-05-06
source_of_truth: AGENTS.md
---

# Pre-commit CI 检查

每次 commit 前运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

所有检查都必须通过。若格式检查失败，运行 `uv run ruff format .` 后重新检查。

# Coverage Targets

Coverage gates are configured in `pyproject.toml` under `[tool.claude_tap.coverage]`.

- Backend Python project coverage: at least `65%`.
- Backend Python incremental coverage: at least `80%` of changed executable package lines.
- Frontend viewer JavaScript function coverage: at least `50%` of V8-reported inline JS functions executed by the viewer contract suite.
- Frontend viewer JavaScript incremental coverage: at least `80%` of changed `viewer.html` JavaScript functions exercised by V8 coverage.
- Frontend viewer CSS selector coverage: at least `65%` of queryable CSS selectors must match real DOM states exercised by the viewer contract suite.
- Frontend viewer CSS incremental coverage: at least `80%` of changed queryable `viewer.html` CSS selectors must match exercised DOM states.

Run the deterministic coverage gate with:

```bash
python -m coverage run -m pytest tests/ -q
python -m coverage json -o .coverage.json
python scripts/check_coverage.py --python-coverage .coverage.json
```

# Meaningful Test Requirements

Coverage percentage is a floor, not the goal. Tests added only to execute lines
without proving behavior are not acceptable.

Every new or changed test must assert at least one meaningful contract:

- returned values, normalized data, status transitions, or error behavior for
  Python code;
- rendered DOM sections, visible semantic text, user interactions, browser
  runtime errors, V8-executed viewer functions, or CSS-backed layout and theme
  states for `viewer.html`;
- persisted files, trace records, or generated evidence when the change affects
  filesystem output.

Viewer tests must prefer generated HTML opened in Chromium for user-visible
behavior. `Full JSON` may be asserted as a fallback section, but a viewer test
must not treat `Full JSON` alone as successful semantic rendering.

Viewer style changes must prove more than screenshot existence. Tests should
assert durable layout and visual contracts such as desktop/mobile widths,
overflow bounds, expanded content dimensions, and light/dark theme differences.

# Pre-work Checklist

在进行任何代码变更之前：

```bash
git diff --stat
git log --oneline -10
git fetch origin
```

在打开或合并 PR 之前：

```bash
git rebase origin/main
uv lock --check
uv run pytest tests/ -x --timeout=60
```
