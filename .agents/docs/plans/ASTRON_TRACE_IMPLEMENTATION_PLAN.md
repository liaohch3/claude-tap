---
status: completed
---

# claude-tap AstronCode Trace 专用客户端实施计划

## 1. 文档状态

- 状态：Completed
- 编写日期：2026-07-24
- 完成日期：2026-07-24
- 目标：在 `claude-tap` 中新增 AstronCode 专用 Trace 客户端
- claude-tap 工作区根目录：`/Users/wangqizhao/Developer/iflytek/claude-tap`
- 计划文件位置：`.agents/docs/plans/ASTRON_TRACE_IMPLEMENTATION_PLAN.md`
- 除明确标注的外部 AstronCode 参考路径外，本文中的仓库路径均相对于上述工作区根目录
- 实施与验证证据：`.agents/evidence/pr/astron-trace/validation.md`

## 2. 背景

`claude-tap` 是一个面向 AI 编程客户端的本地代理与 Trace Viewer。它可以通过
reverse proxy 或 forward proxy 捕获模型请求、响应流、工具定义、工具调用、工具结果
和 token 用量，并将结果写入本地 Trace 存储供 Viewer 查看。

AstronCode 基于 Codex CLI，但有以下产品差异：

1. AstronCode 的 npm 主包是 `@iflytek/astron-code`，对外命令是 `astron-code`；
   npm 启动器再根据当前平台解析内部名为 `codex` 的原生二进制。用户机器 PATH 中的
   `codex` 不一定是 AstronCode 构建，因此不得用它作为 AstronCode 的默认发现目标。
2. AstronCode 的默认 model、provider、模型接口、`models_base_url` 和鉴权配置由产品
   配置层编译进二进制。
3. AstronCode 除模型 `/responses` HTTP/SSE 外，还会访问：
   - `/models` 或带产品路径前缀的模型目录接口；
   - Apps/Connectors 目录接口；
   - Apps 和远程插件使用的 Streamable HTTP/SSE MCP 接口；
   - 其他由 AstronCode 当前实现发起的上游 HTTP 请求。
4. 本项目的目标不是只观察模型 `/responses`，而是保留 AstronCode 运行期间所有经过
   claude-tap forward proxy、且符合 claude-tap 现有通用采集规则的上游 HTTP/SSE
   Trace。

claude-tap 当前 `codex` 客户端默认使用 reverse proxy。reverse proxy 在无法从用户
配置中识别 AstronCode 编译内置 provider 时，会注入临时 OpenAI provider，因此不适合
直接用于 AstronCode 的完整链路 Trace。

## 3. 已确认的产品决策

以下决策作为本计划的固定输入：

1. 新增专用客户端：

   ```bash
   --tap-client astron
   ```

2. `astron` 客户端默认使用 forward proxy。
3. 不为 `astron` 配置仅 `/responses` 的 method/path 白名单。
4. 保留所有经过 forward proxy 且符合 claude-tap 现有通用采集规则的上游请求。
5. 不删除、不替换、不缩减 claude-tap 已有客户端、已有协议支持或已有采集能力。
6. AstronCode 的 provider、base URL、模型、鉴权和 Apps/插件配置保持原样，不由
   claude-tap 重写。
7. 第一阶段不要求为 `/models`、Apps、MCP 增加完整语义化 Viewer；必须先保证原始
   HTTP/JSON/SSE Trace 可见和可检索。
8. `astron` 只支持 forward proxy；显式组合
   `--tap-client astron --tap-proxy-mode reverse` 必须在启动客户端前拒绝。
9. 本次不扩修 claude-tap 现有通用脱敏、存储、Viewer 或导出策略；Astron profile
   复用现有行为，E2E 只提交清洗后的证据摘要。

这里的 `astron` 是 claude-tap 的 client profile，不是新增 AstronCode
`model_provider`。

## 4. 仓库和基线

### 4.1 claude-tap

- 上游仓库：`https://github.com/liaohch3/claude-tap`
- 工作区根目录：`/Users/wangqizhao/Developer/iflytek/claude-tap`
- 基线分支：`main`
- 2026-07-24 观测到的最新提交：
  `1f9b3dfa233ce613cc09bdc5ca33db0663368ee9`
- 实际实施前必须重新拉取并记录当时的 `origin/main` SHA。
- 本地实施 checkout：当前工作区；代码、测试和计划路径均以该工作区为根目录。

### 4.2 AstronCode

- 本地仓库绝对路径：
  `/Users/wangqizhao/Developer/iflytek/AstronCode/acode`
- 远端仓库：`https://code.iflytek.com/CYXH_Agent/acode.git`
- 参考基线分支：`master`
- 规范基线：实施开始时最新的 `origin/master`
- 2026-07-24 观测到的 `origin/master`：
  `000d21ff72a0e3449ddd9d3f9f5602aeaed4d12d`
- 2026-07-24 本地 `master`：
  `000d21ff72a0e3449ddd9d3f9f5602aeaed4d12d`
- 已确认本地 `master` 与 `origin/master` 同步，ahead/behind 为 `0/0`。真实 E2E
  开始前仍须重新记录 SHA；如需更新，只允许 `--ff-only`。

AstronCode 在本阶段是兼容性参考和真实 E2E 客户端，不计划修改其模型、Apps、插件或
管理面实现。若必须修改 AstronCode，需单独提出变更计划。

## 5. 目标

### 5.1 功能目标

完成后应支持自动发现当前 npm 全局环境中的 AstronCode：

```bash
claude-tap --tap-client astron
```

必要时可以显式覆盖自动发现结果：

```bash
claude-tap \
  --tap-client astron \
  --tap-client-cmd /absolute/path/to/astron-code
```

预期行为：

1. 未显式指定路径时，自动使用当前本地 npm 全局环境安装的 `astron-code` shim。
2. 不把 PATH 中已有的官方或其他 `codex` 误认为 AstronCode。
3. 显式指定路径时只使用该路径，不回退到其他候选。
4. 默认启动 forward proxy。
5. 向 AstronCode 子进程注入 forward proxy 和自定义 CA 所需环境。
6. 不修改 AstronCode 的 provider 或上游地址。
7. 所有经过代理并符合 claude-tap 现有通用规则的 HTTP/SSE 请求均正常转发和记录。
8. 现有 `/responses` Viewer 能继续展示：
   - system prompt；
   - messages；
   - tools schema；
   - tool calls；
   - tool results；
   - SSE 输出；
   - token usage；
   - 相邻请求 diff。
9. `/models`、Apps 目录和 Streamable HTTP/SSE MCP 请求至少能以原始 HTTP
   Trace 形式查看。

### 5.2 兼容性目标

1. 现有 `claude`、`codex`、`codexapp` 及其他客户端行为保持不变。
2. 现有 `codex` reverse proxy 行为保持不变。
3. 现有 Trace 存储格式和历史 Trace 读取保持兼容。
4. 不删除任何现有客户端配置、代理模式、Viewer 解析器或测试覆盖。

## 6. 非目标

第一阶段不包含：

1. 为 AstronCode 实现 reverse proxy。
2. 在 claude-tap 中硬编码 AstronCode endpoint、token 或 provider 配置。
3. 捕获本地 stdio MCP 的 stdio 帧。
4. 捕获插件 manifest、skill 或本地资源的文件读取过程。
5. 修改 Plugins Hub 的发布、上传、删除等管理面逻辑。
6. 为所有 Apps/MCP 协议消息一次性开发专用语义化 Viewer。
7. 将 claude-tap Python 运行时直接打包进 AstronCode 正式发行物。
8. 修改 AstronCode `master`；它仅作为构建和 E2E 基线。
9. 将项目或命令 `claude-tap` 改名为 `astron-tap`。
10. 为本次改造新增 TUI。

## 7. 总体设计

```text
npm 全局 `astron-code` shim 或显式指定的 AstronCode 可执行文件
  │
  │ 原有 model/provider/models/apps/plugins 配置保持不变
  │
  ├─ HTTP/SSE Responses
  ├─ Models API
  ├─ Apps/Connectors Directory
  ├─ Streamable HTTP/SSE MCP
  └─ 其他上游 HTTP 请求
          │
          ▼
claude-tap astron forward proxy
          │
          ├─ 正常转发全部请求
          ├─ 按现有通用规则写入 Trace
          └─ 复用现有通用 header/Trace 处理
                  │
                  ▼
          Trace Store / Viewer
```

## 8. 具体改造

### 8.1 新增 `astron` ClientConfig

在 `CLIENT_CONFIGS` 中新增 `astron`：

- `cmd = "astron-code"`，作为 PATH 查找目标，不使用 `codex`；
- `label = "Astron Code"`；
- `default_proxy_mode = "forward"`；
- 不配置 `forward_trace_methods`；
- 不配置 `forward_trace_path_prefixes`；
- 不配置 AstronCode 专用 reverse base URL；
- 不硬编码 AstronCode endpoint、模型或凭据。

空的 `forward_trace_methods` 和 `forward_trace_path_prefixes` 表示不为 AstronCode
新增 method/path 采集限制。已有通用安全过滤、协议限制和噪声过滤仍保留，不删除
上游现有逻辑。

### 8.2 npm 全局安装自动发现与显式覆盖

AstronCode 正式 npm 包通过 `@iflytek/astron-code` 暴露 `astron-code` 命令。
claude-tap 应启动 npm 生成的命令 shim，让该启动器负责选择对应平台包中的原生
`codex`、设置 npm 管理环境并转发进程信号；不直接猜测或启动平台包内部的原生文件。

`--tap-client-cmd` 不新建一套客户端启动机制。当前代码已经有
`args.client_cmd`、`_resolve_client_executable(client, cfg, client_cmd)`、
`_prefer_windows_command_shim()` 和 `run_client(client_cmd=...)` 链路；本次只将
`client_cmd` 暴露为公开 parser 参数，并在现有解析链上补齐 Astron 自动发现和必要的
校验。不得增加平行 resolver，也不得绕过 `run_client()` 直接启动 AstronCode。

可执行文件解析优先级：

1. 若传入 `--tap-client-cmd`，只使用该显式路径。
2. 否则执行 `shutil.which("astron-code")`，优先使用当前 shell 已选择的 npm/Node
   环境中的 shim。
3. 若 PATH 未找到但 `npm` 可用，以无 shell 的子进程执行 `npm prefix -g`：
   - macOS/Linux 候选为 `<prefix>/bin/astron-code`；
   - Windows 候选优先为 `<prefix>/astron-code.cmd`，并兼容 npm 生成的可执行 shim。
4. `npm prefix -g` 必须设置短超时并检查退出码；输出为空、路径不存在或校验失败时，
   进入明确错误分支。
5. 不使用 `npm exec` 或 `npx` 自动下载包，避免运行时拉取非预期版本。
6. 不递归扫描所有 Node/npm 安装目录。若用户使用的不是当前 npm 环境，要求通过
   `--tap-client-cmd` 明确指定。
7. 解析成功后记录不含敏感信息的发现来源、绝对路径和 `astron-code --version`
   结果，便于确认实际观测版本。

新增通用显式覆盖参数：

```bash
--tap-client-cmd <ABSOLUTE_PATH>
```

参数行为：

1. 适用于 `astron`，同时设计为可复用于其他客户端。
2. 优先级高于 PATH 和 npm 全局安装自动发现。
3. 要求路径存在、指向普通文件并具有可执行权限。
4. 支持路径中包含空格。
5. 路径无效时 fail closed，禁止静默回退到 PATH 或 npm 中的其他候选。
6. 未传该参数时，按上述 `astron-code` PATH/npm 优先级自动发现。
7. 不把 `--` 后的第一个参数误解释成 Codex 可执行文件；`--` 后仍然只是客户端参数。

parser 接线要求：

1. 新增 `--tap-client-cmd` 时使用现有 `dest=client_cmd`。
2. 当前 `_extract_wrapped_client_command()` 只负责兼容已有的 wrapped-client 参数；
   它只能在用户未显式传入 `--tap-client-cmd` 时补值，不能覆盖公开参数。
3. 显式路径校验失败时由现有启动链返回明确错误；对 `astron` 禁止继续 PATH/npm
   fallback。
4. POSIX 校验绝对路径、普通文件和可执行权限；Windows 复用现有
   `_prefer_windows_command_shim()` 处理 `.cmd`/`.bat`，并兼容 `.exe`。
5. `npm prefix -g` 和版本探测均使用无 shell 子进程、短超时和显式退出码处理。

示例：

```bash
claude-tap \
  --tap-client astron \
  --tap-client-cmd "/absolute/path/to/astron-code" \
  --tap-store-stream-events
```

### 8.3 Forward proxy 行为

`astron` 启动时：

1. 设置大小写形式的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`。
2. 注入 claude-tap CA 所需的 `CODEX_CA_CERTIFICATE`、`SSL_CERT_FILE` 等现有环境。
3. 保留现有 `NO_PROXY` 对 localhost/loopback 的处理。
4. 不调用 `_codex_reverse_args()`。
5. 不注入 `model_provider="claude-tap-openai"`。
6. 不修改 AstronCode 的 `base_url`、`models_base_url` 或 Apps/MCP endpoint。

`astron + reverse` 的拒绝行为属于本次实现项，不是建议项：

1. `parse_args()` 完成 client 和 proxy mode 默认值解析后，若
   `args.client == "astron"` 且 `args.proxy_mode != "forward"`，调用
   `tap_parser.error("--tap-client astron only supports forward proxy mode")`。
2. 拒绝必须发生在创建代理、调用 `run_client()` 或构造 `_codex_reverse_args()` 前。
3. `--tap-client astron` 未显式指定 proxy mode 时正常得到 `forward`。
4. 其他客户端现有的 proxy mode 选择保持不变。

### 8.4 全量上游采集语义

本计划中的“全量上游采集”定义为：

- 记录所有实际经过 claude-tap forward proxy；
- 且根据 claude-tap 当前通用规则属于可记录对象；
- 且由 claude-tap 现有通用 Trace/header 处理允许记录的 HTTP/SSE 请求和响应。

不得为 `astron` 增加只允许以下路径的限制：

```text
/responses
/v1/responses
```

预期至少覆盖：

- Responses API；
- Models API；
- Apps/Connectors 目录；
- Apps/远程插件 Streamable HTTP MCP 初始化；
- `tools/list`；
- `tools/call`；
- MCP SSE 返回；
- 健康检查和实际运行过程中出现的其他上游 HTTP 请求。

缓存命中、本地文件读取和 stdio 交互没有实际上游 HTTP 请求，因此不属于缺失采集。

### 8.5 Viewer 和分类

第一阶段：

1. `/responses` 继续复用现有语义化 Viewer。
2. 其他请求使用现有原始请求/响应展示。
3. 不因无法语义化解析而丢弃请求。

建议增加非破坏性的 Trace 分类元数据：

- `model_response`
- `model_catalog`
- `app_directory`
- `mcp_initialize`
- `mcp_tools_list`
- `mcp_tools_call`
- `mcp_other`
- `other_upstream`

分类失败时必须回退为 `other_upstream`，不能删除 Trace。

### 8.6 响应驱动的 SSE 转发与保留

AstronCode 只使用 HTTP/SSE，不要求 WebSocket 支持作为验收条件。

当前 forward proxy 在收到上游响应前，主要根据请求 path/body 的 `stream` 信息决定
是否进入 `_handle_streaming()`。MCP JSON-RPC 请求通常没有 `stream: true`，但上游
仍可返回 `Content-Type: text/event-stream`；此时旧实现会走 non-streaming 分支并在
转发前缓冲完整响应。本次必须修复：

1. 收到上游响应后解析 `Content-Type` media type；大小写不敏感，并忽略
   `; charset=...` 等参数。
2. 请求启发式判断为 streaming，或成功响应的 media type 为
   `text/event-stream`，任一成立即进入流式转发，不得先调用完整 body `read()`。
3. 对响应驱动识别出的 MCP SSE 复用 `_handle_streaming()` 和 `SSEReassembler`；
   未知 JSON-RPC/MCP event 不要求重建为 Responses 语义，但开启
   `--tap-store-stream-events` 时必须按顺序保留原始 event/data。
4. 普通 JSON 响应仍走 non-streaming 路径，避免改变既有行为。
5. 客户端断连、取消或上游正常结束时必须有限结束，不挂起、不重复写终止 chunk。

第一阶段保留 claude-tap 现有 `--tap-store-stream-events` 开关，不全局修改其他客户端
默认值。AstronCode 真实 E2E 必须开启该参数，验证模型 SSE 和 MCP SSE 原始事件均可
保留。

是否让 `astron` 默认开启原始 SSE event 存储，需要在完成存储体积评估后单独决定。

### 8.7 本次敏感信息处理边界

本次明确不修改 claude-tap 现有通用 header/query/body 脱敏、SQLite 存储、日志、
Viewer、dashboard 或导出格式，也不把“完全重做敏感信息清洗”设为开工或验收前提。
`astron` ClientConfig 必须复用现有 `filter_headers()` 和通用 Trace 管线，不新增
Astron 专属的凭据记录或日志逻辑。

真实 E2E 报告只允许记录：

- 是否捕获；
- 请求类别；
- HTTP 状态布尔或分类；
- 顺序；
- 耗时；
- 条目数量；
- 不含敏感值的 digest。

真实 E2E 使用最小、非敏感 prompt 和测试专用/非生产凭据。不得提交原始 Trace、真实
prompt、回答、工具结果、音频、凭据或原始 provider 消息；本地原始数据沿用项目现有
清理策略。现有通用脱敏能力是否需要进一步加固，另立任务处理，不进入本计划。

## 9. 测试优先实施顺序

遵循“先补能够在旧实现失败的测试”原则。必须在修复前于本地工作树运行测试并保存真实
red-test 命令、摘要和退出码，但不提交红色中间 commit；首个包含这些测试的提交必须
同时包含最小实现并保持目标测试为绿色。

### 阶段 A：基线和 Red Tests

1. 锁定 claude-tap `origin/main` SHA。
2. 锁定 AstronCode `origin/master` SHA。
3. 运行现有 claude-tap Codex/forward proxy 目标测试，记录基线。
4. 新增并运行以下失败测试：
   - `astron` 尚未注册；
   - `astron` 尚不能默认选择 forward；
   - `--tap-client-cmd` 尚不能指定 AstronCode；
   - 当前实现尚不能从当前 npm 全局 prefix 自动发现 `astron-code`；
   - 当前实现可能错误启动 PATH 中的其他 `codex`；
   - Astron Trace 不应被限制为 `/responses`；
   - `/models`、Apps 和 MCP 路径必须被记录；
   - 无 `stream` 字段的 MCP JSON-RPC 请求收到 `text/event-stream` 时必须立即流式
     转发并保留 event；
   - `--tap-client astron --tap-proxy-mode reverse` 必须在启动前拒绝。
5. 保存命令、失败摘要和退出码到实施证据，不提交仅含失败测试的 commit。

### 阶段 B：客户端和可执行文件选择

1. 注册 `astron` ClientConfig。
2. 将默认命令设置为 `astron-code`。
3. 实现 PATH 和当前 npm 全局 prefix 自动发现。
4. 将公开 `--tap-client-cmd` 接入现有 `client_cmd` 解析和 `run_client()` 链路。
5. 完成绝对路径验证、超时处理和 fail-closed 错误信息。
6. 记录实际解析来源、绝对路径和版本。
7. 实现 `astron + reverse` parser 级拒绝，确保不进入 Codex reverse provider 注入
   分支。
8. 运行阶段 A 中对应的目标测试。

### 阶段 C：全量 HTTP/SSE 采集

使用 fake upstream 覆盖至少两个不同 origin，验证：

1. `/responses` POST + SSE；
2. `/models` GET；
3. Apps 目录 GET；
4. MCP initialize POST；
5. MCP `tools/list` POST；
6. MCP `tools/call` POST + SSE；
7. 任意其他上游路径仍被记录；
8. 非 `/responses` 请求不会因为缺少语义化解析而被丢弃；
9. 所有请求正常转发，上游返回不被修改；
10. fake MCP 对无 `stream` 字段的 JSON-RPC POST 返回分块
    `Content-Type: text/event-stream; charset=utf-8`，验证首个 chunk 在上游关闭前
    到达客户端、event/data 顺序和内容保持一致、正常结束有限完成；
11. 覆盖客户端断连/任务取消，确认不挂起；普通 `application/json` MCP 响应仍走
    non-streaming 路径。

### 阶段 D：兼容性

1. 确认 Astron profile 复用现有 `filter_headers()` 和通用 Trace 管线，没有新增凭据
   写入或日志分支。
2. 运行现有 `codex` reverse/forward 测试。
3. 运行其他使用 forward proxy 的客户端目标测试。
4. 验证历史 Trace 仍可打开。

### 阶段 E：AstronCode Master 真实 E2E

使用基于最新 `origin/master` 的干净 AstronCode 构建：

1. 将对应版本安装到当前 npm 全局环境。
2. 不传 `--tap-client-cmd`，验证自动发现并启动 npm `astron-code` shim。
3. 再通过 `--tap-client-cmd` 指定同一版本的绝对路径，验证显式覆盖。
4. 确认两种方式启动的版本和 `origin/master` 基线一致。
5. 使用不含敏感数据的最小 prompt。
6. 触发模型目录读取。
7. 触发一个 App/远程插件的工具发现和只读调用。
8. 验证模型 `/responses`、`/models`、App 目录、MCP initialize、`tools/list`、
   `tools/call` 和 SSE 顺序。
9. 验证 AstronCode 配置文件未被改写。
10. 输出经过清洗的 E2E digest，不保留或提交原始数据。

## 10. 建议测试清单

### CLI/配置测试

- `astron` 出现在 `--tap-client` choices。
- `astron` 默认代理模式为 forward。
- 显式 `--tap-client astron --tap-proxy-mode reverse` 在启动客户端前以
  `--tap-client astron only supports forward proxy mode` 拒绝。
- reverse 拒绝路径不调用 `run_client()` 或 `_codex_reverse_args()`。
- 其他客户端的显式 proxy mode 行为不变。
- 未指定路径时优先使用 PATH 中的 `astron-code`。
- PATH 未找到时可通过当前 `npm prefix -g` 发现全局 `astron-code` shim。
- npm 不存在、超时、返回非零或生成无效路径时给出明确错误。
- 不调用 `npm exec`/`npx`，也不自动下载包。
- 不把 PATH 中的 `codex` 当作 AstronCode fallback。
- `--tap-client-cmd` 使用指定二进制。
- 指定路径不存在时不回退 PATH 或 npm 候选。
- 路径中有空格时可启动。
- macOS/Linux npm shim 和 Windows `.cmd` shim 均有覆盖。

### 代理测试

- 注入 HTTP/HTTPS/ALL proxy 环境。
- 注入自定义 CA 环境。
- 不注入 Codex reverse provider 参数。
- 不修改 AstronCode provider。
- HTTP 和 SSE 均可正常流式转发。
- 无 `stream` 请求字段时，`Content-Type: text/event-stream` 响应仍立即流式转发。
- `Content-Type` 匹配大小写不敏感并忽略参数。
- 首个 SSE chunk 在上游关闭前到达客户端，断连/取消可有限结束。
- 普通 JSON 响应不误走 SSE 分支。
- 多 origin 请求均可捕获。

### 采集测试

- Responses 语义化展示。
- Models 原始 JSON 展示。
- App 目录原始 JSON 展示。
- MCP JSON-RPC 请求/响应展示。
- MCP SSE 原始 event 展示。
- 任意非白名单路径仍保留。
- 缓存命中时不误报为网络采集失败。

### 回归测试

- 官方 `codex` 默认仍为 reverse。
- `codexapp` 默认仍为 forward。
- 其他客户端配置和 choices 不变。
- 已有导出、dashboard 和历史 Trace 不变。

## 11. 验收标准

全部满足才可认为完成：

1. `--tap-client astron` 可用。
2. `astron` 默认使用 forward proxy。
3. `--tap-client astron --tap-proxy-mode reverse` 在创建代理和启动客户端前以指定错误
   文案拒绝；默认 forward 和其他客户端模式不受影响。
4. 未指定路径时能从当前 PATH 或当前 npm 全局 prefix 自动发现 `astron-code`。
5. `--tap-client-cmd` 复用现有 `args.client_cmd` →
   `_resolve_client_executable()` → `run_client(client_cmd=...)` 链路并可靠覆盖自动发现
   结果；没有新增平行启动实现。
6. 不依赖 PATH 中的 `codex` 是 AstronCode。
7. 不修改 AstronCode provider 和配置文件。
8. 不设置 `/responses` 专属采集白名单。
9. `/responses`、`/models`、Apps 和 Streamable HTTP/SSE MCP 均有 Trace 证据。
10. MCP JSON-RPC 请求未声明 `stream` 时，只要成功响应的 media type 为
    `text/event-stream`，首个 chunk 就在上游关闭前转发；原始 event 顺序可保存，
    断连/取消有限结束。
11. 普通 JSON 响应不误走 SSE 分支。
12. 任意其他经过代理且符合通用规则的上游请求不会被 Astron 专用逻辑删除。
13. Astron profile 复用现有通用 header/Trace 处理，不引入新的凭据存储或日志行为。
14. 现有 claude-tap 客户端和采集能力未删除、未退化。
15. 目标测试、forward proxy 回归测试和 AstronCode `master` 真实 E2E 通过。
16. 测试报告区分目标回归结果与仓库既有基线失败。
17. Git 历史中没有仅含失败测试的红色中间 commit；red-before-fix 只以命令、摘要和
    退出码作为实施证据保留。

## 12. 提交拆分建议

为降低审查风险，保留实现关注点边界，但不提交红色中间状态：

1. 代理提交（绿色）：响应 `Content-Type` 驱动的通用 SSE 转发、MCP SSE
   red-before-fix 证据对应测试和最小实现；提交前相关目标测试通过。
2. Astron 客户端提交（绿色）：`astron` ClientConfig、明确拒绝 reverse、npm 全局
   自动发现，以及将公开 `--tap-client-cmd` 接入现有解析/启动链；测试与最小实现同一
   提交，提交前相关目标测试通过。
3. 展示提交（可选）：Trace 分类标签，不改变原始存储。
4. 闭环提交：文档、清洗后的 E2E digest 和最终验证记录。

Red tests 只在修复前的本地工作树运行并保留命令、摘要和退出码；不得创建、推送或提交
仅含失败测试的 commit。

不得为了整理提交而改写已共享历史。

## 13. 风险与缓解

### 风险 1：全量采集导致噪声和存储增长

- 缓解：保留全量原始采集，先增加分类和 Viewer 过滤，不删除原始条目。
- 后续基于真实数据评估保留数量、压缩和生命周期，不在第一阶段提前裁剪。

### 风险 2：敏感数据进入 Trace

- 范围约束：本次不扩修现有通用脱敏和存储行为。真实 E2E 使用最小非敏感 prompt 与
  测试专用/非生产凭据；只提交 digest、布尔、顺序、计数和耗时，不提交原始 Trace。
- 如发现现有通用脱敏缺陷，单独记录并另立任务，不阻塞本计划已定义的 Astron/SSE
  实施范围。

### 风险 3：部分调用来自缓存

- 缓解：E2E 使用可控刷新动作；报告区分“缓存命中无网络请求”和“网络请求未捕获”。

### 风险 4：远程 executor 不继承本地代理

- 缓解：第一阶段验收本地 AstronCode 进程和本地 Streamable HTTP MCP；远程 executor
  作为独立部署边界记录。

### 风险 5：自动发现到错误的 npm/Node 环境

- 缓解：优先使用当前 shell PATH 中的 `astron-code`，其次只查询当前 `npm prefix -g`；
  记录发现来源、绝对路径和版本。多套 npm/Node 环境无法唯一判断时 fail closed，并
  要求使用 `--tap-client-cmd`。

### 风险 6：上游 claude-tap 更新改变代理或 Trace 格式

- 缓解：实施时锁定 main SHA；保留目标回归测试；升级时重新执行 Astron E2E。

## 14. 外部依赖边界

以下失败不应直接归因于 claude-tap Astron client：

- Astron 模型服务不可用；
- Apps/Connectors 目录服务不可用；
- 第三方 App 或 MCP 服务超时；
- 鉴权账号无权限；
- AstronCode `master` 本身无法构建或基线测试失败；
- 远程 executor 未配置相应代理；
- 本地系统不信任或阻止代理 CA。

报告必须分别给出：

- claude-tap 代理/采集是否工作；
- AstronCode 是否实际发起请求；
- 上游是否返回成功；
- 外部服务是否构成 blocker。

## 15. 回滚策略

第一阶段不修改 AstronCode 配置，因此回滚只涉及 claude-tap：

1. 停止使用 `--tap-client astron`。
2. 使用原有 claude-tap 版本或移除新增 ClientConfig。
3. 不需要还原 AstronCode provider、base URL 或用户配置。
4. 已生成 Trace 按本地数据清理策略处理，不自动上传。

## 16. 最终交付物

- `astron` ClientConfig；
- `astron + reverse` parser 级拒绝；
- npm 全局 `astron-code` 自动发现；
- 复用现有客户端启动链的公开 `--tap-client-cmd`；
- 响应 `Content-Type` 驱动的 MCP SSE 流式转发；
- Red-test 证据和退出码；
- fake multi-origin HTTP/SSE 集成测试；
- 现有客户端回归结果；
- AstronCode `origin/master` 构建信息；
- 经过清洗的真实 E2E digest；
- 使用说明与安全注意事项。
