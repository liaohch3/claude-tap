# Pi 可观测性生态调研

- 调研日期：2026-07-22
- 本机基线：Pi `0.81.0`，Node.js `v26.5.0`
- 问题：是否已有 Pi package/extension 能把请求、响应、工具调用、token、耗时等信息放到 Web 页面，并尽量不改变现有 `pi` 使用习惯？
- 方法：查阅 Pi package registry、npm 元数据、项目 README、源码和厂商官方文档；版本和活跃日期均以调研日查询结果为准。

## 结论

有，而且已经有人实现了此前设想的“Pi extension 主动把事件送给本地 collector，再由 Web dashboard 展示”的完整架构：[`@spences10/pi-observability`](https://github.com/spences10/my-pi/tree/main/packages/pi-observability)。因此，目前没有必要先为 claude-tap 自研一套 Pi extension。

建议分成两个用途选择：

1. **日常实时查看，首选 `@spences10/pi-observability`。** 它自动加载到普通 `pi`，在本机启动带认证的 collector，把 Pi 生命周期事件批量发送到 SQLite，并通过 SSE 实时更新 Web dashboard。无需代理模型流量，也不需要改变 `pi` alias。
2. **需要看模型真正收到的上下文时，加装 `pi-trace-extension`。** 它把 provider 请求 payload 保存到本地 JSONL，再生成静态 HTML；上下文、消息和 tool schema 的可见度明显更深，但不是实时 dashboard。
3. **需要团队检索、留存、评估和共享时，再考虑 `pi-langfuse`。** 它不需要代理，但要把数据发到 Langfuse Cloud 或自建 Langfuse。

两款本地扩展的定位不是互斥关系：`@spences10/pi-observability` 负责“现在发生了什么”，`pi-trace-extension` 负责“这一次模型到底看到了什么”。两者都通过 Pi extension event API 采集，不改 provider URL，也不接管 `pi` 入口。它们从包声明上可以共存；本次只做静态核验，尚未在同一 Pi 会话里做共存 E2E，因此共装前仍应做一次真实会话验证。

## 与当前环境的兼容性

| Package | 当前版本与发布时间 | Node / Pi 要求 | 本机判断 |
|---|---|---|---|
| [`@spences10/pi-observability`](https://www.npmjs.com/package/@spences10/pi-observability) | `0.0.21`，2026-07-20 | Node `>=24.15.0`；peer 为 `@earendil-works/pi-coding-agent:*` 和 `@earendil-works/pi-tui:*` | **元数据兼容**：Node 26.5 满足，Pi 0.81 未被 peer range 排除 |
| [`pi-trace-extension`](https://www.npmjs.com/package/pi-trace-extension) | `0.1.12`，2026-07-13 | Node `>=18`；README 要求 Pi `>=0.79.x`；peer 为 `@earendil-works/pi-coding-agent:*` | **兼容**：Pi 0.81 和 Node 26.5 均满足 |
| [`pi-langfuse`](https://www.npmjs.com/package/pi-langfuse) | `1.5.7`，2026-07-20 | Node `>=22`；peer 为 `@earendil-works/pi-coding-agent:*` | **元数据兼容** |

“元数据兼容”表示 package manifest 没有版本冲突，不等于已经在这台机器完成真实安装/E2E。

## 核心候选对比

| 项目 | 采集方式 | Web 形态 | 能看到什么 | 对 `pi` 习惯影响 | 主要代价 |
|---|---|---|---|---|---|
| `@spences10/pi-observability` | Pi extension → 本地 HTTP collector → SQLite | **实时**本地 dashboard，SSE 更新 | session、turn、message、provider、tool、model、compaction、branch；耗时瀑布、token、cost、错误、可搜索事件 JSON | **最低**：安装后仍直接运行 `pi`；用 `/observability` 打开 | 默认 detailed 会概括大数组/嵌套对象；不是原始网络包；项目仍年轻 |
| `pi-trace-extension` | Pi extension → 每会话 `events.jsonl` → Python 渲染 | **静态**自包含 HTML；另有跨会话 index | provider input、message history、tool schema、输出/思考、token、cost、工具树、subagent、错误/中止 | **最低**：安装后仍直接运行 `pi`；需要 `/trace` 重新生成当前快照 | 非实时；原始 JSONL 的隐私风险；无轮转；普通成功工具结果只保留短预览 |
| `pi-langfuse` | Pi extension → Langfuse SDK/OTel/REST | Langfuse Cloud 或自建 Web | prompt、generation、tool、最终响应、usage/cost、scores、跨会话查询 | 低：仍运行 `pi`，首次配置凭据 | Cloud 会上传数据；自建有运维成本；默认 `full-debug` 很敏感 |
| `pi-kanban` | 直接读取 Pi 原生 session JSONL | 实时本地 session/project UI | 对话、工具、subagent、todo/plan、token/cost/duration | 低：普通 `pi`，另启动 `/kanban start` | 看不到完整 provider request；服务监听与认证设计较弱 |

## 1. `@spences10/pi-observability`：最符合“实时 Web + 普通 pi”

项目的[官方 README](https://github.com/spences10/my-pi/blob/main/packages/pi-observability/README.md)明确描述了完整链路：extension 自动启动 `http://127.0.0.1:43190` 的本地服务，生命周期事件写入 `~/.pi/agent/observability.db`，Web 页面通过 Server-Sent Events 实时更新。安装方式是：

```bash
pi install npm:@spences10/pi-observability
```

之后仍然直接启动：

```bash
pi
```

在 Pi 中使用：

```text
/observability
/observability url
/observability tui
```

从[扩展源码](https://github.com/spences10/my-pi/blob/main/packages/pi-observability/src/index.ts)和 README 可确认，它监听 session、agent、turn、message、tool、provider、model、compaction、branch 等事件，包含 `before_provider_request` 和 `after_provider_response`。事件由 agent 进程主动 POST 给本地 collector，失败时排队重试；这正是此前设想的“extension 主动送数据”，不是网络代理。

Dashboard 已具备：

- 会话概要、工作目录、session file、provider/model、thinking 配置、初始 user/system prompt 预览；
- provider/tool/message span 的 waterfall 与耗时瓶颈；
- token、cost、error 汇总；
- 可搜索事件列表与按需展开 JSON；
- SSE 实时刷新。

本地安全设计也比较完整：默认只监听 `127.0.0.1`，API 使用随机 Bearer token；token、SQLite、WAL/SHM 文件设为 `0600`；浏览器 URL 用 fragment 携带 token，随后改用 `Authorization` header；默认保留 14 天或最多 100,000 个事件。实现可在[服务源码](https://github.com/spences10/my-pi/blob/main/packages/pi-observability/src/server.ts)、[脱敏源码](https://github.com/spences10/my-pi/blob/main/packages/pi-observability/src/redact.ts)和[数据库源码](https://github.com/spences10/my-pi/blob/main/packages/pi-observability/src/db.ts)中核验。

限制也要说清：

- 默认 `detailed` 模式仍会概括大数组和嵌套对象，字符串也有限长，因此它更适合实时定位“哪一步慢、哪个工具错、调用顺序是什么”，不保证完整展示 provider request body。
- `--observability-raw` 可提高细节，但仍会递归脱敏并受 payload byte cap 限制。
- 它采集的是 Pi 已解析后的语义事件，不是 HTTP header、SSE chunk、重试连接等“线上原始字节”。若要排查传输层，仍需代理型抓包。
- GitHub 仓库在调研日有 91 stars，最近 push 为 2026-07-21；开发活跃，但 npm 包仍处于 `0.0.x`，应先做真实 E2E 再作为长期唯一数据源。[仓库](https://github.com/spences10/my-pi)

## 2. `pi-trace-extension`：本地深挖 provider 上下文

[`pi-trace-extension`](https://github.com/npxcnency-ux/pi-trace-extension)订阅 Pi 生命周期事件，把每个 session 写成：

```text
~/.pi/agent/traces/<session-id>/events.jsonl
~/.pi/agent/traces/<session-id>/trace.html
```

安装和使用：

```bash
pi install npm:pi-trace-extension
pi
```

```text
/trace
/trace all
```

它的三栏 HTML 以 `session → interaction → turn → llm-generation/tool` 组织数据。根据[官方 README](https://github.com/npxcnency-ux/pi-trace-extension/blob/main/README.md)，右栏可以查看 provider input，包括 model、messages、tool schemas 和请求参数，以及标准化输出、thinking、stop reason、token、cost、延迟、工具调用、错误和 subagent 子 trace。`/trace all` 还会生成本机跨会话 index。

它比实时 dashboard 更适合回答：

- 这一轮发给模型的 message history 到底是什么？
- system prompt、工具定义和 context compaction 后的上下文是什么？
- agent 为什么在某个 turn 选了这个工具？
- subagent 内部又经历了哪些 turn/tool？

但它不是 live Web app：`events.jsonl` 会持续写，`trace.html` 只是生成时的快照；需要再次执行 `/trace` 才能看到最新内容。普通成功工具执行的结果在当前[采集源码](https://github.com/npxcnency-ux/pi-trace-extension/blob/main/extensions/trace/index.ts)中通常只保留约 500 字符预览，错误约 3,000，subagent 约 8,000，所以“full payload”主要指 provider 输入，而不是所有工具输出都完整。

### 重要隐私核验

项目 README 声称 secret-like key 会在写入时脱敏，但当前源码与该描述不一致：

- TypeScript 的 [`writeEvent`](https://github.com/npxcnency-ux/pi-trace-extension/blob/main/extensions/trace/index.ts)直接执行 `JSON.stringify(event)` 写入 `events.jsonl`；provider payload 的整理函数会截断/重组，但没有做 secret-key sanitizer。
- Python [HTML renderer](https://github.com/npxcnency-ux/pi-trace-extension/blob/main/extensions/trace/trace_to_html.py)才通过 `sanitize()` 对 `password`、`token`、`secret`、`api_key`、`authorization`、`bearer` 等 key 做替换。

因此，生成后的 HTML 对常见 secret key 有一层保护，但原始 `events.jsonl` 仍可能保存敏感内容；prompt、工具参数和输出中的非标准秘密也不会自动识别。不要直接分享整个 trace 目录。这个差异应在采用前向上游反馈。

此外，它没有 retention/rotation，长会话会持续增大 JSONL；README 也提示大型 HTML 可能变慢。仓库最近 push 为 2026-07-13，调研日 6 stars，属于功能有针对性但成熟度较低的项目。[npm](https://www.npmjs.com/package/pi-trace-extension)

## 3. `pi-langfuse`：团队/长期观测的优先候选

[`pi-langfuse`](https://github.com/gooyoung/pi-langfuse)是当前更完整的 Langfuse 集成。安装后仍直接运行 `pi`：

```bash
pi install npm:pi-langfuse
pi
```

首次启动配置 Langfuse public key、secret key 和 host。根据[项目 README](https://github.com/gooyoung/pi-langfuse/blob/main/README.md)，每个用户 prompt 对应一个 trace，并按 Pi session 分组；root agent、每次 provider generation、每个 tool 都有 observation，还包括最终 assistant output、usage/cost、错误和 trace score。它支持 Langfuse Cloud，也支持自建 host。Langfuse 的[官方集成页面](https://langfuse.com/integrations/developer-tools/pi-agent)把它标为 community-maintained integration。

隐私策略比许多简单插件成熟，提供 `metadata-only`、`prompts-only`、`conversations`、`full-debug` 预设，以及 secret redaction、路径 hash 和 payload shaping。不过默认是 `full-debug`，使用 Cloud 时意味着 prompt、response、tool I/O 等敏感数据可能离开本机；应先改成符合需求的 preset，或使用自建 Langfuse。

它适合：

- 跨机器/跨成员查询；
- 按模型、项目、版本分析；
- 长期留存、评分、评估和共享；
- 不介意维护一个自建服务，或允许使用 SaaS。

仓库最近 push 为 2026-07-20，npm `1.5.7` 同日发布，调研日 14 stars，活跃度和功能完整度在 Pi Langfuse 包中较好。

## 其他可用项目

### `pi-kanban`

[`pi-kanban`](https://github.com/NikiforovAll/pi-kanban)直接读取 `~/.pi/agent/sessions` 的原生 JSONL，用文件监听和 SSE 展示实时 session/project 看板。安装 `pi install npm:pi-kanban` 后运行 `/kanban start`，Web 默认端口 3460。它能展开消息、工具和 subagent，并显示 model、token、cache、cost、duration 等信息。[Pi registry](https://pi.dev/packages/pi-kanban) [项目文档](https://nikiforovall.github.io/pi-kanban/docs/)

优点是完全不增加 agent 侧 instrumentation；缺点是只能展示 Pi 已写入 session file 的内容，无法还原完整 `before_provider_request` payload。另一个安全 caveat 是当前 [`server.js`](https://github.com/NikiforovAll/pi-kanban/blob/main/server.js)使用 `listen(PORT)` 而没有显式传入 `127.0.0.1`，也未见 dashboard 认证。根据 Node 的监听语义，这可能绑定未指定地址而不只本机回环；应仅在可信网络中运行，或先改为显式 loopback。

### SaaS / OTel 方向

| 项目 | 状态与价值 | 为什么不是本次首选 |
|---|---|---|
| [`@braintrust/pi-extension`](https://github.com/braintrustdata/braintrust-pi-extension) | Braintrust 官方仓库；npm `0.10.0`，2026-07-17；采集 session/turn/LLM/tool、compaction、token/cost/TTFT，提供 Web trace | 需要 Braintrust 账号并上传数据；刻意不记录完整 provider payload，适合指标与评估，不适合深挖上下文 |
| [`@latitude-data/pi-telemetry`](https://github.com/latitude-dev/latitude-llm/tree/development/packages/telemetry/pi) | Latitude 官方 monorepo；采集 prompt、assistant、system/context、provider 参数、tool 定义/参数/结果、token/cost，并发到 Latitude Web | 需要账号/项目/API key；不是本地 dashboard |
| [`@amaster.ai/pi-telemetry`](https://github.com/TGYD-helige/pi/tree/master/packages/pi-telemetry) | npm `0.1.6`，2026-07-20；支持 Langfuse 和通用 OTLP/HTTP，peer 要求当前 `@earendil-works/pi-coding-agent>=0.74.0` | 只是 exporter，没有自带 Web UI；还要选择并运行观测后端 |
| [`@grafana/agento11y-pi`](https://github.com/grafana/agento11y/tree/main/plugins/pi) | Grafana 官方 Agento11y Pi 插件，可把 conversation/model/token/tool/timing 发到 Grafana Cloud AI Observability | 当前 npm manifest 仍声明旧 `@mariozechner/pi-*` peers，项目又处在 Sigil → Agento11y 包名迁移期；对当前 `@earendil-works` Pi 0.81 需额外兼容性验证 |
| [Hugging Face Agent Traces](https://huggingface.co/docs/hub/en/agent-traces) | 官方 viewer 原生支持 Pi session JSONL；可同步 dataset/bucket 后在 Web 看 timeline、prompt、assistant、tool calls/results | 更适合事后分享，不是本地实时 dashboard；数据需上传 |

## 淘汰项与近似项

- [`@jmfederico/pi-web`](https://github.com/jmfederico/pi-web)是浏览器中的完整 Pi 运行/控制面，不是被动 observability；其当前 npm peer range 是 `@earendil-works/pi-* >=0.80.8 <0.81`，**明确不包含本机 Pi 0.81.0**，且会把主要交互习惯迁到浏览器，因此本次淘汰。
- [`@firstpick/pi-package-webui`](https://www.npmjs.com/package/@firstpick/pi-package-webui)通过 `pi --mode rpc` 把浏览器变成主要界面；功能目标是 Web 客户端，不是尽量无感的旁路追踪。
- [`pi-studio`](https://www.npmjs.com/package/pi-studio)偏浏览器工作区/控制台，不是请求级历史 trace dashboard。
- [`@observal/pi-insights`](https://github.com/Observal/pi-insights)和 [`pi-insights`](https://github.com/RimuruW/pi-insights)从历史 session 生成 HTML 使用分析，适合周期统计，不适合实时排查单次 provider/tool 链路。
- [`pi-observability`](https://github.com/imran-vz/pi-observability)和 [`pine-of-glass`](https://github.com/tmustier/pine-of-glass)主要是 TUI/footer 指标，能看 token、cost、context、速度，但没有目标中的 Web trace 页面。
- [`@hdkiller/pi-langfuse`](https://github.com/hdkiller/pi-langfuse)当前 npm peer 仍使用旧 `@mariozechner/pi-coding-agent:*`，最近发布时间为 2026-04-26；当前 fork 已使用 `@earendil-works` scope，不作为优先候选。
- `@devkade/pi-opentelemetry`、`@mobrienv/pi-otlp` 等较早 OTel 包也以旧 `@mariozechner` API 或较老 Pi 版本开发，兼容性风险高于 `@amaster.ai/pi-telemetry`。

## Logfire 与 Helicone

在本次对 Pi registry、npm 和 GitHub 的检索范围内，没有找到一个活跃且专门支持当前 `@earendil-works` Pi 的 Logfire 或 Helicone package。这是“在已检索一手来源中未发现”，不是对整个互联网的绝对不存在证明。

- Logfire [官方文档](https://logfire.pydantic.dev/docs/how-to-guides/alternative-clients/)支持接收 OTLP HTTP/protobuf/JSON。理论上可把通用 Pi OTLP exporter 指向 Logfire，但没有发现 `@amaster.ai/pi-telemetry → Logfire` 的官方验证文档；这是可行性推断，不应当作开箱即用承诺。
- Helicone 的[官方 OpenLLMetry 集成](https://docs.helicone.ai/getting-started/integration-method/openllmetry)支持无代理异步 telemetry，但要适配 Pi 仍需自定义 instrumentation 或 provider wrapper；目前不如已有 Pi 原生 extension 省事。

## 推荐落地顺序

### 第一阶段：只装实时本地 dashboard

```bash
pi install npm:@spences10/pi-observability
pi list
pi
```

在 Pi 中运行：

```text
/observability url
```

用一次包含普通回复、一个工具调用和一个 MCP 调用的真实会话确认：

- dashboard 能实时新增 event；
- provider/tool span 顺序正确；
- token/cost/model 不再是 `unknown`；
- 展开事件 JSON 后的细节足够日常排查；
- 结束 Pi 后 SQLite 中的会话完整。

这一步通过后，就可以把 `pi` alias 恢复为原生 Pi；扩展会由 Pi package loader 自动加载，不依赖 claude-tap proxy。

### 第二阶段：细节不够时再加静态深挖

```bash
pi install npm:pi-trace-extension
pi list
```

跑完相同用例后执行：

```text
/trace
/trace all
```

重点比较 `before_provider_request`、messages 和 tools schema 是否补足实时 dashboard 的缺口。测试完成后检查 `~/.pi/agent/traces/` 的敏感内容和磁盘增长，制定本地清理策略。

### 第三阶段：只有出现团队需求才接 SaaS/自建平台

若以后需要团队共享、跨机器搜索、评估和长期趋势，再从 `pi-langfuse`、Braintrust、Latitude 或 Grafana 中选择。个人本机调试阶段先用本地两款，部署和隐私成本最低。

## 最终判断

对“尽量不改变使用 Pi 的习惯，同时在 Web 里直观看大量信息”这个目标，当前最合理的组合是：

```text
普通 pi
  ├─ @spences10/pi-observability → 实时本地 dashboard
  └─ pi-trace-extension          → 需要时生成深度静态 trace
```

如果只能选一个，先选 `@spences10/pi-observability`；它已经实现了原计划中的 Pi extension → collector → Web UI，而且本机 Node/Pi 在 package metadata 上兼容。只有当“完整 provider 上下文”比“实时体验”更重要时，才单独优先 `pi-trace-extension`。
