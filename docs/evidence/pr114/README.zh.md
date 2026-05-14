# PR 114 证据

此证据记录了该 PR 分支中新 `update` 子命令的一次真实 dry run：

```bash
uv run claude-tap update --installer pip --dry-run
```

该命令只打印前台 pip 升级命令，不执行网络升级。

