"""Token usage normalization helpers."""

from __future__ import annotations


def normalize_usage(usage: object) -> dict:
    """Return usage with provider-specific token fields mapped to shared names."""
    if not isinstance(usage, dict):
        return {}

    normalized = dict(usage)
    if "input_tokens" not in normalized and "prompt_tokens" in usage:
        normalized["input_tokens"] = usage["prompt_tokens"]
    if "input_tokens" not in normalized and "promptTokenCount" in usage:
        normalized["input_tokens"] = usage["promptTokenCount"]
    if "output_tokens" not in normalized and "completion_tokens" in usage:
        normalized["output_tokens"] = usage["completion_tokens"]
    if "output_tokens" not in normalized and "candidatesTokenCount" in usage:
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
