# 本地 PR 自动 Review 机器人使用指南

本文档说明如何在本地运行 webhook 驱动的 PR review 机器人，并自动把审查结果回帖到 GitHub PR。

## 1. 前置条件

- Python 3.11+
- `uv`
- `git`
- `gh`（已登录并具备仓库 review/comment 权限）
- `tmux`
- 至少一个 review agent CLI：
  - `codex`（默认）
  - `claude`

可选：
- `npx`（用于自动启动 `smee-client`）

## 2. 安装与环境变量

在仓库根目录执行：

```bash
uv sync --extra review-bot --dev
```

设置环境变量：

```bash
export PR_REVIEW_WEBHOOK_SECRET="your_webhook_secret"
export PR_REVIEW_REPO_PATH="/absolute/path/to/this/repo"
export PR_REVIEW_AGENT="codex"   # or claude
export PR_REVIEW_OUTPUT_LANGUAGE="zh"  # zh or en
export PR_REVIEW_TIMEOUT="600"
export PR_REVIEW_PORT="3456"
```

可选变量：

```bash
export PR_REVIEW_IGNORE_USERS="github-actions[bot],dependabot[bot]"
export PR_REVIEW_LOG_FILE="/tmp/pr-review-bot.log"
export PR_REVIEW_ALLOW_INSECURE_WEBHOOKS="false"  # only for local debug
export SMEE_URL="https://smee.io/your-channel"
```

## 3. 配置 GitHub Webhook

在目标仓库中创建 webhook：

- Payload URL:
  - 直连本地时：`http://<your-host>:3456/webhook`
  - 使用 smee 时：`https://smee.io/<your-channel>`
- Content type: `application/json`
- Secret: 与 `PR_REVIEW_WEBHOOK_SECRET` 一致
- 事件：选择 `Pull requests`

机器人只处理以下 action：
- `opened`
- `synchronize`

## 4. 启动方式

前台运行：

```bash
scripts/start_review_bot.sh
```

后台运行：

```bash
scripts/start_review_bot.sh --daemon
```

手动 dry-run（验证导入与配置）：

```bash
python3 scripts/pr_review_bot.py --dry-run
```

健康检查：

```bash
curl -s http://127.0.0.1:${PR_REVIEW_PORT:-3456}/health
```

## 5. 运行流程

收到 PR webhook 后，机器人会：

1. 验证 `X-Hub-Signature-256`。
2. 过滤非 `pull_request` 事件或非 `opened/synchronize` action。
3. 忽略 bot 自身账号触发的 PR（避免循环）。
4. 同一 PR 新事件到达时取消旧任务，只保留最新任务。
5. 执行：
   - `git fetch origin +pull/<number>/head:pr-<number>`
   - `git fetch origin <base>`
   - `git diff origin/<base>...pr-<number>`
6. 根据 `prompts/pr-review-prompt.md` 生成 prompt。
7. 在 tmux session 中调用 `codex` 或 `claude` 完成 review。
8. 10 分钟超时控制，超时或失败会记录日志。
9. 根据输出包含的决策词执行：
   - `APPROVE` -> `gh pr review --approve`
   - `REQUEST_CHANGES` -> `gh pr review --request-changes`
   - 其他 -> `gh pr comment`

## 6. 日志与排障

默认日志文件：

- `/tmp/pr-review-bot.log`
- `/tmp/pr-review-bot-stdout.log`（daemon 模式）
- `/tmp/pr-review-bot-smee.log`（启用 smee 时）

常见问题：

1. webhook 返回 401  
   检查 `PR_REVIEW_WEBHOOK_SECRET` 与 GitHub Webhook Secret 是否一致。

2. review 未回帖  
   检查 `gh auth status`，确认 token 权限包含 PR review/comment。

3. agent 命令失败  
   确认 `codex` 或 `claude` CLI 可在终端直接执行。

4. tmux 报错  
   安装并验证 `tmux -V`。

5. smee 无转发  
   检查 `SMEE_URL` 是否正确，以及 `npx smee-client` 是否可运行。
