---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# 编码标准

## 应做

- 删除无用代码。
- 修复测试失败的根因。
- 使用现有模式，并将范围限制在相关文件。
- 信任类型不变量，避免对已类型化值进行冗余运行时检查。
- 保持函数聚焦单一职责。
- 在脚本中优先使用 POSIX shell 工具。
- 在脚本中使用 `grep -F` 做固定字符串匹配。
- 从 metadata 读取 package version，而不是硬编码字符串。

## 禁止

- 保留注释掉的代码。
- 添加猜测性的抽象。
- 无理由抑制 linter 警告。
- 提交生成文件。
- 将 refactor 与 feature 工作混在一起。
- 为未使用代码添加兼容性 shim。
- 在未做检查时依赖不可移植工具（`rg`、`jq`、`fd` 可能不存在）。

# 运行时安全规则

- 如果使用 `tcsetpgrp` 的前台控制权切换，在将父进程组切回前台时要处理 `SIGTTOU`。
- 将 CI 最高 Python 版本（当前为 3.13）视为运行时敏感行为的兼容性上限。
- TLS 测试/运行时的证书生成必须包含 SKI/AKI 扩展，以兼容 Python 3.13。
- 涉及证书/proxy/安全敏感变更时，在可用条件下本地以 Python 3.13 验证。
