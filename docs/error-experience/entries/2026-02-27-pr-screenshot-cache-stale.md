# GitHub Raw URL 导致 PR 截图缓存陈旧

**日期：** 2026-02-27
**标签：** github, pr, screenshot, cache, review

## 问题

向 PR 分支 push 更新后的截图后，PR 描述仍显示旧图，看起来像变更未生效。

## 根因

PR 描述引用了稳定不变文件名的 `raw.githubusercontent.com` 路径。
GitHub/CDN 会在一段时间内继续缓存这些 URL 的旧内容。

## 影响

- review 混乱：reviewer 误以为 PR 证据未更新。
- 增加沟通成本与反复手动刷新。

## 已实施修复

1. 生成带版本后缀的新图片文件名（`*-v2.png`）。
2. 更新 PR 描述中的图片链接，指向新文件名。
3. 确认 PR 引用已切换到版本化 URL。

## 预防规则

更新 PR 内嵌截图时，优先通过改文件名使用不可变图片 URL
（如 `before-v2.png`、`after-v3.png`），不要复用旧文件名。

## 验证 Checklist

- 新文件出现在 `Files changed`。
- PR markdown 链接指向带版本后缀的文件名。
- reviewer 无需强制刷新也能看到更新图片。
