# Hardcoded Version String in CLI

**Date:** 2026-02-26
**Severity:** Medium
**Tags:** versioning, cli, metadata, release

## Problem

`__version__` in `cli.py` was hardcoded as `"0.1.7"` and never updated on
release. Users saw the wrong version with the `-v` flag even after upgrading.

## Root Cause

The version string was a literal in source code instead of reading from package
metadata.

## Fix

Replaced the hardcoded value with `importlib.metadata.version("claude-tap")` so
it always matches `pyproject.toml` and PyPI package metadata.

## Lesson Learned

Never hardcode version strings. Always use `importlib.metadata` (or another
single source of truth such as dynamic reading from `pyproject.toml`).
