"""HTML viewer generation – embed JSONL data into a self-contained HTML file."""

from __future__ import annotations

import base64
import json
import json.encoder
import math
import re
from collections.abc import Iterator
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


# Size at which a single tool result is worth pointing out, in UTF-8 bytes.
#
# Tool result sizes are heavy-tailed: sampling local Claude Code transcripts put
# the median in the low hundreds of bytes while the largest result ran past
# 600,000.  10,000 bytes sits far out on that tail, so it flags the few results
# big enough to dominate a turn's context without touching the ordinary file
# reads and greps that make up almost all of the distribution.
#
# Measured in bytes rather than characters so that CJK and emoji output, which
# costs two to four bytes per character, is not judged smaller than it is.  The
# JS detector applies the same threshold to the same unit; the two must agree or
# a badge appears in one view and not the other.
TOOL_BLOAT_MIN_BYTES = 10000


def _text_size_bytes(text: str) -> int:
    # Match the browser TextEncoder: unpaired UTF-16 surrogates become U+FFFD
    # (three UTF-8 bytes). Python's utf-8 "replace" would emit a one-byte "?".
    return len(text.encode("utf-16", "surrogatepass").decode("utf-16", "replace").encode("utf-8"))


def _js_number_text(value: float) -> str:
    """Return the digits ``JSON.stringify`` would emit for ``value``.

    ECMA-262 Number::toString picks between positional and exponential notation
    by decimal exponent: positional while the exponent stays within 21 digits on
    the left and 6 zeros on the right, exponential outside that. Python's repr
    switches at 17 and 5 instead, so the two disagree in both directions --
    ``1e16`` prints as ``1e+16`` here and ``10000000000000000`` there, while
    ``1e-7`` prints as ``1e-07`` here and ``1e-7`` there.

    Casting integral floats to ``int`` fixed the common ``1.0`` case but made
    large ones worse: ``int(1e21)`` is 22 digits where the browser emits five
    characters, so 500 such values measured 11,501 bytes in the sidebar and
    3,001 in the detail view -- a badge on one side only, which is exactly what
    this shared serializer exists to prevent.

    Both runtimes round-trip a double through its shortest exact decimal, so
    ``repr`` supplies the digits and only the placement needs redoing.
    """
    if value == 0:
        # Covers -0.0, which JSON.stringify renders as "0".
        return "0"
    sign = "-" if value < 0 else ""
    mantissa, _, exponent = repr(abs(value)).partition("e")
    e10 = int(exponent) if exponent else 0
    int_part, _, frac_part = mantissa.partition(".")

    # `digits` holds the significant decimal digits; `n` is the position of the
    # decimal point relative to their start, as in the spec's k/n/s split.
    leading = int_part.lstrip("0")
    if leading:
        n = len(leading) + e10
        digits = (leading + frac_part).rstrip("0") or "0"
    else:
        after_zeros = frac_part.lstrip("0")
        n = e10 - (len(frac_part) - len(after_zeros))
        digits = after_zeros.rstrip("0") or "0"

    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + digits
    e = n - 1
    lead = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{sign}{lead}e{'+' if e >= 0 else '-'}{abs(e)}"


_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _js_string_text(value: str) -> str:
    """Quote ``value`` the way ``JSON.stringify`` would, surrogates included.

    Since ES2019 ``JSON.stringify`` is well-formed: a UTF-16 code unit with no
    partner is escaped as six ASCII characters (``\\ud800``) rather than emitted
    raw. Python's ``encode_basestring`` leaves it alone, and with
    ``ensure_ascii=False`` it reaches the output as itself, where
    :func:`_text_size_bytes` folds it into a 3-byte U+FFFD -- half the browser's
    six. A payload of 2,000 lone surrogates measured 6,012 bytes here against
    12,012 there, enough to drop a badge from the sidebar that the opened entry
    still shows.

    Paired surrogates are left to ``encode_basestring``, which writes the astral
    character itself, exactly as the browser does.
    """
    encoded = json.encoder.encode_basestring(value)  # type: ignore[attr-defined]
    if not _LONE_SURROGATE_RE.search(encoded):
        return encoded
    # Only unpaired units are escaped, so pairs have to survive the scan: step
    # over a low surrogate that follows a high one instead of rewriting it.
    out: list[str] = []
    index = 0
    length = len(encoded)
    while index < length:
        char = encoded[index]
        code = ord(char)
        if 0xD800 <= code <= 0xDBFF and index + 1 < length and 0xDC00 <= ord(encoded[index + 1]) <= 0xDFFF:
            out.append(encoded[index : index + 2])
            index += 2
            continue
        out.append(char if not 0xD800 <= code <= 0xDFFF else f"\\u{code:04x}")
        index += 1
    return "".join(out)


class _JsNumberEncoder(json.JSONEncoder):
    """A JSON encoder that formats floats the way ``JSON.stringify`` does.

    The C encoder hard-codes ``float.__repr__``, which is why floats are handed
    to the pure-Python path with :func:`_js_number_text` as the formatter. That
    path is only reached when ``c_make_encoder`` is unavailable, so it is asked
    for explicitly rather than left to ``json.dumps``.
    """

    def iterencode(self, o: object, _one_shot: bool = False) -> Iterator[str]:
        # json.dumps widens an integer indent to spaces before handing it over;
        # the pure-Python path concatenates it, so it has to be widened here.
        indent = " " * self.indent if isinstance(self.indent, int) else self.indent
        return json.encoder._make_iterencode(  # type: ignore[attr-defined]
            {} if self.check_circular else None,
            self.default,
            _js_string_text,
            indent,
            _js_number_text,
            self.key_separator,
            self.item_separator,
            self.sort_keys,
            self.skipkeys,
            _one_shot,
        )(o, 0)


def _js_json_value(value: object) -> object:
    """Coerce numbers to what JavaScript would have parsed them as.

    NaN and the infinities become ``null`` there, while Python's json.dumps
    writes the bare words ``NaN`` and ``Infinity``, which are not JSON at all.

    Integers need the same treatment for the opposite reason: ``json.loads``
    keeps them as arbitrary-precision ``int``, so they never reach
    :func:`_js_number_text` and are written digit for digit. ``JSON.parse`` has
    only doubles, so ``999999999999999999999999`` comes back as ``1e+24`` and
    ``9007199254740993`` rounds to ``...992``. 500 of the former measured 12,507
    bytes here against 3,007 in the browser -- a badge on one side only, which is
    what this shared serializer exists to prevent. Anything too large for a
    double parses as Infinity there, hence ``null``.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        try:
            return _js_json_value(float(value))
        except OverflowError:
            # Beyond the double range JSON.parse yields Infinity, which
            # JSON.stringify then writes as null.
            return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _js_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_json_value(item) for item in value]
    return value


def _bloat_json(value: object) -> str:
    """Serialize a structured payload the way the JS detector does.

    `JSON.stringify` emits no separator padding and leaves non-ASCII characters
    as themselves; Python's defaults do the opposite on both counts.  A CJK
    result would otherwise measure roughly six times larger here than in the
    browser, which is enough to put a sidebar badge on a turn whose detail view
    then shows no warning.  Integral floats are rewritten so ``1.0`` becomes
    ``1``, matching JSON.stringify.
    """
    encoder = _JsNumberEncoder(ensure_ascii=False, separators=(",", ":"), default=str)
    return encoder.encode(_js_json_value(value))


_BLOAT_IMAGE_TYPES = {"image", "input_image", "computer_screenshot"}


def _is_bloat_image_type(value: object) -> bool:
    # A `type` carrying a list or dict is a domain field, not a block tag, and
    # testing it against a set would raise `unhashable type` and take metadata
    # generation down for the whole trace.  `Set.has` in the browser returns
    # false for the same value, so restricting the test to strings is what
    # keeps the two detectors agreeing.
    return isinstance(value, str) and value in _BLOAT_IMAGE_TYPES


def _is_recognized_image_object(value: object) -> bool:
    """True only for an image block or nested image object, not a domain field."""
    if not isinstance(value, dict):
        return False
    if _is_bloat_image_type(value.get("type")):
        return True
    return any(key in value for key in ("source", "media_type", "image_url", "data"))


def _is_image_payload(part: dict) -> bool:
    # `computer_screenshot` is the Responses shape for a screenshot handed back
    # from a computer-use call; it carries the same data URL as an image block.
    # A truthy domain field named `image` (for example a container tag) is not
    # an image block and still consumes context as text.
    if _is_bloat_image_type(part.get("type")):
        return True
    return _is_recognized_image_object(part.get("image"))


def _tool_result_text(rc: object) -> str:
    """Flatten a tool result payload to the text that consumes context.

    Image blocks are dropped: they are billed by dimension, not by tokenizing
    their base64 payload, so counting those characters would report a
    screenshot as tens of thousands of text tokens.
    """
    if isinstance(rc, str):
        return rc
    if isinstance(rc, list):
        parts: list[str] = []
        for part in rc:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                if part is not None:
                    parts.append(_bloat_json(part))
                continue
            if _is_image_payload(part):
                continue
            text = part.get("text")
            # Collapse to `text` only for `type: "text"`, the one shape
            # renderContent special-cases; it dumps every other part whole, so
            # sizing `text` for those hides whatever sits beside it.  A `text`
            # alone is not enough (`{"text": "summary", "logs": <25 KB>}` has no
            # `type` at all), and neither is a text-ish type: an `output_text`
            # part is displayed in full, so 25 KB of siblings read as "summary".
            # Only a string `text` is usable as-is regardless.  A null, numeric,
            # or structured value would otherwise reach "".join() and raise,
            # taking down metadata generation for the whole trace.
            if isinstance(text, str) and part.get("type") == "text":
                parts.append(text)
            else:
                parts.append(_bloat_json(part))
        return "\n".join(parts)
    if isinstance(rc, dict):
        if _is_image_payload(rc):
            return ""
        return _bloat_json(rc)
    if rc is not None:
        return _bloat_json(rc)
    return ""


def _bloat_result_payload(b: dict) -> tuple[bool, object]:
    """Select the field a tool-result block keeps its payload in.

    Returns whether the block is a tool result at all, and if so the value to
    measure.  A `function_call_output` keeps its payload in `output`, not in
    `content`, so reading `content` sizes every one of them as empty.
    """
    block_type = b.get("type")
    if block_type == "tool_result":
        return True, b.get("content")
    if block_type in {"function_call_output", "computer_call_output", "custom_tool_call_output"}:
        return True, b.get("output") if "output" in b else b.get("content")
    if isinstance(b.get("toolResult"), dict):
        # Bedrock Converse uses camelCase native blocks.
        return True, b["toolResult"].get("content")
    return False, None


def _detect_tool_bloat(msgs: list) -> dict | None:
    """Summarize oversized tool results in a request's messages.

    Returns the count of oversized results and the largest one's size, or None
    when nothing crosses the threshold.  `size_kb` is a float so the viewer can
    render it without having to trust a string from the trace.

    The badge derives its KB figure from `byte_count`, not from `size_kb`: the two
    languages round halfway cases in opposite directions, so a pre-rounded value
    made the sidebar and the opened entry disagree on the same payload.  `size_kb`
    remains for metadata written before `byte_count` existed.
    """
    worst_bytes = 0
    count = 0
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        # OpenAI Chat Completions puts the result straight on a tool-role
        # message instead of in a tool_result block.  The payload is usually a
        # string, but a normalized Responses item arrives as a block list; the
        # viewer wraps either shape in a tool_result before rendering, so both
        # have to be measured or a badge appears without its warning banner.
        # Display wraps any tool-role payload in one outer tool_result. Measure
        # that same combined payload so lazy metadata and the opened entry agree.
        if msg.get("role") == "tool":
            size = _text_size_bytes(_tool_result_text(content))
            if size >= TOOL_BLOAT_MIN_BYTES:
                count += 1
                worst_bytes = max(worst_bytes, size)
            continue
        blocks = content if isinstance(content, list) else [content]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            matched, rc = _bloat_result_payload(b)
            if not matched:
                continue
            size = _text_size_bytes(_tool_result_text(rc))
            if size >= TOOL_BLOAT_MIN_BYTES:
                count += 1
                worst_bytes = max(worst_bytes, size)

    if count == 0:
        return None
    return {
        "count": count,
        "byte_count": worst_bytes,
        "size_kb": round(worst_bytes / 1024, 1),
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
    """Mirror of ``geminiFunctionResponseContent`` in renderers.js.

    The serialization has to match byte for byte, not just in meaning: the tool
    bloat scan measures this string's length, and the browser measures the
    browser's. A one-line dump of an array of 1,500 short strings is ~7.5 KB
    against the pretty-printed ~10.5 KB, so a divergence here drops a badge from
    the sidebar that the detail view still shows. Hence indent=2 to match
    ``JSON.stringify(output, null, 2)``, and an empty string for a missing
    payload where JS returns one rather than the text "null".
    """
    payload = resp.get("response")
    # Unwrap `output` only when it is the whole response.  A sibling field beside
    # it is real result data the model was given, so unwrapping dropped it from the
    # display and from this measurement at once: `{"output": "ok", "logs": <25 KB>}`
    # showed two bytes and earned no badge.  Widening only the bloat payload would
    # trade that for the opposite bug, a badge whose bytes the reader cannot find.
    if isinstance(payload, dict) and "output" in payload and len(payload) == 1:
        output = payload["output"]
    else:
        output = payload
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    encoder = _JsNumberEncoder(ensure_ascii=False, indent=2)
    return encoder.encode(_js_json_value(output))


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


def _extract_gemini_request_messages(body: dict, *, for_bloat: bool = False) -> list[dict]:
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
        # Display already splits functionResponse parts into separate blocks.
        # Collapsing them to role=tool would make the bloat scan wrap the
        # whole list as one result, so two 6 KB replies look oversized here
        # and clean once the entry is opened.
        if not for_bloat and all(block.get("type") == "tool_result" for block in blocks):
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


def _normalized_screenshot_block(output: object) -> dict | None:
    """Map a computer-use screenshot result to an ordinary image block.

    A `computer_call_output` carries its screenshot as
    `{"type": "computer_screenshot", "image_url": "data:image/png;base64,…"}`.
    Serializing that to a string would hand the base64 to the bloat detector as
    if it were result text, and an image is billed by dimension rather than by
    tokenizing its encoding.
    """
    if not isinstance(output, dict) or output.get("type") != "computer_screenshot":
        return None
    url = output.get("image_url")
    if not isinstance(url, str) or not url:
        return None
    return {"type": "input_image", "image_url": url}


def _response_tool_result_content(item: dict, *, for_bloat: bool = False) -> object:
    if item.get("type") == "tool_search_output":
        if for_bloat:
            tools = item.get("tools")
            return tools if isinstance(tools, list) else item
        return _tool_search_output_content(item)
    leftover = {
        key: value for key, value in item.items() if key not in {"id", "type", "status", "call_id", "execution"}
    }
    if "output" in item:
        output = item.get("output")
        if isinstance(output, str):
            return output
        screenshot = _normalized_screenshot_block(output)
        if screenshot is not None:
            return [screenshot]
        if for_bloat:
            return output
        return json.dumps(output, ensure_ascii=False)
    if for_bloat:
        return leftover
    return json.dumps(leftover, ensure_ascii=False)


def _extract_request_messages(body: dict, *, for_bloat: bool = False) -> list[dict]:
    if not isinstance(body, dict):
        return []
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        return [msg for msg in msgs if isinstance(msg, dict)]

    if _is_gemini_request_body(body):
        return _extract_gemini_request_messages(body, for_bloat=for_bloat)

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
            normalized.append({"role": "tool", "content": _response_tool_result_content(item, for_bloat=for_bloat)})
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


def _clean_session_user_text(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, str) and decoded.strip():
            value = decoded.strip()

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
        return session.group(1).strip()

    first_tag = re.match(r"^<([A-Za-z_-]+)", value)
    if first_tag and first_tag.group(1).lower() in {
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
    }:
        return ""

    if value.startswith(("# AGENTS.md instructions", "<INSTRUCTIONS>")):
        return ""
    if value.startswith("# Files mentioned by the user:"):
        return ""
    if re.match(r"^</?image(_input)?(\s+[^>]*)?>$", value, flags=re.IGNORECASE):
        return ""
    if re.match(r"^\[SUGGESTION MODE:", value, flags=re.IGNORECASE):
        return ""
    if re.match(r"^(web page content|page content|网页内容)\s*[:：]", value, flags=re.IGNORECASE):
        return ""
    if re.match(r"^\[Image:\s*source:", value, flags=re.IGNORECASE):
        return ""
    return re.sub(r"^\[Image #\d+\]\s*", "", value, flags=re.IGNORECASE).strip()


def _session_text_from_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _clean_session_user_text(content)
    if isinstance(content, dict):
        item_type = content.get("type")
        if item_type in {"tool_result", "function_call_output"}:
            return ""
        for key in ("text", "output"):
            value = content.get(key)
            if isinstance(value, str):
                cleaned = _clean_session_user_text(value)
                if cleaned:
                    return cleaned
        if "content" in content:
            return _session_text_from_content(content.get("content"))
        return ""
    if isinstance(content, list):
        for item in content:
            text = _session_text_from_content(item)
            if text:
                return text
    return ""


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


def _first_user_text(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") != "user" or _is_tool_result_only_message(message):
            continue
        text = _session_text_from_content(message.get("content"))
        if text:
            return text
    return ""


def _latest_user_text(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user" or _is_tool_result_only_message(message):
            continue
        text = _session_text_from_content(message.get("content"))
        if text:
            return text
    return ""


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

    tool_bloat = _detect_tool_bloat(_extract_request_messages(body, for_bloat=True))

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
        "tool_bloat": tool_bloat,
        "has_system": bool(sys_text),
        "message_count": len(msgs),
        "session_user_text": _latest_user_text(msgs) or _first_user_text(msgs),
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
