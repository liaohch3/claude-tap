"""Unit tests for scripts/check_pr_policy.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_pr_policy.py"
MODULE_NAME = "check_pr_policy"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_policy_requires_raw_screenshot_for_client_changes() -> None:
    module = _load_module()
    body = """## Summary
- Add a client.

## Test plan
- `uv run pytest tests/ -x --timeout=60`
"""

    result = module.validate_policy(body, ["claude_tap/cli.py"])

    assert result.ok is False
    assert any("no raw.githubusercontent.com screenshot evidence" in item for item in result.failures)


def test_policy_accepts_raw_screenshot_for_runtime_changes() -> None:
    module = _load_module()
    body = """## Summary
- Update viewer behavior.

## Validation
- `uv run pytest tests/ -x --timeout=60`

## Evidence
![viewer](https://raw.githubusercontent.com/liaohch3/claude-tap/branch/.agents/evidence/pr/viewer.png)
"""

    result = module.validate_policy(body, ["claude_tap/viewer.html"])

    assert result.ok is True


def test_policy_requires_raw_screenshot_for_package_runtime_changes() -> None:
    module = _load_module()
    body = """## Summary
- Update HTML export behavior.

## Validation
- `uv run pytest tests/ -x --timeout=60`
"""

    result = module.validate_policy(body, ["claude_tap/export.py"])

    assert result.ok is False
    assert any("no raw.githubusercontent.com screenshot evidence" in item for item in result.failures)


def test_policy_rejects_non_raw_image_urls() -> None:
    module = _load_module()
    body = """## Summary
- Update viewer behavior.

## Validation
- `uv run pytest tests/ -x --timeout=60`

## Evidence
![viewer](https://github.com/liaohch3/claude-tap/blob/branch/viewer.png)
"""

    result = module.validate_policy(body, ["claude_tap/viewer.html"])

    assert result.ok is False
    assert "PR image evidence must use raw.githubusercontent.com URLs" in result.failures


def test_policy_blocks_raw_trace_artifacts() -> None:
    module = _load_module()
    body = """## Summary
- Add evidence.

## Validation
- `uv run pytest tests/ -x --timeout=60`
"""

    result = module.validate_policy(body, [".traces/2026-05-20/trace_120000.jsonl"])

    assert result.ok is False
    assert "PR includes raw trace, generated viewer, log, or secret-like files" in result.failures


def test_policy_allows_docs_without_runtime_evidence() -> None:
    module = _load_module()
    body = """## Summary
- Clarify docs.

## Validation
- Reviewed only.
"""

    result = module.validate_policy(body, ["docs/guides/example.md"])

    assert result.ok is True


def test_policy_reads_github_event_body(tmp_path: Path, capsys) -> None:
    module = _load_module()
    event = tmp_path / "event.json"
    files = tmp_path / "files.txt"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "body": """## Summary
- Add a client.

## Validation
- `uv run pytest tests/ -x --timeout=60`
"""
                }
            }
        ),
        encoding="utf-8",
    )
    files.write_text("claude_tap/cli.py\n", encoding="utf-8")

    exit_code = module.main(["--event-path", str(event), "--changed-files-file", str(files)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "PR Policy: FAIL" in output
