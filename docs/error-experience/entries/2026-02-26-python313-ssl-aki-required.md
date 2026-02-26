# Python 3.13 SSL Requires Authority Key Identifier in Certificates

**Date:** 2026-02-26
**Severity:** High (CI blocker — 4 consecutive failures)
**Tags:** ssl, certificates, python313, ci, certs.py

## Problem

`test_forward_proxy_connect` failed on Python 3.13 with:

```
SSLCertVerificationError: (1, '[SSL: CERTIFICATE_VERIFY_FAILED]
certificate verify failed: Missing Authority Key Identifier (_ssl.c:1032)')
```

CI passed on Python 3.11/3.12 but consistently failed on 3.13.

## Root Cause

Python 3.13 tightened SSL certificate validation. It now **requires** the
Authority Key Identifier (AKI) extension in certificates signed by a CA.

Our `certs.py` generated:
- CA cert: missing `SubjectKeyIdentifier` (SKI)
- Host cert: missing `AuthorityKeyIdentifier` (AKI) and SKI

These extensions are technically optional per X.509 but Python 3.13's SSL
implementation treats missing AKI as a verification failure.

## Why It Wasn't Caught Locally

Local dev environment ran Python 3.11, which does not enforce AKI presence.
The issue only surfaced in CI's Python 3.13 matrix.

## Fix

Added proper X.509 extensions to both certificate types in `certs.py`:

**CA certificate:**
```python
.add_extension(
    x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
    critical=False,
)
```

**Host certificate:**
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

## Lesson Learned

1. **Always check CI across all Python versions before assuming tests pass.**
   Local green on 3.11 doesn't mean green on 3.13.
2. **Self-signed certificate generation must include SKI/AKI extensions.**
   Even if older Python versions tolerate their absence, newer versions won't.
3. **When CI fails on a specific Python version, check that version's changelog
   for tightened security requirements** — SSL/TLS validation is a common area
   for stricter enforcement.
