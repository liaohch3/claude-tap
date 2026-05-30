"""Token usage normalization helpers."""

from __future__ import annotations


def _missing_or_zero(value: object) -> bool:
    return value is None or value == 0


def normalize_usage(usage: object) -> dict:
    """Return usage with provider-specific token fields mapped to shared names."""
    if not isinstance(usage, dict):
        return {}

    normalized = dict(usage)

    input_tokens = normalized.get("input_tokens")
    output_tokens = normalized.get("output_tokens")
    if _missing_or_zero(input_tokens) and usage.get("prompt_tokens"):
        normalized["input_tokens"] = usage["prompt_tokens"]
    if _missing_or_zero(normalized.get("input_tokens")) and usage.get("promptTokenCount"):
        normalized["input_tokens"] = usage["promptTokenCount"]
    if _missing_or_zero(output_tokens) and usage.get("completion_tokens"):
        normalized["output_tokens"] = usage["completion_tokens"]
    if _missing_or_zero(normalized.get("output_tokens")) and usage.get("candidatesTokenCount"):
        normalized["output_tokens"] = usage["candidatesTokenCount"]

    if "cache_read_input_tokens" not in normalized:
        cached = usage.get("cached_tokens")
        if cached is None:
            cached = usage.get("cachedContentTokenCount")
        if cached is None:
            for details_key in ("input_tokens_details", "prompt_tokens_details"):
                details = usage.get(details_key)
                if isinstance(details, dict):
                    cached = details.get("cached_tokens")
                    if cached is not None:
                        break
        if cached is not None:
            normalized["cache_read_input_tokens"] = cached

    return normalized
