# Claude Code 工作机制（五）：Memory 记忆系统

> 本文拆解 Claude Code 的 Memory（记忆）系统——一个纯文件 + prompt 驱动的跨会话记忆机制。
> 日志来源：使用 Claude Code 开发 [WyckoffAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent) 时的真实 API 会话（`trace_172109.jsonl`，568 次）；以及撰写本 wiki 系列时 claude-tap 仓库内的会话（Claude 本地 JSONL `07e6c1e4…`，含低优先级 memory 写入）。
>
> **系列目录**：
> - [（一）请求结构全解](./claude-code-context.md)
> - [（二）Agent Loop 与运行时机制](./claude-code-context-2.md)
> - [（三）上下文压缩](./claude-code-context-3.md)
> - [（四）Skills 技能系统](./claude-code-context-4.md)
> - **（五）Memory 记忆系统** ← 本文
> - [（六）Task 任务与后台通知](./claude-code-context-6.md)
> - [（七）Plan Mode 与用户确认](./claude-code-context-7.md)
> - [（八）MCP 集成](./claude-code-context-8.md)
> - [（九）Agent 子代理](./claude-code-context-9.md)

---

## 一句话总结

Memory = 磁盘上的 Markdown 文件 + System Prompt 里的读写指令 + 每轮注入的 MEMORY.md 索引。没有专用 API，模型用标准的 Write/Read/Edit tool 自己操作文件。

---

## 1. Memory 不是"模型的记忆"

Claude 模型本身**没有跨会话记忆**——每次新对话都是从零开始。Claude Code 的 Memory 系统是客户端的工程方案：

```
┌────────────────────────────────────────────────────────────┐
│                Memory = 三层组合                              │
├──────────────────┬─────────────────────────────────────────┤
│ 1. 指令层         │ System Prompt 中 12,833 字符的规则        │
│                  │ (定义类型、格式、何时读写)                  │
├──────────────────┼─────────────────────────────────────────┤
│ 2. 索引层         │ MEMORY.md 内容注入到每轮 system-reminder  │
│                  │ (模型每轮都能"看到"记忆索引)               │
├──────────────────┼─────────────────────────────────────────┤
│ 3. 存储层         │ ~/.claude/projects/<project>/memory/     │
│                  │ (标准文件系统，Write/Read/Edit 操作)       │
└──────────────────┴─────────────────────────────────────────┘
```

---

## 2. 存储结构

### 目录布局

```
~/.claude/projects/-Users-youngcan-stock-Wyckoff-Analysis/memory/
├── MEMORY.md                          ← 索引文件（每轮注入到 context）
├── user_goal.md                       ← user 类型
├── feedback_code_style.md             ← feedback 类型
├── feedback_web_strategy.md
├── feedback_strategy_principles.md
├── project_react_migration.md         ← project 类型
├── project_data_isolation.md
├── project_reactmind_learnings.md
├── project_branch_strategy.md
└── reference_strategy_decay.md        ← reference 类型
```

### MEMORY.md 索引文件

```markdown
- [User goal](user_goal.md) — 通过迭代 Wyckoff Agent 来掌握 Agent 开发能力
- [Feedback: code style](feedback_code_style.md) — 代码不要死代码，注释只留终态，代码量简洁
- [Feedback: web strategy](feedback_web_strategy.md) — Web 端不增加页面，新功能都放在 Agent 里
- [Project: React migration](project_react_migration.md) — Streamlit 将迁移到 React，CLI-first 迭代策略
- [Project: data isolation](project_data_isolation.md) — 路线A：信号共享，持仓/配置按用户隔离
- [Project: ReactMind learnings](project_reactmind_learnings.md) — 从公司 1024 Agent 提炼的 8 条改进方向
- [Feedback: strategy principles](feedback_strategy_principles.md) — 风控靠代码不靠LLM，改动先回测后上线
- [Ref: strategy decay](reference_strategy_decay.md) — 策略衰减三机制 + 对抗衰减的工业化迭代体系
- [Project: branch strategy](project_branch_strategy.md) — main=策略本地代码, feature/api=策略走API
```

每行 < 150 字符，上限 200 行（超出被截断）。只有索引被注入 context，完整内容需要模型主动 Read。

### 单条 Memory 文件格式

```markdown
---
name: project-branch-strategy
description: main vs feature/api 分支区别：策略本地代码 vs API调用，其余一致需同步
metadata:
  type: project
---

`main` 分支的策略逻辑是本地代码实现；`feature/api` 分支的策略逻辑通过调用 WyckoffStrategyAPI 实现。

除策略实现方式外，两个分支的所有其他改动（web UI、CLI、工具、基础设施）必须保持一致。

**Why:** 策略代码是私有的，不能公开发布。API 方式隔离了策略实现。

**How to apply:** 任何非策略相关的 commit 都需要同步到两个分支。
```

---

## 3. 四种 Memory 类型

System Prompt 中定义了 4 种 memory 类型，每种有明确的触发条件和格式要求：

| 类型 | 用途 | 触发时机 | 格式 |
|------|------|----------|------|
| `user` | 用户角色、目标、知识背景 | 了解到用户是谁 | 事实描述 |
| `feedback` | 用户对工作方式的纠正或确认 | "不要这样做" / "对，就这样" | 规则 + Why + How to apply |
| `project` | 项目动态（谁在做什么、截止日期） | 了解到项目上下文 | 事实 + Why + How to apply |
| `reference` | 外部系统的指针 | 了解到外部资源位置 | 资源描述 + 何时查看 |

### 各类型详细规则

**user（用户画像）**

| 字段 | 内容 |
|------|------|
| 触发 | 了解到用户的角色、目标、职责、知识背景 |
| 用途 | 让未来对话针对用户经验水平调整（高级工程师 vs 初学者） |
| body 格式 | 事实描述（无固定结构） |

**feedback（行为纠正与确认）**

| 字段 | 内容 |
|------|------|
| 触发 | 用户纠正（"不要这样做"）**或确认**（"对，就这样"、接受非常规选择） |
| 用途 | 避免重复犯错，同时保留已验证的成功路径 |
| body 格式 | 规则 → `**Why:**`（原因）→ `**How to apply:**`（适用场景） |

关键设计：**同时记录失败和成功**。System prompt 原文："Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious."

**project（项目动态）**

| 字段 | 内容 |
|------|------|
| 触发 | 了解到谁在做什么、为什么做、截止日期 |
| 用途 | 理解请求背后的约束和动机 |
| body 格式 | 事实/决定 → `**Why:**` → `**How to apply:**` |

关键规则：**相对日期必须转为绝对日期**。System prompt 原文："Always convert relative dates in user messages to absolute dates when saving (e.g., 'Thursday' → '2026-03-05'), so the memory remains interpretable after time passes."

**reference（外部指针）**

| 字段 | 内容 |
|------|------|
| 触发 | 了解到外部系统中资源的位置和用途 |
| 用途 | 用户引用外部系统时知道去哪找信息 |
| body 格式 | 资源描述 + 何时查看 |

### 跨记忆链接机制

Memory body 中可以用 `[[name]]` 引用其他 memory 的 slug：

```markdown
策略代码不能公开；API 方式隔离了实现。详见 [[feedback_strategy_principles]]。
```

System prompt 原文："Link related memories with `[[their-name]]`… a `[[name]]` that doesn't match an existing memory yet is fine; it marks something worth writing later, not an error."

### 触发示例（System Prompt 原文）

```
user: I've been writing Go for ten years but this is my first time touching the React side
assistant: [saves user memory: deep Go expertise, new to React — frame frontend in backend analogues]

user: don't mock the database in these tests — we got burned last quarter
assistant: [saves feedback memory: integration tests must hit real DB. Why: mock/prod divergence]

user: yeah the single bundled PR was the right call here
assistant: [saves feedback memory: for refactors, user prefers one bundled PR. Confirmed approach — not a correction]

user: we're freezing all non-critical merges after Thursday — mobile team cutting release
assistant: [saves project memory: merge freeze begins 2026-03-05. Flag non-critical PRs after that date]
```

### 禁止存储的内容

- 代码模式、架构、文件路径（从代码直接读取）
- Git 历史（`git log` / `git blame` 是权威来源）
- 调试方案（修复已在代码中）
- CLAUDE.md 中已记录的内容
- 临时任务状态（用 TaskCreate，不用 memory）

即使用户明确说"记住这个"，如果内容属于上述类别，模型应该追问"这里面什么是真正意外的？"——只保留非显而易见的部分。

### 验证规则（推荐前先确认）

Memory 可能已过时。System prompt 要求模型在基于 memory 给建议前：

- 如果 memory 提到文件路径 → 先确认文件存在
- 如果 memory 提到函数/flag → 先 grep 确认
- "The memory says X exists" ≠ "X exists now"
- 总结 repo 状态的 memory 是时间冻结的快照 → 优先用 `git log` 查当前状态

---

## 4. 运行时注入机制

### 4.1 指令注入（System Prompt）

Memory 的操作规则写在 System Prompt 的 `# auto memory` 段（约 12,833 字符），模型每次生成响应时都会遵从这些规则。以下是从真实的本地 API trace 中提取的指令核心结构和说明：

#### 4.1.1 核心指令原文与规则定义

##### A. 本地持久化路径声明
系统会在 `# auto memory` 章节的第一句明确告知模型其本地持久化目录（路径依据当前工作目录 Slug 动态替换）：
> "You have a persistent, file-based memory system at `/Users/youngcan/.claude/projects/-Users-youngcan-claude-tap/memory/`. This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence)."
> *(注：模型无需在写入前进行 `mkdir`，而是直接调用 `Write` 工具)*

##### B. 四种记忆类型的 Prompt Schema 定义
System Prompt 中使用 `<types><type>...</type></types>` 结构精确定义了 `user`、`feedback`、`project`、`reference` 4 种记忆类型，各类型的 `<description>`、`<when_to_save>` 和 `<how_to_use>` 定义规范如下：
*   **`user`**：记录用户的角色、目标、职责和已有知识，以便调整未来对话的解答深度（如针对高级工程师 vs 编程初学者采用不同解释策略）。
*   **`feedback`**：用户对工作方式的改进指导（包括纠错和肯定路径）。*特别强调双向记录机制（"Record from failure AND success"）：如果只存纠错，模型会变得过度谨慎并偏离已被验证的路径。* 其正文要求带 `Why:` 和 `How to apply:`。
*   **`project`**：进行中的项目细节、业务目标、待修缺陷或事件。*特别规则：必须将相对日期（如 "Thursday"）转换为绝对日期（如 "2026-03-05"）进行落盘，确保记忆持久可读。*
*   **`reference`**：指向外部系统（如 Linear、Slack 频道、Grafana 仪表盘）的指针信息。

##### C. 显式排除清单（What NOT to save）
Prompt 明确要求，**即使客户主动要求保存**，以下内容也禁止存入记忆系统：
*   代码模式、约定、架构、文件路径或项目结构（应动态读取项目现状）。
*   Git 历史、最近修改（`git log` / `git blame` 是唯一权威源）。
*   调试方案和修复细节（修改已入码库，上下文已在 commit 里）。
*   任何已经在 `CLAUDE.md` 中记录的内容。
*   临时任务状态和当前会话工作细节（应使用 Task 规划，而不是 Memory）。

##### D. 写入与索引更新的“两步走”协议
*   **Step 1**：将记忆写入其独立 MD 文件（如 `feedback_testing.md`），使用 Frontmatter 声明 `name`、`description` 和 `metadata.type`。在正文中通过双括号 `[[memory-slug]]` 链接相关记忆。
*   **Step 2**：在 `MEMORY.md` 索引中追加一行单行指针记录（每行建议限制在 150 字符以内，最多保留 200 行以防截断），不要在索引里直接写记忆正文。

##### E. 建议推荐前的现场验证规则（Before recommending from memory）
记忆随时间可能变质。模型在从记忆中提出建议前，被强制要求在代码库中进行实地检索（Read 确认文件是否存在，Grep 确认函数/Flag 是否仍存在）：
> "The memory says X exists" is not the same as "X exists now." (记忆中记录的存在不等于当下实际存在)


### 4.2 索引注入（system-reminder）

MEMORY.md 的内容在**每轮请求**中被注入到 `<system-reminder>` block 里，与 CLAUDE.md 和 currentDate 拼接在一起：

```json
{
  "type": "text",
  "text": "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n# claudeMd\n...\nContents of CLAUDE.md:\n...\n\nContents of /...memory/MEMORY.md (user's auto-memory, persists across conversations):\n\n- [User goal](user_goal.md) — ...\n- [Feedback: code style](feedback_code_style.md) — ...\n\n# currentDate\nToday's date is 2026/05/20.\n</system-reminder>"
}
```

注入位置：首条 user 消息的 `<system-reminder>` block（与 claudeMd 共享同一个 block）。

**关键细节**：

- 只注入 MEMORY.md 索引（每行 ~80 字符），不注入完整 memory 文件内容
- 模型看到索引后可以用 Read tool 按需读取某条 memory 的完整内容
- 索引标注了 `"user's auto-memory, persists across conversations"` 帮模型理解语义

### 4.3 注入位置图解

```
system-reminder block 结构：

┌─────────────────────────────────────────┐
│ # claudeMd                               │
│ Contents of CLAUDE.md:                    │
│   [项目指令全文]                           │
│                                           │
│ Contents of MEMORY.md (auto-memory):      │  ← Memory 索引在这里
│   - [User goal] — ...                     │
│   - [Feedback: code style] — ...          │
│   - ...                                   │
│                                           │
│ # currentDate                             │
│ Today's date is 2026/05/20.              │
└─────────────────────────────────────────┘
```

---

## 5. 写入流程实例（从 trace 观察）

### 5.1 Wyckoff：分支策略（`trace_172109` Turn 521–523）

用户输入：
> "main分支和feature/api的区别就是一个策略是本地代码另一个策略是通过调用api实现的 别的都要一致"

模型判断这条信息具备跨会话价值，自主触发 memory 保存：

| Turn | 操作 | 内容 |
|------|------|------|
| 521 | `Write` | 创建 `memory/project_branch_strategy.md`（frontmatter + 正文） |
| 521 | 回复用户 | "已同步。我把这个理解记下来。" |
| 522 | `Read` | 读取现有 `MEMORY.md` 确认索引当前状态 |
| 523 | `Edit` | 在 MEMORY.md 末尾追加一行新条目 |

**模型的输出文本**只有一句 "已同步。我把这个理解记下来。"——存储操作对用户几乎透明。

#### 写入后的效果

从 Turn 524 开始，每轮请求的 system-reminder 中都会包含新增的这行：

```
- [Project: branch strategy](project_branch_strategy.md) — main=策略本地代码, feature/api=策略走API，其余同步
```

下次新会话开始时，模型看到这行索引就"知道"这个项目有双分支策略，无需用户重复解释。

### 5.2 claude-tap：低优先级待办（会话 `07e6c1e4`，2026-05-27）

写 wiki 系列（刚改完 [（二）§4](./claude-code-context-2.md) Sonnet 调度数据）时，用户顺手记一条 **Wyckoff 侧**的低优先级改进——当前 cwd 是 `claude-tap`，memory 因此落在 **claude-tap 项目目录**，而非 Wyckoff 仓库：

![低优先级备注触发 project memory 保存](./claude-code-memory-save.png)

TUI 底部 **`Recalled 1 memory, wrote 2 memories`** 是客户端汇总行（ctrl+o 可展开），**不会**作为字面字符串出现在 API `messages` 里；对应的后端操作仍是标准 `Write`。

**时序**（Claude 本地 JSONL，非 claude-tap 代理 trace）：

| 步骤 | 事件 | 说明 |
|------|------|------|
| 1 | `subtype: away_summary` Recap | 客户端注入离开摘要："Writing a multi-part Claude Code mechanism analysis series…" |
| 2 | 用户输入 | 「低优先级：mcp_server.py 里如果 30m 拉取失败… funnel 侧已有降级保护」 |
| 3 | 模型 thinking | 判定为 **project** 类型、跨会话值得保留（含优先级理由） |
| 4 | 回复 | 「记下了。保存为 project memory 以便后续处理。」 |
| 5 | `Read` MEMORY.md | **文件不存在**（该项目 memory 目录首次使用） |
| 6 | `Write` MEMORY.md | 创建索引（1 行） |
| 7 | `Write` project_mcp-30m-degradation.md | 创建正文 + frontmatter（含 `originSessionId`） |
| 8 | 回复 | 「已记录。后续有空时可以在 mcp_server.py 里给 30m fetch 加 try/except 降级。」 |

落盘路径（注意 **项目 slug 随 cwd**）：

```
~/.claude/projects/-Users-youngcan-claude-tap/memory/
├── MEMORY.md
└── project_mcp-30m-degradation.md
```

正文摘录：

```markdown
mcp_server.py 中如果 30m 数据拉取失败，当前整个工具会失败。应改为 30m 失败时仍用 60m 单独分析。

**Why:** funnel 侧已有降级保护，但 mcp_server 的工具调用入口缺少同样的容错。主任务（funnel）没这个风险，所以优先级低。

**How to apply:** 低优先级改进。实现时在 30m fetch 加 try/except，失败时 log warning 并只传 60m 数据给分析逻辑。
```

**与 §5.1 的差异**：

| | Wyckoff 分支策略 | claude-tap 30m 降级 |
|--|-----------------|---------------------|
| MEMORY.md 已存在 | ✓ | ✗（Read 报错后 Write 新建） |
| 索引更新方式 | `Edit` 追加 | `Write` 整文件创建 |
| 内容所属代码库 | 与 cwd 一致（Wyckoff） | **内容与 cwd 不一致**（记的是 Wyckoff 的 mcp_server，但文件在 claude-tap 项目 memory 下） |
| 用户是否显式说「保存」 | 否 | 否（仅标注「低优先级」） |

后一条说明：Memory 路径绑定 **Claude Code 项目**（`~/.claude/projects/-{cwd-with-dashes}/`），不绑定 git remote；在 A 仓库会话里备注 B 仓库的技术债，会进 A 的 memory 目录。

---

## 6. 读取流程

Memory 的读取分两级：

### 被动读取（每轮自动）

MEMORY.md 索引随 system-reminder 自动注入——模型**每轮都能看到所有 memory 的标题和一句话描述**。

### 主动读取（按需）

当模型判断某条 memory 的完整内容与当前任务相关时，使用 Read tool 读取：

```json
{ "type": "tool_use", "name": "Read", "input": { "file_path": "~/.claude/projects/.../memory/feedback_code_style.md" } }
```

System Prompt 要求：
> Before recommending from memory... If the memory names a file path: check the file exists. If the memory names a function or flag: grep for it.

即：记忆可能过时，推荐前必须验证。

---

## 7. 设计洞察

### 为什么不用数据库？

文件系统 = 最简单的持久化 + 模型已有 Read/Write/Edit tool = 零额外开发成本。Memory 目录就是一个迷你 wiki，用现成工具操作。

### 为什么只注入索引不注入全文？

- 9 条 memory 的索引 ≈ 700 字符
- 9 条 memory 的全文 ≈ 5,000+ 字符
- 索引足够让模型判断"要不要深入看"，按需 Read 节省 token

### 为什么模型"自主"保存而不需要用户命令？

System Prompt 中定义了 `when_to_save` 触发条件。模型在每轮处理用户输入时，会同时判断"这句话是否包含值得跨会话保留的信息"——如果是，就主动 Write。

从 trace 看到的真实触发：用户说了分支策略的规则，模型判断这是 `project` 类型（"learn who is doing what, why"），于是自主保存。

### Memory vs CLAUDE.md vs Context Compaction Summary

```
┌──────────────────┬──────────────┬────────────────┬─────────────────┐
│                  │ Memory       │ CLAUDE.md      │ Compaction Sum  │
├──────────────────┼──────────────┼────────────────┼─────────────────┤
│ 持久性           │ 跨会话永久    │ 跨会话永久      │ 仅当前会话       │
│ 写入者           │ 模型自主      │ 用户/模型       │ 压缩 API 自动   │
│ 注入方式         │ 索引每轮注入  │ 全文每轮注入    │ 替换 messages    │
│ 内容类型         │ 用户画像/偏好 │ 项目规则/指令   │ 对话全量摘要     │
│ 体积            │ 索引 ~700 ch  │ 全文 ~1-3 KB   │ ~8 KB            │
│ 可被模型修改     │ ✓（Write）    │ ✓（Edit）      │ ✗（只读）        │
└──────────────────┴──────────────┴────────────────┴─────────────────┘
```

---

## 8. 数据速查

| 指标 | 数值 |
|------|------|
| Memory 指令在 System Prompt 中的长度 | 12,833 chars |
| Memory 指令在 System Prompt 中的位置 | block[2]（主指令块末尾，`# auto memory` 段） |
| MEMORY.md 索引注入位置 | system-reminder block（与 claudeMd 同一 block） |
| 索引每行长度上限 | ~150 chars |
| 索引最大行数 | 200 行（截断） |
| Memory 类型数量 | 4（user / feedback / project / reference） |
| 本次 trace 中的 memory 操作次数 | Wyckoff 3 次（Write + Read + Edit）；claude-tap 首次 2×Write + 1×Read |
| TUI「Recalled / wrote N memories」 | 客户端汇总，不在 API messages 中 |
| Memory 目录与 cwd 关系 | 路径 slug 来自当前工作目录，内容可涉及其他仓库 |
| 操作所用工具 | 标准 Write / Read / Edit（无专用 Memory tool） |
| Memory 目录路径模式 | `~/.claude/projects/-{path-with-dashes}/memory/` |

---

## 9. 可观测事实 vs 推断

| 结论 | 来源 | 可信度 |
|------|------|--------|
| System Prompt 含 12,833 chars 的 memory 操作指令 | trace system block[2] | ✅ 确定 |
| MEMORY.md 索引注入到 system-reminder（与 claudeMd 合并） | trace messages 解析 | ✅ 确定 |
| 模型用标准 Write/Read/Edit 操作 memory 文件 | Turn 521–523 tool_use | ✅ 确定 |
| 写入为三步：Write 内容 → Read 索引 → Edit 追加 | trace_172109 时序 | ✅ 确定 |
| 首次 memory：Read 失败 → Write 索引 + Write 正文 | 07e6c1e4 JSONL | ✅ 确定 |
| 「低优先级」备注仍可触发 project memory | 07e6c1e4 用户原文 | ✅ 确定 |
| Memory 路径随 cwd 项目 slug，非 git remote | claude-tap memory 存 Wyckoff 备注 | ✅ 确定 |
| TUI Recalled/wrote 计数为客户端 UI | 无 API 字面量 | ✅ 确定 |
| 模型自主判断保存时机（无用户命令） | Turn 521 用户原文 vs 模型行为 | ✅ 确定 |
| 只有索引被自动注入，全文需主动 Read | trace 中 system-reminder 内容 | ✅ 确定 |
| Memory 文件热更新（写入后下一轮即可见） | 指令文本 + Turn 524 推断 | ⚠️ 高置信推断 |

---

## 上一篇 / 下一篇 / 系列索引

- [（四）Skills 技能系统](./claude-code-context-4.md)
- [（六）Task 任务与后台通知](./claude-code-context-6.md)
- [系列索引](./claude-code-context-index.md)
