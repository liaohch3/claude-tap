# Standalone Project Shell

## Requirement

The change must create a minimum standalone Python project that can be built, tested, and run without importing `claude_tap` runtime modules. The project must keep its package, CLI entry point, tests, and README isolated from the current viewer-heavy package so the core runner can evolve independently.

## Scenarios

### Scenario: Fresh project layout

**WHEN** a maintainer opens the standalone subproject
**THEN** they can identify package source, tests, CLI entry point, and README without reading the old viewer code
**AND** the package metadata declares only the dependencies needed for proxying, certs, logging, and tests.

### Scenario: Existing project remains intact

**WHEN** the standalone project is added in this repository
**THEN** current `claude_tap` package imports and tests remain unaffected
**AND** no existing public docs or UI files are required to run the standalone tool.

### Scenario: Missing optional extras

**WHEN** development-only packages are not installed
**THEN** the runtime CLI still imports and reports help successfully
**AND** test-only dependencies are not required for normal operation.

## Interface

### Props (if UI component)

Not applicable.

### API Contract (if endpoint)

| Surface | Method | Request | Response |
|---------|--------|---------|----------|
| CLI package | build/run | `python -m <package>` or console script | Help text or command execution |
| Project metadata | packaging | `pyproject.toml` | PEP 621 metadata with minimal dependencies |
| Test suite | pytest | `uv run pytest <standalone-tests>` | Passing focused unit/integration tests |

## Persistence (if applicable)

| Storage | Key | Value | Lifecycle |
|---------|-----|-------|-----------|
| Filesystem | standalone project directory | Source, tests, README, package metadata | Created during implementation and versioned |

---
*Spec for: standalone-proxy-runner*
*Created: 2026-05-20T22:04:03Z*
