# Python 3.13 SSL 要求证书包含 Authority Key Identifier

**日期：** 2026-02-26
**严重级别：** 高（CI 阻塞 - 连续 4 次失败）
**标签：** ssl, certificates, python313, ci, certs.py

## 问题

`test_forward_proxy_connect` 在 Python 3.13 下失败，报错如下：

```
SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: Missing Authority Key Identifier (_ssl.c:1032)')
```

CI 在 Python 3.11/3.12 通过，但在 3.13 持续失败。

## 根因

Python 3.13 收紧了 SSL 证书校验。它现在**要求**由 CA 签发的证书包含
Authority Key Identifier（AKI）扩展。

我们的 `certs.py` 生成结果：
- CA 证书：缺少 `SubjectKeyIdentifier`（SKI）
- Host 证书：缺少 `AuthorityKeyIdentifier`（AKI）和 SKI

这些扩展在 X.509 中技术上可选，但 Python 3.13 的 SSL 实现会将缺失 AKI
视为校验失败。

## 为什么本地未捕获

本地开发环境是 Python 3.11，不强制 AKI 存在。
该问题只在 CI 的 Python 3.13 matrix 中暴露。

## 修复

在 `certs.py` 中为两类证书都加入正确的 X.509 扩展：

**CA 证书：**
```python
.add_extension(
    x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
    critical=False,
)
```

**Host 证书：**
```python
.add_extension(
    x509.AuthorityKeyIdentifier.from_issuer_public_key(
        self._ca_key.public_key()
    ),
    critical=False,
)
.add_extension(
    x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
    critical=False,
)
```

## 经验

1. **在假定测试通过之前，务必检查 CI 的全部 Python 版本。**
   本地 3.11 绿灯不代表 3.13 也绿灯。
2. **自签证书生成必须包含 SKI/AKI 扩展。**
   即使旧版 Python 容忍缺失，新版也可能不容忍。
3. **当 CI 只在特定 Python 版本失败时，先看该版本 changelog 是否有更严格的安全要求**。
   SSL/TLS 校验是常见收紧区域。
