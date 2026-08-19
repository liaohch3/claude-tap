from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


def test_check_schema_rejects_new_dict_annotation(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text("value: dict = {}\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_paths({source: {1}}) == [
        f"{source}:1: dict annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_allows_fully_parameterized_container(tmp_path: Path) -> None:
    source = tmp_path / "explicit.py"
    source.write_text("value: dict[str, list[int]] = {}\n", encoding="utf-8")
    module = _load_schema_checker()
    assert module.check_paths({source: {1}}) == []


def test_check_schema_rejects_dynamic_parameterized_container(tmp_path: Path) -> None:
    source = tmp_path / "dynamic.py"
    source.write_text("value: dict[str, object] = {}\n", encoding="utf-8")
    module = _load_schema_checker()
    assert module.check_paths({source: {1}}) == [
        f"{source}:1: dict annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_rejects_bare_list_annotation(tmp_path: Path) -> None:
    source = tmp_path / "bare_list.py"
    source.write_text("value: list = []\n", encoding="utf-8")
    module = _load_schema_checker()
    assert module.check_paths({source: {1}}) == [f"{source}:1: bare list annotation; declare its member schema"]


def test_check_schema_does_not_hide_bare_container_beside_parameterized_container(tmp_path: Path) -> None:
    source = tmp_path / "mixed.py"
    source.write_text("value: dict | dict[str, str] = {}\n", encoding="utf-8")
    module = _load_schema_checker()
    assert module.check_paths({source: {1}}) == [
        f"{source}:1: dict annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_rejects_any_annotation(tmp_path: Path) -> None:
    source = tmp_path / "bad_any.py"
    source.write_text("from typing import Any\nvalue: Any = None\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_paths({source: {2}}) == [
        f"{source}:2: new Any annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_rejects_qualified_any_annotation(tmp_path: Path) -> None:
    source = tmp_path / "bad_qualified_any.py"
    source.write_text("import typing\nvalue: typing.Any = None\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_paths({source: {2}}) == [
        f"{source}:2: new Any annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_rejects_legacy_json_alias(tmp_path: Path) -> None:
    source = tmp_path / "good.py"
    source.write_text("from claude_tap.models import JsonObject\nvalue: JsonObject = {}\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_paths({source: {2}}) == [
        f"{source}:2: dict annotation; use a Pydantic model or ProviderPayload"
    ]


def test_check_schema_full_scan_rejects_existing_mapping(tmp_path: Path) -> None:
    source = tmp_path / "legacy.py"
    source.write_text("from collections.abc import Mapping\nvalue: Mapping[str, object] = {}\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_repository([source]) == [
        f"{source}:2: Mapping annotation; use a Pydantic model or explicit JSON boundary"
    ]


def test_check_schema_full_scan_rejects_typed_dict(tmp_path: Path) -> None:
    source = tmp_path / "legacy_typed_dict.py"
    source.write_text("from typing import TypedDict\nclass Payload(TypedDict):\n    value: str\n", encoding="utf-8")
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_repository([source]) == [f"{source}:2: use a Pydantic BaseModel instead of TypedDict"]


def test_prompt_models_validate_and_serialize() -> None:
    from claude_tap.models import PromptSnapshotModel, PromptToolModel, ProviderPayload

    tool = PromptToolModel(schema={"type": "object"}, raw={"name": "search"}, name="search")
    snapshot = PromptSnapshotModel(provider="openai", model="gpt-test", tools=(tool,))
    assert snapshot.tools[0].schema == {"type": "object"}
    assert snapshot.model_dump(by_alias=True)["tools"][0]["schema"] == {"type": "object"}
    assert isinstance(snapshot.tools[0].schema, ProviderPayload)


def test_provider_payload_preserves_unknown_json_fields_and_mapping_semantics() -> None:
    from claude_tap.models import ProviderPayload

    payload = ProviderPayload.model_validate({"future_field": {"nested": [1, True, None]}})
    payload["added"] = "value"

    assert dict(payload) == {"future_field": {"nested": [1, True, None]}, "added": "value"}
    assert payload.model_dump() == dict(payload)
    with pytest.raises(ValidationError):
        ProviderPayload.model_validate({"not_json": Path("/tmp")})
    with pytest.raises(ValidationError):
        payload["not_json"] = Path("/tmp")  # type: ignore[assignment]


def test_schema_checker_excludes_unit_and_e2e_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_schema_checker()
    for relative in ("claude_tap/runtime.py", "scripts/tool.py", "tests/test_runtime.py", "tests/e2e/test_real.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("value: object = None\n", encoding="utf-8")

    covered = {path.relative_to(tmp_path) for path in module.repository_paths(tmp_path)}
    assert covered == {Path("claude_tap/runtime.py"), Path("scripts/tool.py")}

    diff = """diff --git a/tests/test_runtime.py b/tests/test_runtime.py
+++ b/tests/test_runtime.py
@@ -0,0 +1 @@
+value: object = None
diff --git a/claude_tap/runtime.py b/claude_tap/runtime.py
+++ b/claude_tap/runtime.py
@@ -0,0 +1 @@
+value: object = None
"""
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=diff))
    assert module._changed_lines("origin/main") == {Path("claude_tap/runtime.py"): {1}}


def test_check_schema_script_is_executable() -> None:
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    assert script.exists()


def _load_schema_checker():
    script = Path(__file__).parents[1] / "scripts" / "check_schema.py"
    spec = importlib.util.spec_from_file_location("check_schema", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
