"""Session-level briefing computed from captured records.

The viewer banner and ``claude-tap summary`` emit this JSON. The numbers come
from the same metadata the sidebar already shows: per-turn cost, cache token
buckets, and request-side tool results. Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from claude_tap.viewer import (
    _build_cost_index,
    _dict_or_empty,
    _extract_metadata_from_record,
    _extract_request_messages,
)

BRIEFING_VERSION = 1
TOOL_RESULT_MIN_BYTES = 10_000
_IMAGE_BLOCK_TYPES = {"image", "computer_screenshot", "input_image"}


def summarize_session(records: list[dict]) -> dict[str, Any]:
    """Return the frozen briefing object for a list of full trace records."""
    rows: list[tuple[dict, dict]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        meta = _extract_metadata_from_record(record)
        if meta is not None:
            rows.append((record, meta))
    return _summarize_rows(rows, cost_index=_build_cost_index(records))


def summarize_session_from_metadata(metadata: list[dict]) -> dict[str, Any]:
    """Return a briefing from sidebar stubs when full records are not inlined.

    Tool-result sizes and a named cache cause need request bodies, so those
    fields stay empty on this path.
    """
    rows = [(None, meta) for meta in metadata if isinstance(meta, dict)]
    return _summarize_rows(rows, cost_index=_cost_index_from_metadata(metadata))


def _summarize_rows(
    rows: list[tuple[dict | None, dict]],
    *,
    cost_index: dict[str, dict],
) -> dict[str, Any]:
    cost = _cost_summary(cost_index)
    cache = _cache_break(rows)
    tools = _top_tool_results(rows)
    return {
        "version": BRIEFING_VERSION,
        "cost": cost,
        "cache": cache,
        "tool_results": tools,
    }


def _cost_index_from_metadata(metadata: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for meta in metadata:
        request_id = str(meta.get("request_id") or "")
        if not request_id:
            continue
        if meta.get("subscription") is True:
            index[request_id] = {"subscription": True}
        elif "cost" in meta:
            index[request_id] = {key: meta[key] for key in ("cost", "uncached_cost", "saved") if key in meta}
    return index


def _cost_summary(cost_index: dict[str, dict]) -> dict[str, Any]:
    amounts = [fields["cost"] for fields in cost_index.values() if isinstance(fields.get("cost"), (int, float))]
    subscription = any(fields.get("subscription") is True for fields in cost_index.values())
    total = float(sum(amounts)) if amounts else None
    unpriced = bool(subscription and total is None)
    return {
        "usd": total,
        "partial": bool(subscription and total is not None),
        "unpriced": unpriced,
    }


def _cache_break(rows: list[tuple[dict | None, dict]]) -> dict[str, Any]:
    """Report the first cold write after a hit, and whether hits ever resumed.

    A single cold write says little on its own: a long session can rebuild the
    cache once and hit for the next hundred turns. ``sustained`` is True only
    when no later turn reads from cache again, which is the case worth calling
    a break rather than a rebuild.
    """
    ordered = sorted(rows, key=lambda item: _turn_number(item[1]))
    warm = False
    previous: tuple[dict | None, dict] | None = None
    for index, (record, meta) in enumerate(ordered):
        read = _int_field(meta, "cache_read_input_tokens")
        write = _int_field(meta, "cache_creation_input_tokens")
        if read > 0:
            warm = True
            previous = (record, meta)
            continue
        if warm and write > 0:
            resumed = any(_int_field(later, "cache_read_input_tokens") > 0 for _rec, later in ordered[index + 1 :])
            return {
                "break_turn": _turn_number(meta) or None,
                "reason": _cache_reason(previous, (record, meta)),
                "sustained": not resumed,
            }
        previous = (record, meta)
    return {"break_turn": None, "reason": None, "sustained": False}


def _cache_reason(
    previous: tuple[dict | None, dict] | None,
    current: tuple[dict | None, dict],
) -> str | None:
    if previous is None:
        return None
    prev_record, prev_meta = previous
    cur_record, cur_meta = current
    if prev_record is None or cur_record is None:
        return None
    sys_changed = _system_text(prev_record) != _system_text(cur_record)
    tools_changed = tuple(prev_meta.get("tool_names") or []) != tuple(cur_meta.get("tool_names") or [])
    if sys_changed and not tools_changed:
        return "cache_miss_system"
    if tools_changed and not sys_changed:
        return "cache_miss_tools"
    return None


def _system_text(record: dict) -> str:
    body = _dict_or_empty(_dict_or_empty(record.get("request")).get("body"))
    system = body.get("system")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for item in system:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    instructions = body.get("instructions")
    return instructions if isinstance(instructions, str) else ""


def _top_tool_results(rows: list[tuple[dict | None, dict]]) -> list[dict[str, Any]]:
    """Return the three largest distinct tool results, keyed by first appearance.

    A tool result stays in the request context for every later turn, so walking
    each body sees the same payload many times. Collapsing on (name, digest)
    keeps the top three from degenerating into one payload retold three times,
    and the reported turn is where it first entered the context.
    """
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for record, meta in rows:
        if record is None:
            continue
        body = _dict_or_empty(_dict_or_empty(record.get("request")).get("body"))
        for name, content in _iter_tool_results(_extract_request_messages(body)):
            byte_count = _tool_result_bytes(content)
            if byte_count < TOOL_RESULT_MIN_BYTES:
                continue
            key = (name, _tool_result_digest(content))
            if key in found:
                continue
            found[key] = {
                "name": name,
                "bytes": byte_count,
                "size_kb": f"{byte_count / 1024:.1f}",
                "turn": _turn_number(meta) or None,
            }
    ranked = sorted(found.values(), key=lambda item: item["bytes"], reverse=True)
    return ranked[:3]


def _tool_result_digest(content: object) -> str:
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _iter_tool_results(messages: list[dict]) -> list[tuple[str, object]]:
    names_by_id: dict[str, str] = {}
    results: list[tuple[str, object]] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    use_id = block.get("id")
                    if isinstance(use_id, str) and use_id:
                        names_by_id[use_id] = str(block.get("name") or "tool")
        if message.get("role") == "tool":
            name = message.get("name")
            results.append((str(name) if isinstance(name, str) and name else "tool", content))
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            use_id = block.get("tool_use_id")
            name = names_by_id.get(use_id) if isinstance(use_id, str) else None
            results.append((name or "tool", block.get("content")))
    return results


def _tool_result_bytes(content: object) -> int:
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, str):
                total += len(block.encode("utf-8"))
            elif isinstance(block, dict):
                if block.get("type") in _IMAGE_BLOCK_TYPES:
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    total += len(text.encode("utf-8"))
                elif isinstance(block.get("content"), str):
                    total += len(block["content"].encode("utf-8"))
        return total
    return 0


def _turn_number(meta: dict) -> int:
    turn = meta.get("turn")
    return turn if isinstance(turn, int) else 0


def _int_field(meta: dict, key: str) -> int:
    value = meta.get(key)
    return int(value) if isinstance(value, (int, float)) else 0
