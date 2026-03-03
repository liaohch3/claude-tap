# 面向 Proxy 的 E2E 与 CI 强化

**日期：** 2026-02-25  
**标签：** e2e, proxy, oauth, ci, reliability

## 背景

真实 E2E 覆盖曾被环境相关行为阻塞：

- forward-proxy 运行中的 OAuth 流程出现 `403 Request not allowed`。
- CI 在 Python 3.13 下因 forward proxy 测试中的 TLS 证书校验更严格而失败。
- 测试预期误以为首个 API 调用总是 `/v1/messages`，但 OAuth 预检并非如此。

## 有效做法

1. **上游转发尊重系统 proxy env**
   - 在 claude-tap 上游转发使用的 aiohttp session 中设置 `trust_env=True`。
   - 使流量能遵循本地 proxy/VPN 工具（如 Clash），而不是绕过它。

2. **提升 forward/OAuth 断言鲁棒性**
   - 不假设首条 trace 请求是 `/v1/messages`。
   - 在记录中搜索至少一条 `/v1/messages` 调用，并在该处验证响应内容。

3. **为 Python 3.13 强化测试证书**
   - 在自签测试证书中同时添加 `SubjectKeyIdentifier` 与 `AuthorityKeyIdentifier` 扩展。
   - 解决 CI 中 `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`。

4. **修复 trace 统计的空值安全**
   - 在收集 model usage 指标时防护 `request.body is None`。

## 为什么重要

- 真实 E2E 可在 proxy 重环境的开发机中通过，避免隐藏的网络路径不一致。
- CI 在不同 Python 版本间更稳定。
- 测试验证的是行为本身，而不是偶然的请求顺序。

## 运行说明

- 真实 E2E 仍通过 `--run-real-e2e` opt-in。
- Browser integration 测试需要安装 Playwright 与浏览器二进制。
- 调试 proxy 问题时，两个链路都要验证：
  - Claude CLI -> claude-tap
  - claude-tap -> upstream（必要时必须遵守 proxy env）
