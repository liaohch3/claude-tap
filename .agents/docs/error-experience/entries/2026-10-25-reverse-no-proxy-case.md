# Reverse clients inherited conflicting proxy bypass variables

## Symptom

Issue #474 reports loopback requests receiving HTTP 502 with WSL2 and Clash.
The reverse launcher assigned only uppercase `NO_PROXY`; inherited lowercase
`no_proxy` could take precedence and did not contain the literal `127.0.0.1`.
The existing `127.*` entry is not a portable proxy bypass glob.

## Root cause and fix

Reuse `_extend_no_proxy` for every reverse client, as MiMo already did. This
preserves both inherited lists, adds explicit loopback addresses, synchronizes
both variable names, and retains the existing wildcard behavior. Network proxy
variables remain available for remote upstream requests.

## Regression evidence

A real subprocess, real rejecting HTTP proxy, tap reverse handler, HTTP upstream,
and SQLite trace store reproduce HTTP 502 before the change and HTTP 200 after.
The test checks the proxy receives no loopback request and the successful request
is persisted. It covers Claude, Codex, and Pi launch paths. See the PR evidence
record for authenticated CLI runs and the limits of the local environment.

## Lesson

When inheriting proxy settings, keep both case variants consistent. Validate
actual request routing and storage rather than asserting environment values alone.
