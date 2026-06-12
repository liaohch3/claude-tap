"""Portable compact trace bundle helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any

COMPACT_TRACE_MARKER = "__claude_tap_compact_trace__"
COMPACT_RECORD_MARKER = "__claude_tap_compact_record__"
BLOB_REF_MARKER = "__claude_tap_blob_ref__"
BLOB_KIND_JSON = "json"
COMPACT_RECORD_VERSION = 1
COMPACT_TRACE_VERSION = 1
MIN_BLOB_BYTES = 512
COMPACT_BLOB_PATHS = (
    ("request", "body", "instructions"),
    ("request", "body", "tools"),
    ("response", "body", "instructions"),
    ("response", "body", "tools"),
)
COMPACT_ITEM_BLOB_PATHS = (
    ("request", "body", "input"),
    ("request", "body", "messages"),
)


def dump_compact_trace(records: list[dict[str, Any]]) -> str:
    """Serialize records into a portable compact trace bundle."""
    return json.dumps(build_compact_trace_bundle(records), ensure_ascii=False, separators=(",", ":")) + "\n"


def build_compact_trace_bundle(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a standalone compact trace bundle with inline blob dictionary."""
    blobs: dict[str, dict[str, Any]] = {}
    compact_records = [_encode_compact_record(record, blobs) for record in records]
    return {
        COMPACT_TRACE_MARKER: {
            "version": COMPACT_TRACE_VERSION,
            "encoding": "json-blob-ref",
            "record_count": len(compact_records),
            "blob_count": len(blobs),
        },
        "records": compact_records,
        "blobs": blobs,
    }


def load_compact_trace(text: str) -> list[dict[str, Any]] | None:
    """Materialize a compact trace bundle, or return None when text is not one."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not is_compact_trace_bundle(value):
        return None
    return materialize_compact_trace_bundle(value)


def is_compact_trace_bundle(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    marker = value.get(COMPACT_TRACE_MARKER)
    return isinstance(marker, dict) and marker.get("version") == COMPACT_TRACE_VERSION


def materialize_compact_trace_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize all records from a compact trace bundle."""
    if not is_compact_trace_bundle(bundle):
        raise ValueError("Unsupported compact trace bundle.")
    records = bundle.get("records")
    blobs = bundle.get("blobs")
    if not isinstance(records, list) or not isinstance(blobs, dict):
        raise ValueError("Compact trace bundle must contain records and blobs.")

    materialized: list[dict[str, Any]] = []
    blob_cache: dict[str, Any] = {}
    for payload in records:
        record = decode_compact_record_payload(payload, lambda ref: _load_bundle_blob(ref, blobs, blob_cache))
        if isinstance(record, dict):
            materialized.append(record)
    return materialized


def decode_compact_record_payload(payload: Any, load_blob: Any) -> dict[str, Any] | None:
    """Decode one compact record payload using the supplied blob loader."""
    if not isinstance(payload, dict):
        return None
    marker = payload.get(COMPACT_RECORD_MARKER)
    if not isinstance(marker, dict):
        return payload
    if marker.get("version") != COMPACT_RECORD_VERSION:
        raise RuntimeError(f"Unsupported compact trace record version: {marker.get('version')}")
    record = payload.get("record")
    refs = marker.get("refs")
    if not isinstance(record, dict):
        return None
    ref_paths = _ref_paths_from_marker_refs(refs)
    if not ref_paths:
        ref_paths = _legacy_compact_ref_paths(record)
    for path in ref_paths:
        record = _materialize_blob_ref_path(record, path, load_blob)
    return record if isinstance(record, dict) else None


def make_blob_ref(hash_value: str, size_bytes: int) -> dict[str, Any]:
    return {
        BLOB_REF_MARKER: {
            "version": COMPACT_RECORD_VERSION,
            "kind": BLOB_KIND_JSON,
            "hash": hash_value,
            "bytes": size_bytes,
        }
    }


def json_blob_payload(value: Any) -> tuple[str, int, str]:
    payload_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    payload_bytes = payload_json.encode("utf-8")
    return payload_json, len(payload_bytes), "sha256:" + sha256(payload_bytes).hexdigest()


def _encode_compact_record(record: dict[str, Any], blobs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    compact_record, refs = compact_record_blobs(record, lambda value: _store_bundle_blob(blobs, value))
    if not refs:
        return compact_record
    return {
        COMPACT_RECORD_MARKER: {
            "version": COMPACT_RECORD_VERSION,
            "encoding": "json-blob-ref",
            "refs": refs,
        },
        "record": compact_record,
    }


def compact_record_blobs(
    record: dict[str, Any],
    store_blob: Callable[[Any], dict[str, Any] | None],
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """Replace large repeated record fields and list items with blob refs."""
    compact_record = record
    refs: list[dict[str, object]] = []
    for path in COMPACT_BLOB_PATHS:
        value = _get_path(compact_record, path)
        if value is None:
            continue
        ref = store_blob(value)
        if ref is None:
            continue
        compact_record = _replace_path(compact_record, path, ref)
        refs.append(
            {
                "path": "/" + "/".join(path),
                "hash": ref[BLOB_REF_MARKER]["hash"],
                "bytes": ref[BLOB_REF_MARKER]["bytes"],
            }
        )
    for path in COMPACT_ITEM_BLOB_PATHS:
        value = _get_path(compact_record, path)
        if not isinstance(value, list):
            continue
        compact_items: list[Any] | None = None
        for index, item in enumerate(value):
            ref = store_blob(item)
            if ref is None:
                continue
            if compact_items is None:
                compact_items = list(value)
            compact_items[index] = ref
            refs.append(
                {
                    "path": "/" + "/".join((*path, str(index))),
                    "hash": ref[BLOB_REF_MARKER]["hash"],
                    "bytes": ref[BLOB_REF_MARKER]["bytes"],
                }
            )
        if compact_items is not None:
            compact_record = _replace_path(compact_record, path, compact_items)
    return compact_record, refs


def _store_bundle_blob(blobs: dict[str, dict[str, Any]], value: Any) -> dict[str, Any] | None:
    _payload_json, size_bytes, hash_value = json_blob_payload(value)
    if size_bytes < MIN_BLOB_BYTES:
        return None
    blobs.setdefault(hash_value, {"kind": BLOB_KIND_JSON, "bytes": size_bytes, "payload": value})
    return make_blob_ref(hash_value, size_bytes)


def _load_bundle_blob(ref: dict[str, Any], blobs: dict[str, Any], blob_cache: dict[str, Any]) -> Any:
    hash_value = ref["hash"]
    if hash_value not in blob_cache:
        blob = blobs.get(hash_value)
        if not isinstance(blob, dict) or blob.get("kind") != (ref.get("kind") or BLOB_KIND_JSON):
            raise KeyError(hash_value)
        blob_cache[hash_value] = blob.get("payload")
    return blob_cache[hash_value]


def _parse_ref_path(path: Any) -> tuple[str, ...] | None:
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in path.removeprefix("/").split("/"))


def _ref_paths_from_marker_refs(refs: Any) -> list[tuple[str, ...]]:
    if not isinstance(refs, list):
        return []
    paths: list[tuple[str, ...]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        path = _parse_ref_path(ref.get("path"))
        if path is not None:
            paths.append(path)
    return paths


def _legacy_compact_ref_paths(record: dict[str, Any]) -> list[tuple[str, ...]]:
    paths: list[tuple[str, ...]] = []
    for path in COMPACT_BLOB_PATHS:
        if is_blob_ref(_get_path(record, path)):
            paths.append(path)
    for path in COMPACT_ITEM_BLOB_PATHS:
        value = _get_path(record, path)
        if not isinstance(value, list):
            continue
        paths.extend((*path, str(index)) for index, item in enumerate(value) if is_blob_ref(item))
    return paths


def _materialize_blob_ref_path(root: dict[str, Any], path: tuple[str, ...], load_blob: Any) -> dict[str, Any]:
    value, changed = _replace_blob_ref_at_path(root, path, load_blob)
    return value if changed and isinstance(value, dict) else root


def _replace_blob_ref_at_path(value: Any, path: tuple[str, ...], load_blob: Any) -> tuple[Any, bool]:
    if not path:
        if is_blob_ref(value):
            return load_blob(value[BLOB_REF_MARKER]), True
        return value, False

    key = path[0]
    if isinstance(value, dict):
        if key not in value:
            return value, False
        replacement, changed = _replace_blob_ref_at_path(value[key], path[1:], load_blob)
        if not changed:
            return value, False
        updated = dict(value)
        updated[key] = replacement
        return updated, True

    if isinstance(value, list):
        try:
            index = int(key)
        except ValueError:
            return value, False
        if index < 0 or index >= len(value):
            return value, False
        replacement, changed = _replace_blob_ref_at_path(value[index], path[1:], load_blob)
        if not changed:
            return value, False
        updated = list(value)
        updated[index] = replacement
        return updated, True

    return value, False


def _get_path(root: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = root
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _replace_path(root: dict[str, Any], path: tuple[str, ...], replacement: Any) -> dict[str, Any]:
    if not path:
        return root
    new_root = dict(root)
    old_node: Any = root
    new_node: dict[str, Any] = new_root
    for key in path[:-1]:
        child = old_node.get(key) if isinstance(old_node, dict) else None
        if not isinstance(child, dict):
            return root
        child_copy = dict(child)
        new_node[key] = child_copy
        old_node = child
        new_node = child_copy
    new_node[path[-1]] = replacement
    return new_root


def is_blob_ref(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {BLOB_REF_MARKER}:
        return False
    ref = value[BLOB_REF_MARKER]
    return (
        isinstance(ref, dict)
        and ref.get("version") == COMPACT_RECORD_VERSION
        and ref.get("kind") == BLOB_KIND_JSON
        and isinstance(ref.get("hash"), str)
    )
