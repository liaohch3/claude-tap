"""Model pricing adapter over a vendored LiteLLM price table.

This module is the single owner of price data and cost arithmetic. The viewer
receives numbers it only has to format; nothing downstream re-derives a rate.

Three details the previous JavaScript implementation got wrong and this module
handles explicitly:

* **Long-context tiers.** Anthropic and Google bill input above 200K tokens at a
  higher rate. LiteLLM carries those as ``*_above_200k_tokens`` fields, and they
  are per-model: ``claude-sonnet-4-20250514`` has them, ``claude-opus-4-1`` (a
  200K-max model) does not.
* **Cache write TTL.** A 1-hour cache write costs more than the default 5-minute
  one (``cache_creation_input_token_cost_above_1hr``).
* **Where cached tokens are counted.** Anthropic reports cache reads in a bucket
  separate from ``input_tokens``; OpenAI-shaped gateways report them inside it.
  Billing the embedded case without subtracting first charges those tokens twice.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import unquote

from claude_tap.bedrock import bedrock_model_from_path

PRICES_PATH = Path(__file__).parent / "model_prices.json"

# The tier boundary LiteLLM encodes in its *_above_200k_tokens field names.
LONG_CONTEXT_THRESHOLD = 200_000

# No context window is remotely this large. A count past it is a malformed
# capture rather than a bill, and bounding it here keeps an arbitrary-precision
# JSON integer from raising OverflowError when it meets a float rate.
MAX_TOKEN_COUNT = 1_000_000_000_000

_MODEL_FROM_PATH_RE = re.compile(r"/models?/([^:?/]+)")
# Bedrock and Vertex prefix the region or publisher onto the model id.
_STRIP_PREFIX_RE = re.compile(
    r"^(?:bedrock/|openrouter/|orcarouter/|azure_ai/|azure/|vertex_ai/|gemini/|"
    r"(?:us|eu|apac|au|jp|ca|global)\.)+",
    re.IGNORECASE,
)

# Host/URL fragments that identify a LiteLLM table namespace. Order is
# significant only in that each host maps to one prefix; they do not overlap.
#
# A host missing from this table is not a small rounding error: the namespace is
# the only way its ids are spelled. All 22 `moonshot/` keys and all 44 `xai/`
# ones have no bare twin, so a Kimi or Grok capture without its prefix resolves
# to nothing and drops out of the cost totals entirely. Every route the support
# matrix lists as reachable belongs here.
_PROVIDER_HOSTS = (
    ("openrouter.ai", "openrouter"),
    # OrcaRouter is an OpenAI- and Anthropic-compatible gateway that names its
    # own routes under an ``orcarouter/`` namespace (``orcarouter/auto``,
    # ``orcarouter/fusion``, ...) while passing vendor ids through unchanged.
    # Its own SKUs are zero-markup, so pricing falls through to the concrete
    # underlying model, but the namespace must be recognized for the ``/v1``
    # base URL to resolve like OpenRouter does.
    ("api.orcarouter.ai", "orcarouter"),
    ("generativelanguage.googleapis.com", "gemini"),
    ("aiplatform.googleapis.com", "vertex_ai"),
    ("vertexai.googleapis.com", "vertex_ai"),
    # Azure OpenAI vs Azure AI Foundry keep different LiteLLM namespaces.
    # A bare OpenAI key would silently understate Azure regional SKUs.
    #
    # Known limitation: this resolves to the generic `azure/` rate, not the
    # deployment tier. The table also carries azure/us/, azure/eu/, azure/global/
    # and azure/global-standard/ variants for 26 model ids, and the tier is not
    # recoverable from a capture -- the deployment name in the path is chosen by
    # the account owner and carries no tier marker. The generic figure is the
    # conservative end for 23 of those 26 (us and eu bill above it); it overstates
    # gpt-4o-2024-11-20 and gpt-4o-mini on Global Standard by 10%. Refusing to
    # price Azure without the tier would unprice all 131 azure/ keys, which costs
    # more accuracy than the 10% it would avoid.
    ("openai.azure.com", "azure"),
    ("cognitiveservices.azure.com", "azure_ai"),
    ("services.ai.azure.com", "azure_ai"),
    # Kimi ships under Moonshot's namespace from either host, and Kimi Code
    # reaches api.moonshot.ai through `--tap-target`.
    ("api.moonshot.ai", "moonshot"),
    ("api.kimi.com", "moonshot"),
    # Grok Build CLI talks to a proxy host rather than api.x.ai.
    ("grok.com", "xai"),
    ("api.x.ai", "xai"),
    # DeepSeek's own rates; the bare ids exist but four of the eight keys are
    # namespace-only, so the prefix is what prices them.
    ("api.deepseek.com", "deepseek"),
)


class ModelRates(NamedTuple):
    """Per-token rates for one model, already resolved for the request size.

    A rate is ``None`` when the price table has no figure for that bucket, which
    is different from a rate of ``0.0``. Several entries genuinely lack cache
    pricing (``gpt-4o`` and ``gemini-2.5-pro`` carry no cache-creation cost), and
    treating that as free would report a confidently wrong total.
    """

    model: str
    input: float | None
    output: float | None
    cache_read: float | None
    cache_write: float | None
    cache_write_5m: float | None
    cache_write_1h: float | None
    long_context: bool


class EntryCost(NamedTuple):
    """What one traced request cost, and what it would have cost uncached."""

    cost: float
    uncached_cost: float
    saved: float
    model: str
    long_context: bool


@lru_cache(maxsize=1)
def _price_table() -> dict[str, Any]:
    """Return the vendored price table, or an empty table when unreadable.

    A missing or corrupt price file must degrade to "no cost shown", never
    break viewer generation.
    """
    try:
        payload = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"__meta__": {}, "models": {}}
    if not isinstance(payload, dict):
        return {"__meta__": {}, "models": {}}
    models = payload.get("models")
    meta = payload.get("__meta__")
    return {
        "__meta__": meta if isinstance(meta, dict) else {},
        "models": models if isinstance(models, dict) else {},
    }


def pricing_metadata() -> dict[str, Any]:
    """Return provenance for the price table, for display in the viewer.

    ``upstream_commit`` is what makes a quoted cost reproducible: the fetch date
    alone does not identify a revision of a file that changes several times a
    day, so it travels with the snapshot into the generated viewer.
    """
    meta = _price_table()["__meta__"]
    return {
        "source": meta.get("source", ""),
        "source_url": meta.get("source_url", ""),
        "upstream_commit": meta.get("upstream_commit", ""),
        "as_of": meta.get("fetched_on", ""),
        "model_count": meta.get("model_count", 0),
    }


def model_from_path(path: str) -> str:
    """Extract a model id from a request path.

    Bedrock and Vertex need opposite treatment of a colon. A Bedrock id ends in
    a version suffix that is part of the id (``...-v1:0``), while a Vertex path
    appends the method after one (``...:streamGenerateContent``). Bedrock routes
    therefore go through :func:`bedrock_model_from_path`, which also
    percent-decodes the id, and only the Vertex/Gemini shape keeps the colon
    cutoff. Truncating a Bedrock id at its version costs real money: the
    regional ``jp.anthropic.claude-sonnet-4-5-20250929-v1:0`` entry bills input
    at 3.3e-6, while the bare model the truncated id falls back to bills 3e-6.
    """
    if not isinstance(path, str):
        return ""
    bedrock = bedrock_model_from_path(path)
    if bedrock:
        return bedrock
    match = _MODEL_FROM_PATH_RE.search(path)
    return unquote(match.group(1)) if match else ""


def _provider_signal(source: object) -> str:
    """Return a lowercase host/url/path blob from a record, URL, or host string."""
    if isinstance(source, str):
        return source.lower()
    if not isinstance(source, dict):
        return ""
    req = source.get("request")
    req = req if isinstance(req, dict) else {}
    headers = req.get("headers")
    host = ""
    if isinstance(headers, dict):
        for key in ("Host", "host", ":authority"):
            value = headers.get(key)
            if isinstance(value, str) and value:
                host = value
                break
    return " ".join(
        part
        for part in (
            str(source.get("upstream_base_url") or ""),
            host,
            str(req.get("path") or ""),
        )
        if part
    ).lower()


def provider_namespace(source: object) -> str:
    """Return the LiteLLM key prefix implied by a captured upstream or host.

    The same model id is stored under several keys — ``deepseek/deepseek-chat``
    at DeepSeek's own rate, ``openrouter/deepseek/deepseek-chat`` at OpenRouter's
    — and a bare Gemini id is not the same entry as ``gemini/`` or
    ``vertex_ai/``. The captured host is what distinguishes them; looking the
    request's model string up as-is silently bills the wrong vendor.
    """
    signal = _provider_signal(source)
    if not signal:
        return ""
    for fragment, namespace in _PROVIDER_HOSTS:
        if fragment in signal:
            return namespace
    return ""


def _candidate_keys(model: str, provider: str = "") -> list[str]:
    """Return lookup keys for a model id, most specific first."""
    raw = model.strip()
    if not raw:
        return []
    candidates: list[str] = []
    prefix = provider.strip().strip("/")
    if prefix and not raw.lower().startswith(prefix.lower() + "/"):
        candidates.append(f"{prefix}/{raw}")
    candidates.append(raw)
    stripped = _STRIP_PREFIX_RE.sub("", raw)
    if stripped and stripped != raw:
        candidates.append(stripped)
    # Bedrock appends a version suffix (":0") and prefixes a publisher
    # ("anthropic.claude-..."); Vertex appends "@20240101".
    for base in list(candidates):
        trimmed = base.split(":", 1)[0].split("@", 1)[0]
        if trimmed and trimmed not in candidates:
            candidates.append(trimmed)
        if "." in trimmed:
            tail = trimmed.split(".", 1)[1]
            if tail and tail not in candidates:
                candidates.append(tail)
    return candidates


def _rate_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """Return only the billing rates, so provenance keys never affect equality."""
    return {k: v for k, v in entry.items() if "cost" in k or "rate" in k}


def _namespace_rate_differs(models: dict[str, Any], provider: str, model: str) -> bool:
    """True when this provider prices ``model`` differently from the bare id.

    A namespaced key that repeats the bare rates is a mirror: Vertex stores
    every Claude id that way, and refusing the bare fallback there would leave
    real traffic unpriced. A namespaced key that *changes* any rate is a
    distinct SKU -- an Azure region, a Gemini generation, a Bedrock
    republication -- and falling through to the bare id bills it at another
    product's price. Comparing the rates decides which case this is, so a
    provider added to the table later is covered without editing this file.
    """
    prefix = provider.strip().strip("/").lower()
    if not prefix:
        return False
    for key in _candidate_keys(model, provider):
        entry = models.get(key)
        if not isinstance(entry, dict) or not _key_matches_provider(key, provider):
            continue
        _, _, tail = key.partition("/")
        bare = models.get(tail) or models.get(tail.lower())
        if isinstance(bare, dict) and _rate_fields(entry) != _rate_fields(bare):
            return True
    return False


def _contains_key_as_segment(lowered_model: str, lowered_key: str) -> bool:
    """True when the model name carries ``lowered_key`` as a routed segment.

    A gateway names the model after its route, so the id begins the name or
    begins a route segment, and a variant suffix may follow it. That admits
    ``my-pool/claude-opus-5-preview`` and
    ``openrouter/anthropic/claude-sonnet-4-20250514`` while refusing
    ``my-gpt-4-deployment``, where the key sits in the middle of a name that
    may route anywhere. A trailing character other than a separator ends the
    match too, so ``pool/gpt-4omni`` names no known model.
    """
    if not lowered_key or lowered_key not in lowered_model:
        return False
    route_separators = "/.:@"
    start = 0
    while True:
        idx = lowered_model.find(lowered_key, start)
        if idx < 0:
            return False
        end = idx + len(lowered_key)
        begins_segment = idx == 0 or lowered_model[idx - 1] in route_separators
        # "-" ends the key and opens a variant suffix; anything else means the
        # key was only a fragment of a longer word.
        ends_cleanly = end == len(lowered_model) or lowered_model[end] in route_separators + "-"
        if begins_segment and ends_cleanly:
            return True
        start = idx + 1


def _key_matches_provider(key: str, provider: str) -> bool:
    """True when ``key`` belongs to the declared LiteLLM namespace."""
    prefix = provider.strip().strip("/").lower()
    if not prefix:
        return True
    lowered = key.lower()
    return lowered.startswith(prefix + "/") or lowered.startswith(prefix + ".")


def _lookup(model: str, provider: str = "") -> tuple[str, dict[str, Any]] | None:
    """Resolve a model id to a price entry, or None when unpriced.

    Traffic stays inside its namespace whenever that namespace prices the model
    differently from the bare id: falling through is how Azure regional SKUs
    were billed 10% low, and the table carries the same divergence for Gemini,
    Azure AI, Bedrock and DeepSeek keys. Where the namespaced rates only mirror
    the bare ones -- all of Vertex, most of every other namespace -- the bare
    fallback is what prices the traffic at all, so it is kept.
    """
    models = _price_table()["models"]
    strict = _namespace_rate_differs(models, provider, model)
    for key in _candidate_keys(model, provider):
        entry = models.get(key)
        if not isinstance(entry, dict):
            continue
        if strict and not _key_matches_provider(key, provider):
            continue
        return key, entry
    if strict:
        return None
    # Fall back to the longest known id the name carries as a *prefixed
    # segment*, which is what a gateway produces: "my-pool/claude-opus-5" names
    # the model after its route. An arbitrary substring does not — a deployment
    # called "my-gpt-4-deployment" may target any model on any plan, and
    # resolving it to the bundled `gpt-4` entry reports a confident cost for a
    # model that was never billed. Leaving it unpriced is the honest answer;
    # a genuine alias for a known billed model is already handled by the
    # response-model fallback in `entry_cost`.
    lowered = model.lower()
    best: tuple[str, dict[str, Any]] | None = None
    for key, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if not _contains_key_as_segment(lowered, key.lower()):
            continue
        # A route prefix hides the provider's own key from `_candidate_keys`, so
        # the segment match is where "my-pool/gpt-4o-mini" on Azure would reach
        # the bare OpenAI rate. Re-check the match inside the namespace and take
        # the provider's SKU when it prices this id differently.
        if _namespace_rate_differs(models, provider, key):
            scoped = _lookup(key, provider)
            if scoped is not None:
                key, entry = scoped
        if best is None or len(key) > len(best[0]):
            best = (key, entry)
    return best


def _rate(entry: dict[str, Any], field: str, tiered: str | None, long_context: bool) -> float | None:
    """Return the rate for ``field``, or None when the table carries no figure.

    A missing field is reported as None rather than 0.0 so callers can refuse to
    price a bucket instead of silently billing it free.
    """
    if long_context and tiered:
        value = entry.get(tiered)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _long_context_write_1h(entry: dict[str, Any], write_5m: float | None, base_1h: float) -> float:
    """Return the 1-hour cache-write rate above 200K tokens.

    LiteLLM carries no combined ``*_above_1hr_above_200k_tokens`` field, so the
    two adjustments have to be composed: the long-context tier scales the write
    rate, and the 1-hour TTL multiplies it again. Taking the tiered 5-minute rate
    alone would drop the TTL premium entirely — Sonnet 4 bills 3.75e-6/6e-6 at
    base and 7.5e-6 for a 5-minute write above 200K, so a 1-hour write there is
    1.2e-5, not 7.5e-6.
    """
    tiered_5m = entry.get("cache_creation_input_token_cost_above_200k_tokens")
    base_5m = entry.get("cache_creation_input_token_cost")
    if (
        isinstance(tiered_5m, (int, float))
        and not isinstance(tiered_5m, bool)
        and isinstance(base_5m, (int, float))
        and not isinstance(base_5m, bool)
        and base_5m > 0
    ):
        return float(tiered_5m) * (base_1h / float(base_5m))
    # No tiered write rate to scale: the TTL premium is the only known
    # adjustment, so keep it rather than falling back to the untiered figure.
    return max(base_1h, write_5m or 0.0)


def resolve_rates(
    model: str, *, prompt_tokens: int = 0, cache_ttl_1h: bool = False, provider: str = ""
) -> ModelRates | None:
    """Return rates for ``model``, tiered by ``prompt_tokens``.

    ``prompt_tokens`` is the full prompt size (input plus any cached tokens
    billed separately), because the long-context tier is selected by how large
    the prompt is, not by how much of it was billed at the uncached rate.

    ``cache_ttl_1h`` sets which rate ``cache_write`` reports for callers that
    only handle one TTL; both TTLs are always carried so a request that mixes
    5-minute and 1-hour breakpoints can bill each bucket at its own rate.

    ``provider`` is the LiteLLM namespace derived from the captured upstream
    (``openrouter``, ``gemini``, ``vertex_ai``). It is tried as a key prefix
    before the bare model id, so an OpenRouter DeepSeek turn is not billed at
    DeepSeek's own rate.
    """
    found = _lookup(model or "", provider)
    if found is None:
        return None
    key, entry = found
    long_context = prompt_tokens > LONG_CONTEXT_THRESHOLD and any(
        field in entry
        for field in (
            "input_cost_per_token_above_200k_tokens",
            "output_cost_per_token_above_200k_tokens",
            "cache_read_input_token_cost_above_200k_tokens",
            "cache_creation_input_token_cost_above_200k_tokens",
        )
    )
    write_5m = _rate(
        entry,
        "cache_creation_input_token_cost",
        "cache_creation_input_token_cost_above_200k_tokens",
        long_context,
    )
    # Models without an *_above_1hr field bill both TTLs at the same rate.
    write_1h = _rate(entry, "cache_creation_input_token_cost_above_1hr", None, long_context)
    if write_1h is None:
        write_1h = write_5m
    elif long_context:
        write_1h = _long_context_write_1h(entry, write_5m, write_1h)
    return ModelRates(
        model=key,
        input=_rate(entry, "input_cost_per_token", "input_cost_per_token_above_200k_tokens", long_context),
        output=_rate(entry, "output_cost_per_token", "output_cost_per_token_above_200k_tokens", long_context),
        cache_read=_rate(
            entry,
            "cache_read_input_token_cost",
            "cache_read_input_token_cost_above_200k_tokens",
            long_context,
        ),
        cache_write=write_1h if cache_ttl_1h else write_5m,
        cache_write_5m=write_5m,
        cache_write_1h=write_1h,
        long_context=long_context,
    )


def is_priced_model(model: str, *, provider: str = "") -> bool:
    """Return True when the price table can resolve ``model`` to an entry.

    Callers that hold several candidate names for one request — a gateway alias
    in the request body, the concrete model in the response — use this to pick a
    name that can actually be priced instead of taking the first non-empty one.
    """
    return isinstance(model, str) and bool(model.strip()) and _lookup(model, provider) is not None


def _int(value: Any) -> int:
    """Coerce a reported token count to a non-negative int.

    Two unusable shapes a capture can carry, both of which would otherwise abort
    viewer generation rather than skip one figure: ``1e309`` decodes to ``inf``
    and ``int(inf)`` raises, and a 400-digit integer decodes to an
    arbitrary-precision ``int`` whose later multiplication by a float rate raises
    ``OverflowError``. Anything past ``MAX_TOKEN_COUNT`` is malformed, not a bill,
    so it is dropped like any other unusable count.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    count = int(value)
    if count > MAX_TOKEN_COUNT:
        return 0
    return max(0, count)


# Where OpenAI-shaped usage reports the modality split. Realtime traces use the
# *_token_details spelling; the chat completions shape uses *_tokens_details.
_MODALITY_DETAIL_KEYS = (
    "prompt_tokens_details",
    "completion_tokens_details",
    "input_token_details",
    "output_token_details",
    "input_tokens_details",
    "output_tokens_details",
)


_GEMINI_MODALITY_DETAIL_KEYS = (
    "promptTokensDetails",
    "candidatesTokensDetails",
    "prompt_tokens_details",
    "candidates_tokens_details",
)


def _audio_tokens_from_modality_array(details: object) -> int:
    """Return AUDIO tokens from a Gemini-style modality-split array."""
    if not isinstance(details, list):
        return 0
    total = 0
    for item in details:
        if not isinstance(item, dict):
            continue
        modality = item.get("modality") or item.get("modalityType")
        if not isinstance(modality, str) or modality.upper() != "AUDIO":
            continue
        total += _int(item.get("tokenCount", item.get("token_count")))
    return total


def audio_tokens(usage: dict[str, Any] | None) -> int:
    """Return audio tokens reported in any modality detail bucket.

    Audio is billed at its own rate, several times the text one, and the vendored
    table carries no audio fields at all — the refresh script keeps only the text
    and cache rates. Normalization folds audio into the aggregate counts, so an
    audio turn priced from those aggregates would come out confidently low.
    """
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in _MODALITY_DETAIL_KEYS:
        details = usage.get(key)
        if isinstance(details, dict):
            total += _int(details.get("audio_tokens"))
        else:
            total += _audio_tokens_from_modality_array(details)
    for key in _GEMINI_MODALITY_DETAIL_KEYS:
        total += _audio_tokens_from_modality_array(usage.get(key))
    return total


def cache_write_buckets(usage: dict[str, Any] | None, *, cache_ttl_1h: bool = False) -> tuple[int, int]:
    """Split cache-creation tokens into (5-minute, 1-hour) buckets.

    Anthropic reports the split under ``usage.cache_creation`` when a request
    mixes breakpoint TTLs. Without it there is only one total, so ``cache_ttl_1h``
    decides which rate it is billed at.
    """
    if not isinstance(usage, dict):
        return (0, 0)
    total = _int(usage.get("cache_creation_input_tokens"))
    detail = usage.get("cache_creation")
    if isinstance(detail, dict):
        write_5m = _int(detail.get("ephemeral_5m_input_tokens"))
        write_1h = _int(detail.get("ephemeral_1h_input_tokens"))
        if write_5m or write_1h:
            # Trust the detailed split, but never bill more than the reported
            # total when the two disagree.
            if total and write_5m + write_1h != total:
                scale = total / (write_5m + write_1h)
                write_1h = round(write_1h * scale)
                write_5m = max(0, total - write_1h)
            return (write_5m, write_1h)
    return (0, total) if cache_ttl_1h else (total, 0)


def _search_cost_per_query(model: str, provider: str = "") -> float | None:
    """Return the per-query web-search rate, or None when the table is silent."""
    found = _lookup(model or "", provider)
    if found is None:
        return None
    value = found[1].get("search_context_cost_per_query")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def entry_cost(
    model: str,
    usage: dict[str, Any] | None,
    *,
    cache_ttl_1h: bool = False,
    provider: str = "",
    search_calls: int = 0,
) -> EntryCost | None:
    """Price one traced request, or return None when it cannot be priced.

    ``usage`` must already be normalized by :func:`claude_tap.usage.normalize_usage`,
    which records whether the cache-read count sits inside ``input_tokens`` via
    ``cache_read_in_input``. When it does, those tokens are subtracted from the
    uncached input before billing, so they are charged at the cache-read rate
    only.

    Returns None — rather than a partial figure — when a bucket carries tokens
    the price table has no rate for. ``gpt-4o`` and ``gemini-2.5-pro`` ship with
    no cache-creation cost, and billing those writes at zero would understate the
    turn while still presenting it as fully priced.

    ``saved`` is signed on purpose. A 5-minute cache write costs 1.25x uncached
    input, so a turn that only writes cache is genuinely more expensive than an
    uncached one; clamping that to zero would make every create-then-read session
    overstate its net savings.
    """
    if not isinstance(usage, dict):
        return None

    # Audio tokens sit inside the aggregate counts but bill at a rate the table
    # does not carry. Refusing the turn puts it in the "unpriced" count the
    # viewer already surfaces, rather than reporting an understated total as
    # complete.
    if audio_tokens(usage):
        return None

    search_count = _int(search_calls)
    search_rate = _search_cost_per_query(model, provider) if search_count else None
    if search_count and search_rate is None:
        # Built-in web search is billed per query. A token-only total would look
        # complete while omitting the only rate that covers that call.
        return None

    cache_read = _int(usage.get("cache_read_input_tokens"))
    write_5m, write_1h = cache_write_buckets(usage, cache_ttl_1h=cache_ttl_1h)
    cache_write = write_5m + write_1h
    reported_input = _int(usage.get("input_tokens"))
    output = _int(usage.get("output_tokens"))

    if usage.get("cache_read_in_input") is True:
        uncached_input = max(0, reported_input - cache_read)
        prompt_tokens = reported_input + cache_write
    else:
        uncached_input = reported_input
        prompt_tokens = reported_input + cache_read + cache_write

    if prompt_tokens == 0 and output == 0:
        return None

    rates = resolve_rates(model, prompt_tokens=prompt_tokens, cache_ttl_1h=cache_ttl_1h, provider=provider)
    if rates is None:
        return None

    # Every bucket that carries tokens needs a rate. A rate of None means the
    # table is silent, which is not the same as free.
    billed = (
        (uncached_input, rates.input),
        (cache_read, rates.cache_read),
        (write_5m, rates.cache_write_5m),
        (write_1h, rates.cache_write_1h),
        (output, rates.output),
    )
    if any(tokens and rate is None for tokens, rate in billed):
        return None

    cost = sum(tokens * (rate or 0.0) for tokens, rate in billed)
    if search_count and search_rate is not None:
        cost += search_count * search_rate
    # What the same turn would have cost with no cache at all: every prompt
    # token billed as fresh input. Search is independent of the cache.
    input_rate = rates.input or 0.0
    uncached_cost = (uncached_input + cache_read + cache_write) * input_rate + output * (rates.output or 0.0)
    if search_count and search_rate is not None:
        uncached_cost += search_count * search_rate
    return EntryCost(
        cost=cost,
        uncached_cost=uncached_cost,
        saved=uncached_cost - cost,
        model=rates.model,
        long_context=rates.long_context,
    )
