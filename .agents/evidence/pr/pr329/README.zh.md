# PR 329 — Session Detail 页返回按钮

仪表盘会话详情页头部 `←` 返回按钮的截图。

**改动：**
- 详情页头部的返回按钮调用 `showListView()`
- 返回列表前清除 `state.selectedSessionId`（修复请求竞态问题）

**源：**
- 通过 `claude-tap dashboard` 启动本地 dashboard：`http://localhost:3000`
- 真实轨迹：任意 session id 的会话详情页
