# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`--tap-open` flag**: Automatically open HTML viewer in browser after exit
- **Token statistics**: Summary now shows detailed token breakdown (input/output/cache_read/cache_write)
- Type annotations for all public functions
- `__all__` export declaration
- Coverage configuration in pyproject.toml
- This CHANGELOG.md file

### Changed
- Migrated tests to pytest with proper structure (`tests/` directory)
- Updated CLI options documentation in README

### Removed
- Cost estimation feature (pricing data is hard to maintain accurately)

## [0.1.3] - 2026-02-16

### Added
- `-v/--version` CLI flag
- PyPI badges in README
- Pre-commit hooks configuration
- pytest-based test infrastructure

### Changed
- Applied ruff formatting to all Python files

## [0.1.2] - 2026-02-15

### Added
- Structural diff view in HTML viewer
- Side-by-side comparison for consecutive requests
- Turn ordering fix

## [0.1.1] - 2026-02-15

### Fixed
- Stdout buffering issue with uv tool
- Transparent argument passthrough to claude

## [0.1.0] - 2026-02-15

### Added
- Initial release
- Local reverse proxy for Claude Code API requests
- JSONL trace recording
- Self-contained HTML viewer with:
  - Light/dark mode
  - i18n support (8 languages)
  - Token usage display
  - SSE event inspection
  - System prompt viewing
  - cURL export
