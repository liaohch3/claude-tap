# CLI 中硬编码版本字符串

**日期：** 2026-02-26
**严重级别：** 中
**标签：** versioning, cli, metadata, release

## 问题

`cli.py` 中的 `__version__` 被硬编码为 `"0.1.7"`，发布时从未更新。
用户即使升级后，用 `-v` 看到的仍是错误版本。

## 根因

版本字符串是源码字面量，而不是从 package metadata 读取。

## 修复

将硬编码值替换为 `importlib.metadata.version("claude-tap")`，
使其始终与 `pyproject.toml` 和 PyPI package metadata 一致。

## 经验

不要硬编码版本字符串。始终使用 `importlib.metadata`（或其他单一真相源，例如从 `pyproject.toml` 动态读取）。
