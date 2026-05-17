# Empty Trace Viewer Evidence

Date: 2026-05-17

## Scope

This evidence covers the viewer behavior for a real generated trace file with zero captured API calls. The page must render an explicit empty trace state instead of falling back to the generic file picker.

## Artifacts

- `empty-trace.jsonl`: empty JSONL trace input
- `empty-trace-viewer.png`: Playwright screenshot at 1440x900

The generated self-contained `empty-trace.html` was used during local validation but is intentionally not committed because it duplicates the full viewer template and adds thousands of low-signal review lines.

## Assertions Added

- Empty embedded trace renders `.empty-trace-state`
- Viewer shows `No API calls captured`
- Viewer shows `Captured API calls: 0`
- Sidebar entries are absent and the sidebar/detail panels are hidden
- Trace path bar still shows both JSONL and HTML paths
- The unloaded file-picker template is still rejected by `verify_screenshots.py`
- PNG blankness analysis can sample large screenshots while still rejecting blank white images

## Validation Run

- `uv run pytest tests/test_check_coverage.py tests/test_check_screenshots.py tests/test_verify_screenshots.py tests/test_viewer_contracts.py::test_viewer_empty_embedded_trace_renders_explicit_no_api_calls_state -q`
- `uv run pytest tests/test_translate_i18n.py -q`
- `uv run pytest tests/test_viewer_contracts.py -q`
- `uv run python scripts/verify_screenshots.py <generated empty-trace.html>`
- `uv run python scripts/check_screenshots.py .agents/evidence/pr/empty-trace-viewer/empty-trace-viewer.png`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pytest tests/ -x --timeout=60`
- `uv run python scripts/check_coverage.py --base origin/main --skip-python`

## Results

- Targeted tests: 16 passed
- Full viewer contract tests: 12 passed
- Full test suite: 358 passed, 25 skipped, 4 warnings
- PR screenshot validation: 1 passed, 0 warnings, 0 failures
- Viewer coverage gate: JS diff 100.00% >= 80.00%; CSS diff 100.00% >= 80.00%
- Viewer project coverage: JS functions 62.81% >= 50.00%; CSS selectors 76.52% >= 65.00%
