"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import base64
import json
import re
from importlib.metadata import version as _pkg_version
from pathlib import Path

from claude_tap.compact_trace import (
    COMPACT_TRACE_MARKER,
    build_compact_trace_bundle,
    is_compact_trace_bundle,
    materialize_compact_trace_bundle,
)
from claude_tap.pricing import (
    _int,
    _search_cost_per_query,
    entry_cost,
    is_priced_model,
    model_from_path,
    pricing_metadata,
    provider_namespace,
)
from claude_tap.sse import SSEReassembler
from claude_tap.usage import normalize_usage

try:
    CLAUDE_TAP_VERSION = _pkg_version("claude-tap")
except Exception:
    CLAUDE_TAP_VERSION = "0.0.0"

# Threshold: traces with more entries than this use lazy mode
LAZY_THRESHOLD = 50
VIEWER_TEMPLATE_PATH = Path(__file__).parent / "viewer.html"
VIEWER_ASSETS_DIR = Path(__file__).parent / "viewer_assets"
VIEWER_CSS_PATH = VIEWER_ASSETS_DIR / "viewer.css"
VIEWER_JS_PATHS = (
    VIEWER_ASSETS_DIR / "state.js",
    VIEWER_ASSETS_DIR / "responses.js",
    VIEWER_ASSETS_DIR / "lazy_loading.js",
    VIEWER_ASSETS_DIR / "i18n_ui.js",
    VIEWER_ASSETS_DIR / "live_bootstrap.js",
    VIEWER_ASSETS_DIR / "filters_search.js",
    VIEWER_ASSETS_DIR / "sidebar.js",
    VIEWER_ASSETS_DIR / "detail_trace.js",
    VIEWER_ASSETS_DIR / "renderers.js",
    VIEWER_ASSETS_DIR / "sections_json.js",
    VIEWER_ASSETS_DIR / "diff.js",
    VIEWER_ASSETS_DIR / "utilities_mobile.js",
)
VIEWER_I18N_PATH = Path(__file__).parent / "viewer_i18n.json"
VIEWER_STYLE_TEMPLATE_ANCHOR = "<!-- CLAUDE_TAP_VIEWER_STYLE -->"
VIEWER_SCRIPT_TEMPLATE_ANCHOR = "<!-- CLAUDE_TAP_VIEWER_SCRIPT -->"
VIEWER_SCRIPT_ANCHOR = "<script>\nconst $ = s =>"


def _load_viewer_i18n() -> dict[str, dict[str, str]]:
    data = json.loads(VIEWER_I18N_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("viewer_i18n.json must contain a JSON object.")
    for lang, entries in data.items():
        if not isinstance(lang, str) or not isinstance(entries, dict):
            raise ValueError("viewer_i18n.json must map language codes to string maps.")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in entries.items()):
            raise ValueError("viewer_i18n.json language maps must contain string keys and values.")
    return data


def _viewer_i18n_script() -> str:
    payload = json.dumps(_load_viewer_i18n(), ensure_ascii=False, separators=(",", ":"))
    return f"const __CLAUDE_TAP_I18N__ = {payload};\n"


def _read_viewer_template() -> str:
    html = VIEWER_TEMPLATE_PATH.read_text(encoding="utf-8")
    if VIEWER_STYLE_TEMPLATE_ANCHOR not in html:
        raise ValueError("viewer.html is missing the style asset anchor.")
    if VIEWER_SCRIPT_TEMPLATE_ANCHOR not in html:
        raise ValueError("viewer.html is missing the script asset anchor.")
    css = VIEWER_CSS_PATH.read_text(encoding="utf-8").rstrip()
    js = "".join(path.read_text(encoding="utf-8") for path in VIEWER_JS_PATHS).rstrip()
    html = html.replace(VIEWER_STYLE_TEMPLATE_ANCHOR, f"<style>\n{css}\n</style>", 1)
    html = html.replace(
        VIEWER_SCRIPT_TEMPLATE_ANCHOR,
        f"<script>\n{_viewer_i18n_script()}</script>\n<script>\n{js}\n</script>",
        1,
    )
    if VIEWER_SCRIPT_ANCHOR not in html:
        raise ValueError("viewer asset script is missing the main script anchor.")
    return html


def _iter_response_events(resp: dict) -> list[dict]:
    """Return stream events from SSE or WebSocket traces."""
    if not isinstance(resp, dict):
        return []
    events = resp.get("sse_events")
    if isinstance(events, list) and events:
        return events
    events = resp.get("ws_events")
    if isinstance(events, list):
        return events
    return []


def _iter_request_events(req: dict) -> list[dict]:
    """Return request-side WebSocket events when a raw trace stores them."""
    if not isinstance(req, dict):
        return []
    events = req.get("ws_events")
    if isinstance(events, list):
        return events
    return []


def _event_type(event: dict) -> str:
    if not isinstance(event, dict):
        return ""
    value = event.get("event") or event.get("type")
    return value if isinstance(value, str) else ""


def _event_payload(event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    payload = event.get("data", event)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    return payload if isinstance(payload, dict) else None


def _first_bool(*values: object) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def _response_payload_from_event(event: dict) -> dict:
    data = _event_payload(event)
    if not isinstance(data, dict):
        return {}
    response = data.get("response")
    if isinstance(response, dict):
        return response
    return data


def _last_response_payload_for_event(events: list[dict], event_type: str) -> dict:
    for event in reversed(events):
        if _event_type(event) == event_type:
            return _response_payload_from_event(event)
    return {}


def _response_output_count_from_events(events: list[dict]) -> int:
    completed = _last_response_payload_for_event(events, "response.completed")
    output = completed.get("output")
    if isinstance(output, list):
        return len(output)
    return sum(1 for event in events if _event_type(event) == "response.output_item.done")


def _decode_bedrock_eventstream_events(body: object) -> list[dict]:
    """Extract normalized stream events from a decoded AWS EventStream body.

    Bedrock streaming responses are binary AWS EventStream frames. Legacy
    traces may contain those bytes decoded as text with invalid frame bytes
    replaced, but the JSON payloads inside the frames remain intact.
    """
    error_event_keys = {
        "internalServerException",
        "modelStreamErrorException",
        "modelTimeoutException",
        "serviceUnavailableException",
        "throttlingException",
        "validationException",
    }
    if not isinstance(body, str):
        return []
    stream_event_keys = (
        "bytes",
        "chunk",
        "type",
        "messageStart",
        "contentBlockStart",
        "contentBlockDelta",
        "contentBlockStop",
        "messageStop",
        "metadata",
        *error_event_keys,
    )
    if not any(f'"{key}"' in body for key in stream_event_keys):
        return []

    def _converse_event_payload(payload: dict) -> tuple[str | None, dict | None]:
        message_start = payload.get("messageStart")
        if isinstance(message_start, dict):
            return "message_start", {
                "message": {
                    "type": "message",
                    "role": message_start.get("role") or "assistant",
                    "content": [],
                }
            }

        block_start = payload.get("contentBlockStart")
        if isinstance(block_start, dict):
            start = block_start.get("start") if isinstance(block_start.get("start"), dict) else {}
            tool_use = start.get("toolUse") if isinstance(start, dict) else None
            block: dict = {}
            if isinstance(tool_use, dict):
                block = {
                    "type": "tool_use",
                    "id": tool_use.get("toolUseId", ""),
                    "name": tool_use.get("name", ""),
                    "input": {},
                }
            return "content_block_start", {
                "index": block_start.get("contentBlockIndex", payload.get("contentBlockIndex", 0)),
                "content_block": block,
            }

        block_delta = payload.get("contentBlockDelta")
        if isinstance(block_delta, dict):
            delta = block_delta.get("delta") if isinstance(block_delta.get("delta"), dict) else {}
            normalized_delta: dict = {}
            if isinstance(delta.get("text"), str):
                normalized_delta = {"type": "text_delta", "text": delta["text"]}
            elif isinstance(delta.get("reasoningContent"), dict):
                reasoning = delta["reasoningContent"]
                text = reasoning.get("text") if isinstance(reasoning.get("text"), str) else ""
                normalized_delta = {"type": "thinking_delta", "thinking": text}
                signature = reasoning.get("signature") if isinstance(reasoning.get("signature"), str) else ""
                if signature:
                    normalized_delta["signature"] = signature
            elif isinstance(delta.get("toolUse"), dict):
                tool_delta = delta["toolUse"]
                partial = tool_delta.get("input") if isinstance(tool_delta.get("input"), str) else ""
                normalized_delta = {"type": "input_json_delta", "partial_json": partial}
            if normalized_delta:
                return "content_block_delta", {
                    "index": block_delta.get("contentBlockIndex", payload.get("contentBlockIndex", 0)),
                    "delta": normalized_delta,
                }

        block_stop = payload.get("contentBlockStop")
        if isinstance(block_stop, dict):
            return "content_block_stop", {
                "index": block_stop.get("contentBlockIndex", payload.get("contentBlockIndex", 0)),
            }

        message_stop = payload.get("messageStop")
        if isinstance(message_stop, dict):
            return "message_delta", {
                "delta": {"stop_reason": message_stop.get("stopReason")},
            }

        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("usage"), dict):
            return "message_delta", {"usage": metadata["usage"]}

        return None, None

    def _event_payload_from_frame(frame: dict) -> tuple[str | None, dict | None]:
        encoded = frame.get("bytes")
        if not isinstance(encoded, str):
            chunk = frame.get("chunk")
            if isinstance(chunk, dict):
                encoded = chunk.get("bytes")
        if isinstance(encoded, str):
            try:
                payload_bytes = base64.b64decode(encoded, validate=True)
                payload = json.loads(payload_bytes)
            except (ValueError, json.JSONDecodeError):
                return None, None
            if not isinstance(payload, dict):
                return None, None

            event_type = payload.get("type")
            if isinstance(event_type, str) and event_type:
                return event_type, payload
            event_type, event_payload = _converse_event_payload(payload)
            if event_type and event_payload:
                return event_type, event_payload
            for event_type in error_event_keys:
                event_payload = payload.get(event_type)
                if isinstance(event_payload, dict):
                    return event_type, event_payload
            return None, None

        event_type = frame.get("type")
        if isinstance(event_type, str) and event_type:
            return event_type, frame
        event_type, event_payload = _converse_event_payload(frame)
        if event_type and event_payload:
            return event_type, event_payload

        for event_type in error_event_keys:
            payload = frame.get(event_type)
            if isinstance(payload, dict):
                return event_type, payload
        return None, None

    events: list[dict] = []
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = body.find('{"', pos)
        if start < 0:
            break
        try:
            frame, end = decoder.raw_decode(body[start:])
        except json.JSONDecodeError:
            pos = start + 1
            continue
        pos = start + end

        if not isinstance(frame, dict):
            continue
        event_type, payload = _event_payload_from_frame(frame)
        if event_type and payload:
            events.append({"event": event_type, "data": payload})

    return events


def _normalize_record_for_viewer(record_json: str) -> str:
    """Normalize trace variants into the shape expected by viewer.html."""
    try:
        record = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return record_json
    if not isinstance(record, dict):
        return record_json

    response = record.get("response")
    if not isinstance(response, dict):
        return record_json

    events = _decode_bedrock_eventstream_events(response.get("body"))
    if not events:
        return record_json

    reassembler = SSEReassembler()
    for event in events:
        reassembler.add_event(event["event"], event["data"])

    reconstructed = reassembler.reconstruct()
    if reconstructed:
        response["body"] = reconstructed
    response.setdefault("sse_events", events)

    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _parse_function_call_arguments(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    if arguments is None:
        return {}
    return arguments


def _parse_sse_data_frames(body: object) -> list[dict]:
    if not isinstance(body, str) or "data:" not in body:
        return []

    events: list[dict] = []
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
            continue
        if line.strip():
            continue
        if not data_lines:
            continue
        raw_data = "\n".join(data_lines)
        data_lines = []
        if raw_data == "[DONE]":
            continue
        try:
            data = json.loads(raw_data)
        except (json.JSONDecodeError, TypeError):
            data = raw_data
        events.append({"event": "message", "data": data})

    if data_lines:
        raw_data = "\n".join(data_lines)
        if raw_data != "[DONE]":
            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                data = raw_data
            events.append({"event": "message", "data": data})

    return events


def _looks_like_gemini_request(value: object) -> bool:
    return isinstance(value, dict) and (
        isinstance(value.get("contents"), list) or isinstance(value.get("systemInstruction"), dict)
    )


def _gemini_request(body: dict) -> dict:
    req = body.get("request")
    if _looks_like_gemini_request(req):
        return req
    return body if _looks_like_gemini_request(body) else {}


def _is_gemini_request_body(body: dict) -> bool:
    req = _gemini_request(body)
    return _looks_like_gemini_request(req)


def _model_from_path(path: object) -> str:
    """Extract a model id from a request path.

    Delegates to the pricing adapter so both sides read one parser: a Bedrock id
    keeps its ``-v1:0`` version suffix (and is percent-decoded) while a Vertex
    path still drops the method after the colon.
    """
    if not isinstance(path, str):
        return ""
    return model_from_path(path)


def _first_priced_model(*candidates: object, provider: str = "", billed: object = None) -> str:
    """Return the first candidate the price table knows, else the first non-empty.

    A gateway names its own deployment alias in the request body while the
    response reports the model actually billed. Taking the first non-empty string
    leaves such a turn unpriced even though the table can price the response
    model, so the table gets consulted before the order is settled. The first
    non-empty name is still what gets displayed when none of them is priceable,
    since that is what the request asked for.

    `billed` names the model the response says was charged. It wins whenever the
    table can price it, because a deployment alias may also be a table key at a
    different rate: an Azure deployment called `gpt-4o` answering as
    `gpt-4o-2024-11-20` would otherwise bill at the undated entry's 2.5/10 rather
    than the dated 2.75/11, understating the turn by 10%.
    """
    billed_name = billed if isinstance(billed, str) and billed else ""
    if billed_name and is_priced_model(billed_name, provider=provider):
        return billed_name
    names = [value for value in candidates if isinstance(value, str) and value]
    for name in names:
        if is_priced_model(name, provider=provider):
            return name
    if billed_name:
        names.append(billed_name)
    return names[0] if names else ""


def _cache_ttl_1h(body: dict) -> bool:
    """Return True when the request asks for Anthropic's 1-hour cache TTL.

    A 1-hour cache write is billed above the default 5-minute rate, so the
    request's own cache_control breakpoints decide which write rate applies.
    """

    def _scan(value: object, depth: int = 0) -> bool:
        if depth > 6:
            return False
        if isinstance(value, dict):
            control = value.get("cache_control")
            if isinstance(control, dict) and control.get("ttl") == "1h":
                return True
            return any(_scan(item, depth + 1) for item in value.values())
        if isinstance(value, list):
            return any(_scan(item, depth + 1) for item in value)
        return False

    return _scan(body)


# Upstreams whose traffic is covered by a plan or an account quota rather than
# billed per token, so a dollar figure derived from the provider's published API
# rates was never charged to the user (see README's per-client auth tables).
#
#   - chatgpt.com/backend-api/codex: Codex CLI's ChatGPT Plus/Pro/Team OAuth.
#   - cloudcode-pa.googleapis.com: the Google Code Assist API behind Gemini CLI's
#     default OAuth flow and Antigravity CLI. Its staging and daily hosts carry
#     the same suffix. API-key Gemini traffic goes to
#     generativelanguage.googleapis.com instead and *is* billed per token, so the
#     Code Assist host is what separates quota from billed usage -- classifying
#     Gemini by model name would wrongly zero out real API charges.
_SUBSCRIPTION_UPSTREAMS = ("chatgpt.com/backend-api/codex", "cloudcode-pa.googleapis.com")

# Code Assist's own route. A reverse-mode capture names the local listener as the
# host, so the route is the only remaining signal that the request was answered
# from an account quota.
_SUBSCRIPTION_ROUTES = ("/v1internal:", "/v1internal/")


def _is_subscription_traffic(record: object) -> bool:
    """Return True when the record's upstream bills by subscription, not by token.

    Checked against the recorded upstream and the request Host together: reverse
    mode records the target it forwarded to, while forward-proxy mode identifies
    the destination only by the CONNECT host.
    """
    if not isinstance(record, dict):
        return False
    req = _dict_or_empty(record.get("request"))
    headers = req.get("headers")
    host = ""
    if isinstance(headers, dict):
        for key in ("Host", "host", ":authority"):
            value = headers.get(key)
            if isinstance(value, str) and value:
                host = value
                break
    signal = " ".join(
        part
        for part in (
            str(record.get("upstream_base_url") or ""),
            host,
            str(req.get("path") or ""),
        )
        if part
    ).lower()
    if not signal:
        return False
    if any(upstream in signal for upstream in _SUBSCRIPTION_UPSTREAMS):
        return True
    if any(route in signal for route in _SUBSCRIPTION_ROUTES):
        return True
    # A forward-proxy capture names only the host, so the Codex route on that
    # host is what identifies the subscription upstream.
    return "chatgpt.com" in signal and "/backend-api/codex" in signal


def _completed_web_search_calls_in_output(output: object) -> int:
    if not isinstance(output, list):
        return 0
    return sum(
        1
        for item in output
        if isinstance(item, dict)
        and item.get("type") == "web_search_call"
        and item.get("status") in (None, "completed")
    )


def _completed_web_search_calls_in_events(events: object) -> int:
    if not isinstance(events, list):
        return 0
    count = 0
    for event in events:
        if _event_type(event) != "response.output_item.done":
            continue
        payload = _event_payload(event)
        item = payload.get("item") if isinstance(payload, dict) else None
        if (
            isinstance(item, dict)
            and item.get("type") == "web_search_call"
            and item.get("status") in (None, "completed")
        ):
            count += 1
    return count


def _completed_web_search_calls(*, output: object = None, events: object = None) -> int:
    """Count completed web_search_call items once.

    The same call is typically present in both the final ``output`` array and
    the ``response.output_item.done`` stream. Adding those counts would apply
    the per-query surcharge twice.
    """
    return max(
        _completed_web_search_calls_in_output(output),
        _completed_web_search_calls_in_events(events),
    )


def _cost_fields(model: str, usage: dict, body: dict, *, record: object = None, search_calls: int = 0) -> dict:
    """Return per-entry cost fields, or an empty dict when no cost applies.

    Cost lives here rather than in the viewer so a single price table and a
    single set of tier rules serve every output path.

    Subscription traffic is deliberately left unpriced. The price table can put a
    number on those tokens, but it is a counterfactual "what the API would have
    charged", not money the user was billed, and presenting it beside real
    per-token costs in one total would misstate what the session cost. The
    ``subscription`` flag travels instead so the viewer can say why the turn
    carries no figure rather than implying the model has no known price.
    """
    if _is_subscription_traffic(record):
        return {"subscription": True}
    priced = entry_cost(
        model,
        usage,
        cache_ttl_1h=_cache_ttl_1h(body),
        provider=provider_namespace(record),
        search_calls=search_calls,
    )
    if priced is None:
        return {}
    return {
        "cost": priced.cost,
        "uncached_cost": priced.uncached_cost,
        "saved": priced.saved,
        "priced_model": priced.model,
        "long_context": priced.long_context,
    }


def _sum_usage(usages: list[dict]) -> dict:
    """Return the summed token buckets for several responses in one record.

    Only the buckets the sidebar reports are summed. ``cache_read_in_input`` is a
    shape flag rather than a count, so it is carried from the first response that
    states it — every response in one record comes from the same provider.

    Counts go through :func:`claude_tap.pricing._int` so a non-finite or
    oversize value in one response cannot abort viewer generation.
    """
    totals: dict[str, object] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "total_tokens",
    ):
        summed = sum(_int(usage.get(key)) for usage in usages)
        if summed:
            totals[key] = summed
    for usage in usages:
        if "cache_read_in_input" in usage:
            totals["cache_read_in_input"] = usage["cache_read_in_input"]
            break
    return totals


def _aggregate_cost_fields(
    models: list[str],
    usages: list[dict],
    body: dict,
    *,
    record: object = None,
    search_calls: int = 0,
) -> dict:
    """Return cost fields covering every response in a multi-response record.

    Each response is priced on its own — the long-context tier is selected by one
    prompt's size, so pricing the summed tokens would push short responses into a
    tier they never hit — and the resulting figures are then added.

    Returns an empty dict when any response is unpriceable, matching
    :func:`claude_tap.pricing.entry_cost`: a total that silently omits some of the
    responses in a record would still be displayed as if it covered all of them.
    """
    if not usages:
        return {}
    if _is_subscription_traffic(record):
        return {"subscription": True}
    ttl_1h = _cache_ttl_1h(body)
    provider = provider_namespace(record)
    total = 0.0
    total_uncached = 0.0
    total_saved = 0.0
    long_context = False
    priced_model = ""
    for model, usage in zip(models, usages):
        priced = entry_cost(model, usage, cache_ttl_1h=ttl_1h, provider=provider)
        if priced is None:
            return {}
        total += priced.cost
        total_uncached += priced.uncached_cost
        total_saved += priced.saved
        long_context = long_context or priced.long_context
        priced_model = priced_model or priced.model
    if search_calls:
        search_rate = _search_cost_per_query(priced_model or models[0], provider)
        if search_rate is None:
            return {}
        search_total = search_calls * search_rate
        total += search_total
        total_uncached += search_total
    return {
        "cost": total,
        "uncached_cost": total_uncached,
        "saved": total_saved,
        "priced_model": priced_model,
        "long_context": long_context,
    }


def _gemini_text_from_parts(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _extract_gemini_system(body: dict) -> str:
    instr = _gemini_request(body).get("systemInstruction")
    if not isinstance(instr, dict):
        return ""
    return _gemini_text_from_parts(instr.get("parts")).strip()


def _gemini_function_response_content(resp: dict) -> str:
    payload = resp.get("response")
    if isinstance(payload, dict) and "output" in payload:
        output = payload["output"]
    else:
        output = payload
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False)


def _gemini_part_blocks(part: dict) -> list[dict]:
    blocks: list[dict] = []
    text = part.get("text")
    if isinstance(text, str) and text.strip():
        if part.get("thought") is True:
            blocks.append({"type": "thinking", "thinking": text})
        else:
            blocks.append({"type": "text", "text": text})

    call = part.get("functionCall")
    if isinstance(call, dict):
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id", ""),
                "name": call.get("name", "tool_use"),
                "input": call.get("args") if isinstance(call.get("args"), dict) else {},
            }
        )

    response = part.get("functionResponse")
    if isinstance(response, dict):
        blocks.append(
            {
                "type": "tool_result",
                "tool_use_id": response.get("id") or response.get("name", ""),
                "content": _gemini_function_response_content(response),
            }
        )
    return blocks


def _gemini_role(role: object) -> str:
    if role == "model":
        return "assistant"
    return role if isinstance(role, str) and role else "user"


def _extract_gemini_request_messages(body: dict) -> list[dict]:
    contents = _gemini_request(body).get("contents")
    if not isinstance(contents, list):
        return []

    messages: list[dict] = []
    for content in contents:
        if not isinstance(content, dict):
            continue
        blocks: list[dict] = []
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                blocks.extend(_gemini_part_blocks(part))
        if not blocks:
            continue
        role = _gemini_role(content.get("role"))
        if all(block.get("type") == "tool_result" for block in blocks):
            role = "tool"
        messages.append({"role": role, "content": blocks})
    return messages


def _extract_gemini_tools(body: dict) -> list[dict]:
    tools = _gemini_request(body).get("tools")
    if not isinstance(tools, list):
        return []

    normalized: list[dict] = []
    for tool_group in tools:
        if not isinstance(tool_group, dict):
            continue
        declarations = tool_group.get("functionDeclarations")
        if not isinstance(declarations, list):
            continue
        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            normalized.append(
                {
                    "name": decl.get("name", ""),
                    "description": decl.get("description", ""),
                    "input_schema": decl.get("parametersJsonSchema") or decl.get("parameters") or {},
                }
            )
    return normalized


def _gemini_payloads_from_response_body(body: object) -> list[dict]:
    if isinstance(body, str):
        return [event["data"] for event in _parse_sse_data_frames(body) if isinstance(event.get("data"), dict)]
    if isinstance(body, dict):
        return [body]
    return []


def _extract_gemini_response_output(body: object) -> dict | None:
    payloads = _gemini_payloads_from_response_body(body)
    content: list[dict] = []

    def append_mergeable_block(block: dict[str, str]) -> None:
        previous = content[-1] if content else None
        if previous and previous.get("type") == block.get("type"):
            if block.get("type") == "thinking":
                previous["thinking"] = f"{previous.get('thinking', '')}{block.get('thinking', '')}"
                return
            if block.get("type") in {"text", "input_text", "output_text"}:
                previous["text"] = f"{previous.get('text', '')}{block.get('text', '')}"
                return
        content.append(block)

    def append_text(text: str) -> None:
        if not text.strip():
            return
        append_mergeable_block({"type": "text", "text": text})

    for payload in payloads:
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_content = candidate.get("content")
            if not isinstance(candidate_content, dict):
                continue
            for part in candidate_content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    if part.get("thought") is True:
                        thinking = part["text"]
                        if thinking.strip():
                            append_mergeable_block({"type": "thinking", "thinking": thinking})
                    else:
                        append_text(part["text"])
                call = part.get("functionCall")
                if isinstance(call, dict):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": call.get("name", "tool_use"),
                            "input": call.get("args") if isinstance(call.get("args"), dict) else {},
                        }
                    )

    return {"content": content} if content else None


def _extract_gemini_response_usage(body: object) -> dict:
    usage: dict = {}
    for payload in _gemini_payloads_from_response_body(body):
        response = payload.get("response") if isinstance(payload.get("response"), dict) else payload
        if not isinstance(response, dict):
            continue
        event_usage = response.get("usageMetadata")
        if isinstance(event_usage, dict):
            usage = event_usage
    return normalize_usage(usage)


def _extract_gemini_response_tool_names(body: object) -> list[str]:
    output = _extract_gemini_response_output(body)
    if not output:
        return []
    return [block.get("name", "") for block in output["content"] if block.get("type") == "tool_use"]


def _tool_search_output_content(item: dict) -> str:
    names: list[str] = []
    tools = item.get("tools")
    if isinstance(tools, list):
        for namespace in tools:
            if not isinstance(namespace, dict):
                continue
            namespace_name = namespace.get("name")
            if isinstance(namespace_name, str) and namespace_name:
                names.append(namespace_name)
            nested_tools = namespace.get("tools")
            if isinstance(nested_tools, list):
                for tool in nested_tools:
                    if not isinstance(tool, dict):
                        continue
                    tool_name = tool.get("name")
                    if isinstance(tool_name, str) and tool_name:
                        if isinstance(namespace_name, str) and namespace_name:
                            names.append(f"{namespace_name}.{tool_name}")
                        else:
                            names.append(tool_name)
    if names:
        return "tool_search_output\n" + "\n".join(names)
    if isinstance(tools, list):
        return json.dumps(tools, ensure_ascii=False)
    return json.dumps(item, ensure_ascii=False)


def _response_call_tool_name(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "tool_search_call":
        return "tool_search"
    item_name = item.get("name")
    if isinstance(item_name, str) and item_name:
        return item_name
    if isinstance(item_type, str) and item_type.endswith("_call"):
        return item_type[: -len("_call")]
    return ""


def _is_response_call_item(item: dict) -> bool:
    item_type = item.get("type")
    return isinstance(item_type, str) and item_type.endswith("_call")


def _response_call_input(item: dict) -> object:
    if "arguments" in item:
        return _parse_function_call_arguments(item.get("arguments"))
    return {
        key: value for key, value in item.items() if key not in {"id", "type", "status", "call_id", "name", "execution"}
    }


def _is_response_tool_result_item(item: dict) -> bool:
    item_type = item.get("type")
    return item_type == "tool_search_output" or (isinstance(item_type, str) and item_type.endswith("_call_output"))


def _response_tool_result_content(item: dict) -> str:
    if item.get("type") == "tool_search_output":
        return _tool_search_output_content(item)
    if "output" in item:
        output = item.get("output")
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False)
    return json.dumps(
        {key: value for key, value in item.items() if key not in {"id", "type", "status", "call_id", "execution"}},
        ensure_ascii=False,
    )


def _extract_request_messages(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        return [msg for msg in msgs if isinstance(msg, dict)]

    if _is_gemini_request_body(body):
        return _extract_gemini_request_messages(body)

    inp = body.get("input")
    if not isinstance(inp, list):
        return []

    normalized = []
    for item in inp:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if _is_response_call_item(item):
            normalized.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": _response_call_tool_name(item),
                            "input": _response_call_input(item),
                        }
                    ],
                }
            )
            continue
        if _is_response_tool_result_item(item):
            normalized.append({"role": "tool", "content": _response_tool_result_content(item)})
            continue
        if item_type not in (None, "message") and "role" not in item:
            continue
        role = item.get("role")
        if not isinstance(role, str) or not role:
            continue
        normalized.append({"role": role, "content": item.get("content")})
    return normalized


def _extract_response_tool_names(output: list) -> list[str]:
    names: list[str] = []
    if not isinstance(output, list):
        return names
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    names.append(c.get("name", ""))
        elif _is_response_call_item(item):
            names.append(_response_call_tool_name(item))
    return names


def _extract_response_tool_names_from_output_item_events(events: list[dict]) -> list[str]:
    names: list[str] = []
    for ev in events:
        if _event_type(ev) != "response.output_item.done":
            continue
        data = _event_payload(ev)
        if not isinstance(data, dict):
            continue
        item = data.get("item")
        if isinstance(item, dict):
            names.extend(_extract_response_tool_names([item]))
    return names


def _dict_or_empty(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _tool_display_name(tool: dict) -> str:
    for value in (
        tool.get("name"),
        (tool.get("function") or {}).get("name") if isinstance(tool.get("function"), dict) else None,
        tool.get("id"),
        tool.get("type"),
    ):
        if isinstance(value, str) and value:
            return value
    return ""


# Wrapper tags a harness opens a user-role message with. Shared by the classifier
# and the cleaner so the two cannot disagree about which openers are injected: one
# blanking a message the other calls human prose costs the message its badge and
# can cost its turn a session title. Mirrors INJECTED_WRAPPER_TAGS in sidebar.js.
_INJECTED_WRAPPER_TAGS = {
    "additional_metadata",
    "artifacts",
    "codex_internal_context",
    "environment_context",
    "local-command-caveat",
    "session_context",
    "skills",
    "slash_commands",
    "subagents",
    "system-reminder",
    "user_information",
}

# Injected openers that are not tags, kept beside the tag set for the same reason.
_INJECTED_TEXT_PREFIXES = (
    "# AGENTS.md instructions",
    "<INSTRUCTIONS>",
    "# Files mentioned by the user:",
)

# Injected forms the cleaner blanks that are neither a wrapper tag nor a fixed
# prefix. Shared with the classifier so cleaning and provenance cannot disagree:
# a form the cleaner discards but the classifier calls prose loses both its title
# and its badge. Mirrors INJECTED_BLANK_PATTERNS in sidebar.js.
_INJECTED_BLANK_PATTERNS = [
    re.compile(r"^</?image(_input)?(\s+[^>]*)?>$", re.IGNORECASE),
    re.compile(r"^\[SUGGESTION MODE:", re.IGNORECASE),
    re.compile(r"^(web page content|page content|网页内容)\s*[:：]", re.IGNORECASE),
    re.compile(r"^\[Image:\s*(original|source)", re.IGNORECASE),
]


def _injected_wrapper_tag(value: str) -> str:
    first_tag = re.match(r"^<([A-Za-z_-]+)(?:\s|>)", value)
    if first_tag and first_tag.group(1).lower() in _INJECTED_WRAPPER_TAGS:
        return first_tag.group(1).lower()
    return ""


def _natural_text_from_prompt_payload(payload: object) -> str:
    """Unwrap a decoded JSON prompt object or array to readable text.

    Mirrors naturalTextFromPromptPayload in sidebar.js. The browser cleaner
    extracts ``{"prompt":"..."}`` before classifying; without this, lazy
    metadata titles the group with the raw JSON and calls it human.
    """
    if isinstance(payload, str):
        return _clean_session_user_text(payload)
    if isinstance(payload, list):
        # Human-first, the same rule _preferred_user_text_for_message applies across
        # separate content blocks. A decoded array is the same shape inside one block,
        # so taking the first readable item let a leading injection title and badge the
        # whole message while the question after it went unread:
        # [{"prompt": "Perform a web search for the query: pricing"},
        #  {"prompt": "What does that cost?"}].
        first = ""
        for item in payload:
            text = _natural_text_from_prompt_payload(item)
            if not text:
                continue
            if _classify_user_input_origin(text) == "human":
                return text
            if not first:
                first = text
        return first
    if not isinstance(payload, dict):
        return ""
    for key in ("prompt", "request", "instruction", "message", "query", "text", "title"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        text = _clean_session_user_text(value)
        if text:
            return text
    if "content" in payload:
        text, _origin = _preferred_user_text_for_message({"content": payload.get("content")})
        return text
    return ""


def _trim_user_text(text: str) -> str:
    """Match JavaScript String.trim(), including U+FEFF BOM at either end."""
    return text.strip().strip("\ufeff").strip()


def _clean_session_user_text(text: str) -> str:
    # JS String.trim() removes U+FEFF; Python strip() does not, so a BOM-prefixed
    # import would be payload in the browser and human in lazy metadata.
    value = _trim_user_text(text)
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str) and decoded.strip():
            value = decoded.strip()

    if value[:1] in "{[":
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            prompt = _natural_text_from_prompt_payload(decoded)
            if prompt:
                return prompt

    request = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", value, flags=re.DOTALL | re.IGNORECASE)
    if request:
        return request.group(1).strip()

    codex_request = re.search(
        r"^#+\s*My request for Codex:\s*(.*?)\s*$",
        value,
        flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    if codex_request:
        return codex_request.group(1).strip()

    session = re.fullmatch(r"<session>\s*(.*?)\s*</session>", value, flags=re.DOTALL | re.IGNORECASE)
    if session:
        unquoted = session.group(1).strip()
        stripped = re.sub(r"^\[Image #\d+\]\s*", "", unquoted, flags=re.IGNORECASE).strip()
        return stripped or unquoted

    if _injected_wrapper_tag(value):
        return ""
    if value.startswith(_INJECTED_TEXT_PREFIXES):
        return ""
    if any(pattern.match(value) for pattern in _INJECTED_BLANK_PATTERNS):
        return ""
    return re.sub(r"^\[Image #\d+\]\s*", "", value, flags=re.IGNORECASE).strip()


def _websocket_response_groups(events: list[dict]) -> list[list[dict]]:
    """Group WebSocket stream events into one list per completed response.

    The viewer splits a single WebSocket record into one entry per
    ``response.created``…``response.completed`` pair, so pricing has to group
    the same way or the per-entry costs will not line up with the entries the
    viewer renders.
    """
    groups: list[list[dict]] = []
    current: list[dict] | None = None
    for event in events:
        event_type = _event_type(event)
        if event_type == "response.created":
            if current:
                groups.append(current)
            current = [event]
            continue
        if current is None:
            continue
        current.append(event)
        if event_type == "response.completed":
            groups.append(current)
            current = None
    if current:
        groups.append(current)
    return [group for group in groups if any(_event_type(event) == "response.completed" for event in group)]


def _cost_index_entries(r: dict) -> list[tuple[str, dict]]:
    """Return (entry key, cost fields) pairs for one raw record.

    A WebSocket record carrying several responses yields one pair per response,
    keyed the way the viewer keys the entries it derives from that record.
    """
    if not isinstance(r, dict):
        return []
    req = _dict_or_empty(r.get("request"))
    body = _dict_or_empty(req.get("body"))
    resp = _dict_or_empty(r.get("response"))
    request_id = r.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return []

    ws_events = resp.get("ws_events")
    groups = _websocket_response_groups(ws_events) if isinstance(ws_events, list) else []
    if len(groups) > 1:
        provider = provider_namespace(r)
        pairs: list[tuple[str, dict]] = []
        for idx, group in enumerate(groups):
            payload = _last_response_payload_for_event(group, "response.completed")
            usage = normalize_usage(payload.get("usage") or {})
            model = _first_priced_model(
                body.get("model", ""),
                _model_from_path(req.get("path", "")),
                provider=provider,
                billed=payload.get("model"),
            )
            fields = _cost_fields(
                model,
                usage,
                body,
                record=r,
                search_calls=_completed_web_search_calls(output=payload.get("output"), events=group),
            )
            if fields:
                pairs.append((f"{request_id}:{idx + 1}", fields))
        return pairs

    meta = _extract_metadata_from_record(r)
    if not isinstance(meta, dict):
        return []
    if meta.get("subscription") is True:
        return [(request_id, {"subscription": True})]
    if "cost" not in meta:
        return []
    return [
        (
            request_id,
            {key: meta[key] for key in ("cost", "uncached_cost", "saved", "priced_model", "long_context")},
        )
    ]


def _build_cost_index(records: list[dict]) -> dict[str, dict]:
    """Return per-entry cost fields keyed by the viewer's entry request id."""
    index: dict[str, dict] = {}
    for record in records:
        for key, fields in _cost_index_entries(record):
            index[key] = fields
    return index


def attach_cost_to_record(record: dict) -> dict:
    """Return ``record`` with a ``_cost_index`` of the costs Python computed.

    Live mode and the records API hand raw records to the viewer with no
    generated cost index, so the cost has to travel on the record itself or
    those paths show no cost at all. Keys match what the viewer derives from the
    record, including the ``<request_id>:<n>`` form for a WebSocket record it
    splits into several entries.
    """
    if not isinstance(record, dict):
        return record
    pairs = _cost_index_entries(record)
    if not pairs:
        return record
    enriched = dict(record)
    enriched["_cost_index"] = {key: fields for key, fields in pairs}
    return enriched


def _is_tool_result_only_message(message: dict) -> bool:
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(
        isinstance(block, dict) and block.get("type") in {"tool_result", "function_call_output"} for block in content
    )


_HARNESS_PATTERNS = [
    re.compile(r"^<system-reminder>", re.IGNORECASE),
    # The lookahead is the boundary _injected_wrapper_tag enforces; without it a
    # longer user-authored tag such as "<local-command-caveats>" read as harness.
    re.compile(r"^<(?:local-command-caveat|command-(?:name|message|args))(?=\s|>)", re.IGNORECASE),
    re.compile(r"^Caveat: The messages below were generated", re.IGNORECASE),
    re.compile(r"^\[Request interrupted", re.IGNORECASE),
    re.compile(r"^\[SYSTEM NOTIFICATION - NOT USER INPUT\]", re.IGNORECASE),
    re.compile(r"^The user stepped away and is coming back", re.IGNORECASE),
    re.compile(r"^\[SUGGESTION MODE:", re.IGNORECASE),
    re.compile(r"^CRITICAL: Respond with TEXT ONLY", re.IGNORECASE),
    re.compile(r"^Briefly inform the user about the task result", re.IGNORECASE),
    # "^Analyze if this message indicates" used to sit here. Unlike every other
    # entry it is ordinary English, so a human asking "Analyze if this message
    # indicates fraud or a billing mistake" was badged as harness-injected and
    # lost its group title. The other openers earn their place by being
    # unmistakable template text; this one only matched a template's opening
    # words, and no installed CLI build carries the full wording to anchor a
    # longer pattern against. A missed injection costs a badge; a false positive
    # relabels what the user actually typed, so this stays out until the emitted
    # text can be quoted from a capture.
    re.compile(r"^This session is being continued from a previous conversation", re.IGNORECASE),
    re.compile(r"^Perform a web search for the query:", re.IGNORECASE),
    re.compile(r"^\[Image(\s*#\d+)?\]|^\[Image:\s*(original|source)", re.IGNORECASE),
]

# Mirrors PAYLOAD_INPUT_PATTERNS in sidebar.js. Digits are spelled out rather
# than left to "\d", which is Unicode-aware here and ASCII-only there; the JS
# mirror likewise writes identifiers as [\p{L}\p{N}_] to match the "\w" below,
# so that "def 处理():" is payload on both sides. Letting the escapes differ
# would change a paste's badge, title and grouping as a capture crosses
# LAZY_THRESHOLD and the browser hands the decision to this module.
_PAYLOAD_PATTERNS = [
    re.compile(r"^diff --git "),
    re.compile(r"^@@ -[0-9]+"),
    re.compile(r"^#!/usr/bin/env "),
    # An import is payload only when the statement ends where a source line
    # would. "import pandas and plot the data" is someone talking, and a
    # prefix-only match would badge that prose as pasted.
    re.compile(r"^import\s+[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*[ \t]*(?:\r?\n|$)"),
    re.compile(
        r"^from\s+[\w.]+\s+import\s+(?:[(*]|[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*)[ \t]*(?:\r?\n|$)"
    ),
    # "__future__" is unmistakable, so this one needs no line boundary.
    re.compile(r"^from __future__ import "),
    re.compile(r"^:root\s*\{"),
    re.compile(r"^/\*[\s─=-]"),
    re.compile(r'^"""'),
    # "async" is a prefix rather than its own alternative so it covers "async def"
    # too; spelling out only "async function" left a pasted coroutine reading as
    # prose while its sync form read as payload.
    re.compile(r"^\s*(?:async\s+)?(?:function|const|let|var|class|def)\s+[\w$]+\s*[({=]"),
    # The base-less Python form "class Foo:" needs the colon as a suffix, but a
    # bare colon after keyword-plus-word also matches English: "class action: can
    # I join the settlement?" and "function calls: why are they slow?" were badged
    # as pasted code. So the colon forms are spelled out separately, each with the
    # syntax a declaration carries and prose does not -- end of line for a class
    # header, an annotation that goes on to assign or terminate for a binding.
    re.compile(r"^\s*class\s+[\w$]+\s*:[ \t]*(?:\r?\n|$)"),
    re.compile(r"^\s*(?:const|let|var)\s+[\w$]+\s*:[^\n]*?[=;]"),
    re.compile(r"^\s*[0-9]+\t"),
]


def _classify_user_input_origin(text: str) -> str:
    value = _trim_user_text(text)
    if not value:
        return "human"
    for pattern in _HARNESS_PATTERNS:
        if pattern.search(value):
            return "harness"
    if _injected_wrapper_tag(value) or value.startswith(_INJECTED_TEXT_PREFIXES):
        return "harness"
    if any(pattern.match(value) for pattern in _INJECTED_BLANK_PATTERNS):
        return "harness"
    for pattern in _PAYLOAD_PATTERNS:
        if pattern.search(value):
            return "payload"
    return "human"


def _block_input_text(block: dict) -> str | None:
    """Text a content block carries as input, or None when it carries neither key.

    ``text`` when it says something and ``output`` otherwise. Stopping at ``text``
    merely because it is a string of the right type loses the content of the blocks
    that leave it empty and put the readable text under ``output``, e.g.
    ``{"type": "input_text", "text": "", "output": "Perform a web search for..."}``.
    Losing it on one side only would change that message's title, badge and grouping
    as a capture crosses LAZY_THRESHOLD and the decision moves between the two
    mirrors. Mirrors blockInputText in sidebar.js.
    """
    text = block.get("text")
    # _trim_user_text, not str.strip: JS String.trim() removes U+FEFF and Python's
    # does not, so a BOM-only `text` beside a populated `output` stopped here and
    # returned the BOM, which _eligible_user_text_blocks then dropped without ever
    # reconsidering `output` -- the browser read the injection, lazy metadata read
    # an empty human message.
    if isinstance(text, str) and _trim_user_text(text):
        return text
    output = block.get("output")
    if isinstance(output, str):
        return output
    return text if isinstance(text, str) else None


def _eligible_user_text_blocks(content: object) -> list[str]:
    """Raw text of the blocks a user-role message carries as input, in wire order.

    Tool results are excluded: they are output the caller sent back, so counting
    them makes an injected message read as ordinary prose. Mirrors
    eligibleUserTextBlocks in sidebar.js.
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        if content.get("type") in {"tool_result", "function_call_output"}:
            return []
        value = _block_input_text(content)
        if value is not None:
            return [value]
        if "content" in content:
            return _eligible_user_text_blocks(content.get("content"))
        return []
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"tool_result", "function_call_output"}:
            continue
        if block.get("type") == "message":
            texts.extend(_eligible_user_text_blocks(block.get("content")))
            continue
        value = _block_input_text(block)
        if value is not None:
            texts.append(value)
    # _trim_user_text so the surviving blocks are the ones JavaScript keeps: its
    # filter uses String.trim(), which drops U+FEFF where str.strip() leaves it.
    return [text for text in texts if _trim_user_text(text)]


def _preferred_user_text_for_message(message: dict) -> tuple[str, str]:
    """Title text and provenance for one user-role message, decided together.

    Blocks are read in wire order and human prose wins, so a pasted diff followed
    by a question is titled by the question.

    The two halves are deliberately independent: cleaning blanks the injections that
    carry no readable request, and a blank title is the right answer for those, but
    provenance is read off the raw text either way. An injection that does say
    something keeps its text and titles its own group.
    Mirrors preferredUserTextForMessage in sidebar.js.
    """
    fallback: tuple[str, str] | None = None
    for raw in _eligible_user_text_blocks(message.get("content")):
        # _trim_user_text, not str.strip: JS String.trim() drops U+FEFF and Python's
        # does not, so a BOM-only leading block would be skipped in the browser and
        # here install an empty human fallback that a later pasted diff then inherits.
        value = _trim_user_text(raw)
        if not value:
            continue
        cleaned = _clean_session_user_text(raw)
        origin = _classify_user_input_origin(cleaned or value)
        if cleaned and origin == "human":
            return cleaned, origin
        # Keep the first block's provenance, but do not let a badge-only injection
        # lock in an empty title when a later block survives cleaning: such a turn
        # would read as untitled and merge into the group before it.
        if fallback is None:
            fallback = (cleaned, origin)
        elif not fallback[0] and cleaned:
            fallback = (cleaned, fallback[1])
    return fallback or ("", "human")


def _session_user_title(messages: list[dict]) -> tuple[str, str]:
    """Title text and its provenance for a lazy-metadata session group.

    Scans newest first, so a cumulative request carrying human turn A followed by
    human turn B is titled by B rather than by the oldest prompt in its history.
    Messages left with no title -- an injection carrying no readable request -- are
    passed over, grouping an injected-only follow-up under the query it follows.

    The origin travels with the text because it cannot be recovered from it. A
    turn whose harness block is blanked by the cleaner and whose title therefore
    comes from a later pasted block is deliberately kept as ``harness`` by
    ``_preferred_user_text_for_message``; re-reading that title in the browser
    would call it ``payload`` and relabel the group as the capture crosses
    LAZY_THRESHOLD. Consumed by ``buildStubEntry`` in lazy_loading.js.
    """
    for message in reversed(messages):
        if message.get("role") != "user" or _is_tool_result_only_message(message):
            continue
        text, origin = _preferred_user_text_for_message(message)
        if text:
            return text, origin
    return "", "human"


def _session_user_text(messages: list[dict]) -> str:
    return _session_user_title(messages)[0]


def _extract_metadata(record_json: str) -> dict | None:
    """Extract sidebar-relevant metadata from a raw JSON record string."""
    try:
        r = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return _extract_metadata_from_record(r)


def _extract_metadata_from_record(r: dict) -> dict | None:
    """Extract sidebar metadata from a raw record without embedding full payloads.

    The returned dict contains only fields needed for sidebar rendering,
    filtering, and search previews.
    """
    if not isinstance(r, dict):
        return None

    req = _dict_or_empty(r.get("request"))
    body = _dict_or_empty(req.get("body"))
    resp = _dict_or_empty(r.get("response"))
    raw_resp_body = resp.get("body")
    resp_body = _dict_or_empty(raw_resp_body)
    request_events = _iter_request_events(req)
    stream_events = _iter_response_events(resp)
    if not stream_events:
        stream_events = _parse_sse_data_frames(raw_resp_body)
    created_response = _last_response_payload_for_event(stream_events, "response.created")
    completed_response = _last_response_payload_for_event(stream_events, "response.completed")
    request_event_bodies = [_event_payload(event) for event in request_events]
    response_output = resp_body.get("output")
    response_output_count = (
        len(response_output) if isinstance(response_output, list) else _response_output_count_from_events(stream_events)
    )

    # Token usage — from response.body.usage or terminal stream event.
    # A WebSocket record can carry several completed responses. There is one
    # metadata stub per record, and lazy and dashboard viewers read cost from that
    # stub alone (their embedded index is empty), so reading only the last
    # response would drop every earlier one from the displayed total.
    response_groups = _websocket_response_groups(stream_events) if stream_events else []
    group_usages: list[dict] = []
    group_models: list[str] = []
    provider = provider_namespace(r)
    if len(response_groups) > 1:
        for group in response_groups:
            payload = _last_response_payload_for_event(group, "response.completed")
            group_usages.append(normalize_usage(payload.get("usage") or {}))
            group_models.append(
                _first_priced_model(body.get("model", ""), provider=provider, billed=payload.get("model"))
            )

    usage = resp_body.get("usage") or _extract_gemini_response_usage(raw_resp_body) or {}
    if not usage:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                usage = (data.get("response") or {}).get("usage") or {}
                if usage:
                    break
    usage = normalize_usage(usage)
    if group_usages:
        usage = _sum_usage(group_usages)

    # System prompt hint (first 200 chars)
    sys_text = ""
    if isinstance(body.get("system"), str):
        sys_text = body["system"]
    elif isinstance(body.get("system"), list):
        parts = []
        for s in body["system"]:
            if isinstance(s, str):
                parts.append(s)
            elif isinstance(s, dict):
                parts.append(s.get("text", ""))
        sys_text = "\n".join(parts)
    elif isinstance(body.get("instructions"), str):
        sys_text = body["instructions"]
    elif _is_gemini_request_body(body):
        sys_text = _extract_gemini_system(body)

    # Messages
    msgs = _extract_request_messages(body)

    metadata = _dict_or_empty(body.get("metadata"))
    headers = _dict_or_empty(req.get("headers"))
    codex_app_session_id = metadata.get("codex_app_session_id") or headers.get("x-codex-app-session-id")
    if not isinstance(codex_app_session_id, str):
        codex_app_session_id = ""

    # Tool names from request
    tools = _extract_gemini_tools(body) or body.get("tools") or []
    tool_names = [_tool_display_name(t) for t in tools if isinstance(t, dict)]

    # Response tool names (tool_use blocks in response content)
    response_tool_names = []
    # Try response.body.content first
    rc = resp_body.get("content") or []
    if rc:
        for block in rc:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                response_tool_names.append(block.get("name", ""))
    else:
        response_tool_names.extend(_extract_response_tool_names(resp_body.get("output") or []))
    if not response_tool_names:
        response_tool_names.extend(_extract_response_tool_names_from_output_item_events(stream_events))
    if not response_tool_names:
        response_tool_names.extend(_extract_gemini_response_tool_names(raw_resp_body))
    if not response_tool_names:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                response_tool_names.extend(
                    _extract_response_tool_names((data.get("response") or {}).get("output") or [])
                )
                break

    # Error info
    error_msg = ""
    err_obj = resp_body.get("error")
    if isinstance(err_obj, dict):
        error_msg = err_obj.get("message", "")

    # A WebSocket or Responses-API record often names the model only in the
    # streamed response payload, so a request body without one is not the end of
    # the search — otherwise the turn stays unpriced.
    model = _first_priced_model(
        body.get("model", ""),
        _model_from_path(req.get("path", "")),
        completed_response.get("model", ""),
        created_response.get("model", ""),
        provider=provider,
        billed=resp_body.get("model", "") or completed_response.get("model", "") or created_response.get("model", ""),
    )

    search_calls = _completed_web_search_calls(output=resp_body.get("output"), events=stream_events)
    cost_fields = (
        _aggregate_cost_fields(group_models, group_usages, body, record=r, search_calls=search_calls)
        if group_usages
        else _cost_fields(model, usage, body, record=r, search_calls=search_calls)
    )

    session_user_text, session_user_origin = _session_user_title(msgs)

    return {
        "turn": r.get("turn"),
        "request_id": r.get("request_id", ""),
        "timestamp": r.get("timestamp", ""),
        "duration_ms": r.get("duration_ms", 0),
        "transport": r.get("transport", ""),
        "method": req.get("method", ""),
        "path": req.get("path", ""),
        "model": model,
        "request_generate": _first_bool(
            body.get("generate"),
            *(event_body.get("generate") for event_body in request_event_bodies if isinstance(event_body, dict)),
            created_response.get("generate"),
        ),
        "response_generate": _first_bool(
            resp_body.get("generate"),
            completed_response.get("generate"),
            created_response.get("generate"),
        ),
        "response_output_count": response_output_count,
        "status": resp.get("status", 0),
        "error_message": error_msg,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_in_input": bool(usage.get("cache_read_in_input")),
        **cost_fields,
        "has_system": bool(sys_text),
        "message_count": len(msgs),
        "session_user_text": session_user_text,
        # Only when it differs from the default the browser would infer anyway,
        # so the common case adds no bytes to every stub in a large capture.
        **({"session_user_origin": session_user_origin} if session_user_origin != "human" else {}),
        "cursor_turn": body.get("cursor_turn") if isinstance(body.get("cursor_turn"), int) else None,
        "cursor_step": body.get("cursor_step") if isinstance(body.get("cursor_step"), int) else None,
        "codex_app_session_id": codex_app_session_id,
        "sys_hint": sys_text[:200],
        "tool_names": tool_names,
        "response_tool_names": response_tool_names,
    }


def _pricing_data_js(cost_index: dict[str, dict]) -> str:
    """Return the JS consts carrying precomputed cost and price provenance.

    The viewer formats and sums these; it never holds a price table of its own.
    """
    index_js = json.dumps(cost_index, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    meta_js = json.dumps(pricing_metadata(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f"const EMBEDDED_COST_INDEX = {index_js};\nconst EMBEDDED_PRICING_META = {meta_js};\n"


def _generate_html_viewer(
    trace_path: Path,
    html_path: Path,
    *,
    display_trace_path: str | Path | None = None,
    display_html_path: str | Path | None = None,
) -> None:
    """Read viewer.html template, embed JSONL data, write self-contained HTML."""
    if trace_path.exists():
        text = trace_path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if is_compact_trace_bundle(parsed):
            _generate_html_viewer_from_compact_bundle(
                parsed,
                html_path,
                display_trace_path=display_trace_path if display_trace_path is not None else trace_path.absolute(),
                display_html_path=display_html_path if display_html_path is not None else html_path.absolute(),
            )
            return

    records: list[dict] = []
    if trace_path.exists():
        with open(trace_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(_normalize_record_for_viewer(line))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        records.append(record)
    _generate_html_viewer_from_compact_bundle(
        build_compact_trace_bundle(records),
        html_path,
        display_trace_path=display_trace_path if display_trace_path is not None else trace_path.absolute(),
        display_html_path=display_html_path if display_html_path is not None else html_path.absolute(),
    )


def _generate_html_viewer_from_compact_bundle(
    compact_bundle: dict,
    html_path: Path,
    *,
    display_trace_path: str | Path,
    display_html_path: str | Path,
) -> None:
    """Write a self-contained HTML viewer that embeds compact trace data."""
    if not VIEWER_TEMPLATE_PATH.exists():
        return
    if not is_compact_trace_bundle(compact_bundle):
        raise ValueError(f"Expected {COMPACT_TRACE_MARKER} compact trace bundle.")

    trace_path_label = str(display_trace_path)
    html_path_label = str(display_html_path)
    compact_js = json.dumps(compact_bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    jsonl_path_js = json.dumps(trace_path_label)
    html_path_js = json.dumps(html_path_label)
    version_js = json.dumps(CLAUDE_TAP_VERSION)
    try:
        cost_index = _build_cost_index(materialize_compact_trace_bundle(compact_bundle))
    except ValueError:
        cost_index = {}
    data_js = (
        f"const EMBEDDED_TRACE_COMPACT_DATA = {compact_js};\n"
        f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
        f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
        f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        f"{_pricing_data_js(cost_index)}"
    )

    html = _read_viewer_template()
    html = html.replace(
        VIEWER_SCRIPT_ANCHOR,
        f"<script>\n{data_js}</script>\n{VIEWER_SCRIPT_ANCHOR}",
        1,
    )
    html_path.write_text(html, encoding="utf-8")


def _generate_html_viewer_from_metadata(
    metadata: list[dict],
    html_path: Path,
    *,
    display_trace_path: str | Path,
    display_html_path: str | Path,
    records_api_path: str | Path,
) -> None:
    """Write an online viewer that fetches full records on demand."""
    if not VIEWER_TEMPLATE_PATH.exists():
        return

    trace_path_label = str(display_trace_path)
    html_path_label = str(display_html_path)
    records_api_label = str(records_api_path)
    meta_js = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    jsonl_path_js = json.dumps(trace_path_label)
    html_path_js = json.dumps(html_path_label)
    records_api_js = json.dumps(records_api_label)
    version_js = json.dumps(CLAUDE_TAP_VERSION)
    data_js = (
        f"const EMBEDDED_TRACE_META = {meta_js};\n"
        f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
        f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
        f"const __TRACE_RECORDS_API__ = {records_api_js};\n"
        f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        # Cost already rides on each metadata record, so only provenance is added.
        f"{_pricing_data_js({})}"
    )

    html = _read_viewer_template()
    html = html.replace(
        VIEWER_SCRIPT_ANCHOR,
        f"<script>\n{data_js}</script>\n{VIEWER_SCRIPT_ANCHOR}",
        1,
    )
    html_path.write_text(html, encoding="utf-8")


def _generate_html_viewer_from_records(
    record_json_lines: list[str],
    html_path: Path,
    *,
    display_trace_path: str | Path,
    display_html_path: str | Path,
) -> None:
    """Write a self-contained HTML viewer from already-loaded JSON records."""
    if not VIEWER_TEMPLATE_PATH.exists():
        return

    # Escape </ sequences so embedded record JSON cannot prematurely close the
    # surrounding <script> / <script type="text/plain"> blocks. Forward-proxy
    # mode can capture arbitrary HTTPS upstreams whose bodies legitimately
    # contain </script>; without this, the browser closes the data block early
    # and renders the captured HTML as page content. JSON's \/ is a valid
    # escape for /, so the parsed JSON value is unchanged.
    records = [rec.replace("</", "<\\/") for rec in record_json_lines]

    trace_path_label = str(display_trace_path)
    html_path_label = str(display_html_path)
    jsonl_path_js = json.dumps(trace_path_label)
    html_path_js = json.dumps(html_path_label)
    version_js = json.dumps(CLAUDE_TAP_VERSION)

    use_lazy = len(records) > LAZY_THRESHOLD

    if use_lazy:
        # Extract metadata for sidebar rendering
        meta_list = []
        for rec in records:
            meta = _extract_metadata(rec)
            if meta is not None:
                meta_list.append(meta)

        meta_js = json.dumps(meta_list, separators=(",", ":"))

        raw_lines = "\n".join(records)

        data_js = (
            f"const EMBEDDED_TRACE_META = {meta_js};\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
            # Cost already rides on each metadata record, so only provenance is added.
            f"{_pricing_data_js({})}"
        )

        html = _read_viewer_template()
        # Inject data script + raw JSONL block before the main <script> tag
        html = html.replace(
            VIEWER_SCRIPT_ANCHOR,
            f"<script>\n{data_js}</script>\n"
            f'<script type="text/plain" id="trace-raw">\n{raw_lines}\n</script>\n'
            f"{VIEWER_SCRIPT_ANCHOR}",
            1,
        )
    else:
        # Small trace: inline all data as before
        parsed_records: list[dict] = []
        for rec in record_json_lines:
            try:
                parsed = json.loads(rec)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                parsed_records.append(parsed)
        data_js = (
            "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_path_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_path_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
            f"{_pricing_data_js(_build_cost_index(parsed_records))}"
        )

        html = _read_viewer_template()
        html = html.replace(
            VIEWER_SCRIPT_ANCHOR,
            f"<script>\n{data_js}</script>\n{VIEWER_SCRIPT_ANCHOR}",
            1,
        )

    html_path.write_text(html, encoding="utf-8")
