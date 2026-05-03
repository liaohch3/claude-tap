# Security Policy

`claude-tap` intercepts and records API traffic from local AI coding clients. Security reports may involve credentials, proxy routing, generated certificates, trace files, or private prompt data.

## Supported Versions

Security fixes target the latest published PyPI release and the `main` branch.

## Reporting a Vulnerability

Please do not open a public GitHub issue for security-sensitive reports.

Use GitHub private vulnerability reporting if it is available on this repository. If private reporting is not available, contact the maintainer privately before sharing exploit details, credentials, private traces, or reproduction data.

Include:

- A concise description of the issue
- Affected versions or commits
- Steps to reproduce with sanitized data
- Whether credentials, traces, local files, or generated certificates may be exposed
- Any known mitigation or workaround

## Trace Data

Do not attach raw `.traces/*.jsonl`, generated HTML viewers, screenshots, or recordings unless you have reviewed and redacted them. Trace files can contain prompts, tool schemas, file paths, response bodies, and other private context even when API keys are redacted.

## Scope

Security-sensitive areas include:

- API key, auth token, cookie, or header handling
- Trace redaction and export behavior
- Reverse proxy and forward proxy routing
- `--tap-host`, `--tap-no-launch`, and remote binding behavior
- Generated CA certificates and per-host TLS certificates
- Generated viewer HTML that may contain private trace data

## Public Disclosure

Please allow maintainers time to investigate and prepare a fix before public disclosure.
