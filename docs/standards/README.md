---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# 标准元数据

`docs/standards/*.md` 下所有文件都必须包含 frontmatter，字段包括：

- `owner`：负责更新的团队或维护者。
- `last_reviewed`：最近一次策略审查的 ISO 日期 `YYYY-MM-DD`。
- `source_of_truth`：规范策略来源引用。

# 维护工作流

1. 更新受影响的标准文件，并刷新 `last_reviewed`。
2. 保持 `AGENTS.md` 为简洁索引，并链接到更新后的文件。
3. 本地运行 `python scripts/check_legibility.py`。
4. 若策略行为发生变化，在 PR 描述中记录理由。
