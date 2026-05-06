# 工程实践指南

本文档对 claude-tap 项目的工程标准进行规范化。

## Python 代码风格

- **Linter/formatter：** [ruff](https://docs.astral.sh/ruff/)
- **行长限制：** 120 字符
- **目标版本：** Python 3.11+
- **Lint 规则：** `E`（errors）、`F`（pyflakes）、`W`（warnings）、`I`（import sorting）
- **忽略规则：** `E501`（行长，由 formatter 而非 lint 强制）

本地运行：
```bash
uv run ruff check .          # Lint
uv run ruff format --check . # Check formatting
uv run ruff format .         # Auto-fix formatting
```

## 测试策略

### 测试分层

| Layer | Location | 测试内容 | 在 CI 中运行 | 外部依赖 |
|-------|----------|----------|--------------|----------|
| **Unit** | `tests/test_diff_matching.py` | 纯逻辑（diff matching、parsing） | 是 | 无 |
| **Mock E2E** | `tests/test_e2e.py` | fake upstream + fake Claude 的完整 pipeline | 是 | 无 |
| **Browser integration** | `tests/test_nav_browser.py` | HTML viewer JavaScript 逻辑 | 是（使用 Playwright） | Playwright |
| **Real E2E** | `tests/e2e/` | 真实 Claude CLI 集成 | 否（opt-in） | Claude CLI、API key |

### 运行测试

```bash
# Full CI suite (unit + mock E2E)
uv run pytest tests/ -x --timeout=60

# Specific test file
uv run pytest tests/test_e2e.py -x --timeout=120

# Real E2E tests (requires claude CLI)
uv run pytest tests/e2e/ --run-real-e2e --timeout=300

# All tests including real E2E
uv run pytest tests/ --run-real-e2e --timeout=300
```

### 编写新测试

- 使用 `pytest` fixtures 做 setup/teardown（见 `conftest.py`）
- 用 `tempfile.mkdtemp()` 创建临时目录，并始终清理
- 对 async 测试，使用 `pytest-asyncio`（配置为 `asyncio_mode = "auto"`）
- 慢测试标记为 `@pytest.mark.slow`
- integration 测试标记为 `@pytest.mark.integration`

## Commit 约定

- commit message 使用英文
- 使用祈使语气：“add feature” 而非 “added feature”
- 使用类型前缀：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`
- subject 行保持在 72 字符以内
- 对非简单变更，在 body 解释“为什么”

示例：
```
feat: add --tap-host flag for custom bind address
fix: handle malformed SSE events without crashing
test: add real E2E tests with Claude CLI integration
docs: update engineering practices guide
```

## Pre-Work Checklist

进行任何代码变更之前：

1. **检查仓库状态：**
   ```bash
   git diff --stat
   git log --oneline -10
   ```
2. **确保工作树干净**，或先 stash 变更
3. **拉取最新 main：** `git fetch origin && git rebase origin/main`
4. **确认测试通过：** `uv run pytest tests/ -x --timeout=60`

## Feature 的 Worktree 工作流

使用 git worktree 进行隔离的 feature 开发：

```bash
# Create worktree for new feature
git worktree add -b feat/my-feature /tmp/claude-tap-my-feature main

# Work in the worktree
cd /tmp/claude-tap-my-feature
# ... make changes, run tests ...

# Merge back (fast-forward only)
cd /path/to/claude-tap
git merge --ff-only feat/my-feature

# Clean up
git worktree remove /tmp/claude-tap-my-feature
git branch -d feat/my-feature
```

收益：
- 主 worktree 保持干净
- 无需 stash 即可在多个 feature 间切换
- 自然隔离可防止相互污染

## Code Review 流程

commit 前：

1. **Lint：** `uv run ruff check .`
2. **Format：** `uv run ruff format --check .`
3. **Test：** `uv run pytest tests/ -x --timeout=60`
4. **Review diff：** `git diff`，逐行阅读每个变更
5. **验证范围：** 仅修改了与任务相关的文件吗？

合并 PR 前：

1. 所有 CI 检查通过
2. 无未解决 review 评论
3. 分支已 rebase 到最新 `main`
4. `uv.lock` 保持一致

## 语言

所有代码、注释、commit message、文档和 skill 文件必须使用英文。
唯一例外是 `README_zh.md` 以及其他明确为中文的 README 文件。
