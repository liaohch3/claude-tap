# AGENTS 索引

本文件是贡献者规则的入口。详细策略文本位于 `docs/standards/*.md`。

## Pre-commit Hook

项目提供了 `.githooks/pre-commit` 自动在 commit 前运行 lint 检查。首次 clone 后执行：

```bash
git config core.hooksPath .githooks
```

## 不可协商规则

以下规则是强制性的，并会在 review 中执行：

1. 每次 commit 前运行 gate 检查：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest tests/ -x --timeout=60`。启用 pre-commit hook 可自动执行 lint 检查。
2. UI 变更要求在 PR 中提供使用 `raw.githubusercontent.com` 绝对 URL 的截图。
3. 每个 commit 只处理一个关注点（不要在同一 commit 中混合 refactor 与 feature/fix）。
4. 代码/注释/文档/commit 仅使用英文，`README_zh.md` 除外。
5. 证据必须使用 `.traces/` 中的真实 trace 数据（禁止合成 mock 截图/演示）。
6. 编码前必须执行 pre-work checklist，开 PR 前必须执行 pre-PR checklist。
7. 不要留下仅本地存在的工作；你必须执行 `git add`、`git commit` 和 `git push`。
8. 你必须使用 `gh pr create` 打开 GitHub PR。

## 标准目录

- 硬性规则与仓库策略：`docs/standards/hard-rules.md`
- 验证 gate 与必需命令：`docs/standards/validation-and-gates.md`
- E2E 与截图证据要求：`docs/standards/e2e-and-evidence.md`
- 截图采集与验证标准：`docs/standards/screenshot-standards.md`
- 编码与运行时安全规则：`docs/standards/coding-and-runtime.md`
- 工作流、review 与 Brain/Hands 协议：`docs/standards/workflow-and-review.md`
- 调试方法论与反模式：`docs/standards/debugging-standards.md`
- 客户端支持矩阵与 URL 构造规则：`docs/support-matrix.md`
- 标准文档元数据与维护流程：`docs/standards/README.md`

## Skills 目录

可复用的 agent 技能位于 `.agents/skills/`，按功能分类：

### 测试
- `e2e-test`：pytest E2E 测试套件
- `real-e2e-test`：真实 Claude CLI E2E 测试（pytest + tmux 模式）
- `js-in-html-testing`：HTML 内嵌 JS 的两层测试策略（Python 单测 + Playwright）

### 验证
- `legibility-check`：文档结构、标准 freshness、manifest 路径、plan 状态检查
- `screenshot-validation`：截图质量 + viewer HTML 渲染验证
- `pr-preflight`：PR 合并就绪全面检查（lint + test + CI + 截图）

### 翻译
- `translate-i18n`：自动补全 viewer I18N 缺失翻译（via OpenRouter）

### 发布
- `push-release`：推送代码并按需 bump 版本触发 PyPI 发布

### 资产生成
- `demo-video`：从真实 E2E 运行录制演示视频
- `playwright-screen-recording`：Playwright 录屏用于 PR review

## Scripts 目录

`scripts/` 下的确定性脚本，部分已被 skill 包装：

| 脚本 | 对应 Skill | 用途 |
|------|-----------|------|
| `check_legibility.py` | `legibility-check` | 文档可读性检查（CI: `legibility.yml`） |
| `check_changelog.py` | `legibility-check` | publish 阶段校验 release tag 与 `CHANGELOG.md` 覆盖 |
| `update_changelog.py` | `legibility-check` | auto-release 发 tag 前自动补齐 `CHANGELOG.md` release section，分支保护下会走 auto-merge release PR |
| `check_screenshots.py` | `screenshot-validation` | 截图质量检查（CI: `ci.yml`） |
| `check_screenshots.sh` | `screenshot-validation` | 检查 git staged 图片的 shell 包装 |
| `verify_screenshots.py` | `screenshot-validation` | Playwright viewer HTML 渲染验证 |
| `check_pr.sh` | `pr-preflight` | PR 合并就绪检查 |
| `translate_i18n.py` | `translate-i18n` | 自动翻译 i18n 缺失 key |
| `run_real_e2e.sh` | `real-e2e-test` | 真实 E2E（非 tmux） |
| `run_real_e2e_tmux.sh` | `real-e2e-test` | 真实 E2E（tmux 交互模式） |

## 可读性检查

确定性的可读性检查由 `scripts/check_legibility.py` 实现，并在 CI 中通过 `.github/workflows/legibility.yml` 运行。
