# PR 截图质量失败案例（2026-03-03）

## 发生了什么

PR #22（WebSocket proxy 修复）需要截图证据。在产出可接受截图前，先后出现了三次质量失败：

### 失败 1：移动端 Viewport 布局
- **症状**：Trace viewer 渲染为移动端布局，拥挤、单列、难以阅读
- **根因**：OpenClaw 内置浏览器默认窄 viewport（约 750px），触发响应式移动端断点
- **影响**：截图展示的是移动端 UI，和用户实际看到的不一致

### 失败 2：Unicode 箭头乱码
- **症状**：日志中的箭头 `→` 与 `←` 在截图里显示为乱码 `鉞@`
- **根因**：日志文件包含 Unicode 箭头。headless 环境中的浏览器或字体渲染破坏了多字节字符；原始 `.log` 被直接提供且未处理 charset。
- **影响**：关键证据（WS 方向标记）不可读

### 失败 3：截图内容错误
- **症状**：Trace viewer 显示的是 `/v1/models` 请求详情，而非 WebSocket `/v1/responses` 请求
- **根因**：截图前没有先点击到正确 trace entry，误以为默认视图就是 WS 请求
- **影响**：截图无法证明 PR 声称的内容

### 元失败：缺少提交前审查
- 三张错误截图都在未先审查的情况下被 commit 并 push 到 PR
- 用户不得不手动检查并逐个指出问题
- 多轮往返才修复本应在 commit 前捕获的问题

## 如何修复

1. **Viewport**：截图前执行 `browser act resize width=1440 height=900`
2. **Unicode**：创建自定义 HTML 卡片（`ws-log-clean.html`），使用 HTML entities（`&gt;` `&lt;` `->`）替代原始 Unicode 箭头
3. **内容**：先导航到正确 trace entry（Turn 2 WEBSOCKET），确认内容后再截图
4. **审查**：commit 前逐张肉眼检查截图

## 形成的标准

### 截图提交前 Checklist
1. **Viewport**：截图前设为桌面宽度（≥1280px）
2. **内容**：确认截图展示的内容与声明完全一致
3. **编码**：检查是否有乱码/损坏字符，尤其是 Unicode 符号、CJK 文本、emoji
4. **布局**：确认渲染为桌面布局（不是移动端响应断点）
5. **可读性**：关键证据文本在 1x 缩放下可清晰阅读
6. **审查**：`git add` 前查看实际 PNG 文件，禁止盲目提交

### 自动化机会
- `scripts/check_screenshots.sh`：自动检查图像尺寸（拒绝宽度 <1000px，疑似移动端）、文件大小（拒绝 <10KB，疑似错误页）和基础合理性
- PR 正文模板可以加入截图 checklist 章节
- 可考虑通过固定 viewport 设置程序化生成证据截图

## 预防

- 已在 `docs/standards/e2e-and-evidence.md` 增加截图质量 gate
- 已新增 `scripts/check_screenshots.sh` 用于 pre-commit 自动验证
- AGENTS.md 应对任何包含视觉证据的 PR 引用该截图标准

## 关键结论

截图是证据。证据必须在提交前验证。“我截了图”不等于“我验证了这张图能证明我的结论”。
