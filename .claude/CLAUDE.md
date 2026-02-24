## 提交前必须通过本地 CI

每次 `git commit` 之前，必须先跑通以下检查（和 GitHub CI 一致）：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

三项全过才能 commit。如果 format 不过，先 `uv run ruff format .` 修复再提交。
