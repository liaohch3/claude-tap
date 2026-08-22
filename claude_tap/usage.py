"""Token usage normalization helpers."""

from __future__ import annotations

import math


def _missing_or_zero(value: object) -> bool:
    return value is None or value == 0


def _as_count(value: object) -> int:
    """Return a token count as an int, treating anything unusable as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float) and not math.isfinite(value):
        return 0
    return max(0, int(value))


def normalize_usage(usage: object) -> dict:
    """Return usage with provider-specific token fields mapped to shared names."""
    if not isinstance(usage, dict):
        return {}

    normalized = {k: v for k, v in usage.items() if v is not None}

    input_tokens = normalized.get("input_tokens")
    output_tokens = normalized.get("output_tokens")
    if _missing_or_zero(input_tokens) and usage.get("prompt_tokens"):
        normalized["input_tokens"] = usage["prompt_tokens"]
    if _missing_or_zero(normalized.get("input_tokens")) and usage.get("promptTokenCount"):
        normalized["input_tokens"] = usage["promptTokenCount"]
    if _missing_or_zero(normalized.get("input_tokens")) and usage.get("inputTokens"):
        normalized["input_tokens"] = usage["inputTokens"]
    if _missing_or_zero(output_tokens) and usage.get("completion_tokens"):
        normalized["output_tokens"] = usage["completion_tokens"]
    if _missing_or_zero(normalized.get("output_tokens")) and (
        usage.get("candidatesTokenCount") or usage.get("thoughtsTokenCount")
    ):
        # Gemini reports reasoning tokens in a separate thoughtsTokenCount and
        # excludes them from candidatesTokenCount, but bills both at the output
        # rate. Counting only the visible answer undercharges thinking turns.
        normalized["output_tokens"] = _as_count(usage.get("candidatesTokenCount")) + _as_count(
            usage.get("thoughtsTokenCount")
        )
    if _missing_or_zero(normalized.get("output_tokens")) and usage.get("outputTokens"):
        normalized["output_tokens"] = usage["outputTokens"]
    if _missing_or_zero(normalized.get("total_tokens")) and usage.get("totalTokens"):
        normalized["total_tokens"] = usage["totalTokens"]
    if _missing_or_zero(normalized.get("total_tokens")) and usage.get("totalTokenCount"):
        normalized["total_tokens"] = usage["totalTokenCount"]

    if "cache_read_input_tokens" not in normalized:
        # OpenAI- and Gemini-shaped usage counts cached tokens *inside* the
        # prompt total, so cost and cache-hit-rate math must subtract them
        # before billing the rest as fresh input. Bedrock's camelCase field is
        # a separate bucket like Anthropic's. Record which shape was seen —
        # without it, cached tokens get billed at the input rate and again at
        # the cache-read rate.
        embedded = True
        cached = usage.get("cached_tokens")
        if cached is None:
            cached = usage.get("cachedContentTokenCount")
        if cached is None:
            cached = usage.get("cacheReadInputTokens")
            if cached is not None:
                embedded = False
        if cached is None:
            # "input_token_details" is singular on OpenAI Realtime responses, and
            # pricing.py already lists it among the Realtime modality buckets. Left
            # out here, a Realtime cache hit produced no cache_read_input_tokens at
            # all, so entry_cost() billed the whole prompt at the full input rate and
            # the viewer showed no cache read.
            for details_key in ("input_tokens_details", "input_token_details", "prompt_tokens_details"):
                details = usage.get(details_key)
                if isinstance(details, dict):
                    cached = details.get("cached_tokens")
                    if cached is not None:
                        break
        if cached is None:
            # Direct DeepSeek names its two prompt buckets outright rather than
            # nesting them under a details object. Both are counted inside
            # prompt_tokens, so this is an embedded bucket: a 900-of-1000 hit
            # billed as fresh input costs an order of magnitude more than the
            # cache-read rate, and the viewer reported no hit at all.
            cached = usage.get("prompt_cache_hit_tokens")
        if cached is not None:
            normalized["cache_read_input_tokens"] = cached
            normalized["cache_read_in_input"] = embedded
    elif "cache_read_in_input" not in normalized:
        # Native cache_read_input_tokens (Anthropic) is billed separately from
        # input_tokens.
        normalized["cache_read_in_input"] = False

    if "cache_creation_input_tokens" not in normalized:
        cache_write = usage.get("cacheWriteInputTokens")
        if cache_write is not None:
            normalized["cache_creation_input_tokens"] = cache_write

    return normalized


def usage_total_tokens(usage: dict) -> int:
    """Return the provider-reported total, or a compatible derived fallback."""
    reported = usage.get("total_tokens")
    if isinstance(reported, int) and not isinstance(reported, bool) and reported >= 0:
        return reported

    return sum(
        int(usage.get(field) or 0)
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
