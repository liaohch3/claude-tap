from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from claude_tap import pricing
from claude_tap.pricing import LONG_CONTEXT_THRESHOLD, entry_cost, resolve_rates
from claude_tap.usage import normalize_usage

_REFRESH_SPEC = importlib.util.spec_from_file_location(
    "refresh_model_prices", Path(__file__).resolve().parents[1] / "scripts" / "refresh_model_prices.py"
)
assert _REFRESH_SPEC and _REFRESH_SPEC.loader
refresh = importlib.util.module_from_spec(_REFRESH_SPEC)
_REFRESH_SPEC.loader.exec_module(refresh)

SONNET_4 = "claude-sonnet-4-20250514"
OPUS_4_1 = "claude-opus-4-1"


@pytest.fixture(autouse=True)
def _clear_price_cache():
    pricing._price_table.cache_clear()
    yield
    pricing._price_table.cache_clear()


def test_price_table_ships_with_provenance() -> None:
    meta = pricing.pricing_metadata()

    assert meta["source_url"].startswith("https://raw.githubusercontent.com/BerriAI/litellm/")
    assert meta["as_of"]
    assert meta["model_count"] > 100


def test_corrupt_price_file_degrades_to_no_cost(tmp_path: Path, monkeypatch) -> None:
    broken = tmp_path / "model_prices.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(pricing, "PRICES_PATH", broken)
    pricing._price_table.cache_clear()

    assert resolve_rates(SONNET_4) is None
    assert entry_cost(SONNET_4, {"input_tokens": 100, "output_tokens": 10}) is None
    assert pricing.pricing_metadata()["model_count"] == 0


def test_missing_price_file_degrades_to_no_cost(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pricing, "PRICES_PATH", tmp_path / "absent.json")
    pricing._price_table.cache_clear()

    assert resolve_rates(SONNET_4) is None


def test_unpriced_model_returns_none() -> None:
    assert entry_cost("totally-made-up-model", {"input_tokens": 10, "output_tokens": 1}) is None


def test_empty_usage_returns_none() -> None:
    assert entry_cost(SONNET_4, None) is None
    assert entry_cost(SONNET_4, "not a dict") is None  # type: ignore[arg-type]
    assert entry_cost(SONNET_4, {}) is None
    assert entry_cost(SONNET_4, {"input_tokens": 0, "output_tokens": 0}) is None


def test_bedrock_and_gateway_prefixes_resolve_to_the_same_rates() -> None:
    base = resolve_rates(SONNET_4)
    assert base is not None

    for alias in (
        "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "bedrock/anthropic.claude-sonnet-4-20250514-v1:0",
        "openrouter/anthropic/claude-sonnet-4-20250514",
        "orcarouter/anthropic/claude-sonnet-4-20250514",
    ):
        rates = resolve_rates(alias)
        assert rates is not None, alias
        assert (rates.input, rates.output, rates.cache_read) == (
            base.input,
            base.output,
            base.cache_read,
        ), alias


def test_a_known_id_buried_inside_a_deployment_name_stays_unpriced() -> None:
    """A gateway names the model after its route, so the id starts a segment.

    A deployment called ``my-gpt-4-deployment`` may target any model on any plan.
    Resolving it to the bundled ``gpt-4`` entry reports a confident cost for a
    model that was never billed, which is worse than reporting no cost at all. A
    genuine alias for a known billed model is already handled by the
    response-model fallback in :func:`entry_cost`.
    """
    for name in ("my-gpt-4-deployment", "my-gpt-4", "pool/gpt-4omni", "some-gateway-deployment-alias"):
        assert resolve_rates(name) is None, name


def test_a_routed_prefix_with_a_variant_suffix_still_resolves() -> None:
    """The key may begin a route segment and carry a ``-`` variant suffix."""
    base = resolve_rates("claude-opus-5")
    assert base is not None

    routed = resolve_rates("my-pool/claude-opus-5-preview")
    assert routed is not None
    assert (routed.input, routed.output) == (base.input, base.output)


def test_long_context_tier_applies_above_200k() -> None:
    small = resolve_rates(SONNET_4, prompt_tokens=LONG_CONTEXT_THRESHOLD)
    large = resolve_rates(SONNET_4, prompt_tokens=LONG_CONTEXT_THRESHOLD + 1)
    assert small is not None and large is not None

    assert small.long_context is False
    assert large.long_context is True
    # Sonnet 4 doubles input and lifts output above the 200K boundary.
    assert large.input == pytest.approx(small.input * 2)
    assert large.output > small.output
    assert large.cache_read > small.cache_read
    assert large.cache_write > small.cache_write


def test_long_context_tier_is_not_invented_for_models_without_one() -> None:
    """Opus 4.1 caps at 200K input, so a huge prompt must not tier its rates up."""
    base = resolve_rates(OPUS_4_1)
    huge = resolve_rates(OPUS_4_1, prompt_tokens=900_000)
    assert base is not None and huge is not None

    assert huge.long_context is False
    assert huge.input == base.input
    assert huge.output == base.output


def test_long_context_cost_uses_tiered_rates() -> None:
    usage = {"input_tokens": 300_000, "output_tokens": 1_000}

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.long_context is True
    assert priced.cost == pytest.approx(300_000 * 6e-06 + 1_000 * 2.25e-05)


def test_cache_write_ttl_uses_the_one_hour_rate() -> None:
    usage = {"input_tokens": 100, "cache_creation_input_tokens": 10_000, "output_tokens": 5}

    default_ttl = entry_cost(SONNET_4, usage)
    one_hour = entry_cost(SONNET_4, usage, cache_ttl_1h=True)
    assert default_ttl is not None and one_hour is not None

    assert default_ttl.cost == pytest.approx(100 * 3e-06 + 10_000 * 3.75e-06 + 5 * 1.5e-05)
    assert one_hour.cost == pytest.approx(100 * 3e-06 + 10_000 * 6e-06 + 5 * 1.5e-05)
    assert one_hour.cost > default_ttl.cost


def test_one_hour_rate_is_ignored_when_the_model_has_no_such_tier() -> None:
    """gemini-2.5-pro has no *_above_1hr field; asking for 1h must not change rates."""
    rates = resolve_rates("gemini-2.5-pro", cache_ttl_1h=True)
    base = resolve_rates("gemini-2.5-pro")
    assert rates is not None and base is not None

    assert rates.cache_write == base.cache_write


def test_separate_cache_bucket_is_billed_on_top_of_input() -> None:
    """Anthropic reports cache reads outside input_tokens, so both are billed."""
    usage = normalize_usage(
        {
            "input_tokens": 1_000,
            "cache_read_input_tokens": 50_000,
            "output_tokens": 100,
        }
    )
    assert usage["cache_read_in_input"] is False

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.cost == pytest.approx(1_000 * 3e-06 + 50_000 * 3e-07 + 100 * 1.5e-05)
    assert priced.uncached_cost == pytest.approx(51_000 * 3e-06 + 100 * 1.5e-05)
    assert priced.saved == pytest.approx(priced.uncached_cost - priced.cost)


def test_bedrock_camel_case_cache_bucket_is_also_separate() -> None:
    usage = normalize_usage(
        {
            "inputTokens": 1_000,
            "cacheReadInputTokens": 50_000,
            "outputTokens": 100,
        }
    )
    assert usage["cache_read_in_input"] is False

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.cost == pytest.approx(1_000 * 3e-06 + 50_000 * 3e-07 + 100 * 1.5e-05)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            {"input_tokens": 51_000, "cached_tokens": 50_000, "output_tokens": 100},
            id="responses_cached_tokens",
        ),
        pytest.param(
            {
                "input_tokens": 51_000,
                "input_tokens_details": {"cached_tokens": 50_000},
                "output_tokens": 100,
            },
            id="input_tokens_details",
        ),
        pytest.param(
            {
                "prompt_tokens": 51_000,
                "prompt_tokens_details": {"cached_tokens": 50_000},
                "completion_tokens": 100,
            },
            id="prompt_tokens_details",
        ),
        pytest.param(
            {
                "promptTokenCount": 51_000,
                "cachedContentTokenCount": 50_000,
                "candidatesTokenCount": 100,
            },
            id="gemini_cached_content",
        ),
        # OpenAI Realtime spells the bucket singular. pricing.py already lists it
        # among the Realtime modality buckets, so a normalizer that skipped it
        # billed the whole prompt at the input rate and showed no cache read.
        pytest.param(
            {
                "input_tokens": 51_000,
                "input_token_details": {"cached_tokens": 50_000},
                "output_tokens": 100,
            },
            id="realtime_input_token_details",
        ),
    ],
)
def test_embedded_cache_tokens_are_not_billed_twice(raw: dict[str, object]) -> None:
    """OpenAI- and Gemini-shaped usage counts cached tokens inside the prompt total.

    Billing the reported input as-is would charge those 50K tokens at the full
    input rate and again at the cache-read rate.
    """
    usage = normalize_usage(raw)
    assert usage["cache_read_in_input"] is True
    assert usage["cache_read_input_tokens"] == 50_000

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    # 1K fresh input, 50K read from cache — not 51K fresh plus 50K cached.
    assert priced.cost == pytest.approx(1_000 * 3e-06 + 50_000 * 3e-07 + 100 * 1.5e-05)
    assert priced.uncached_cost == pytest.approx(51_000 * 3e-06 + 100 * 1.5e-05)


def test_embedded_cache_read_larger_than_input_clamps_to_zero_fresh_input() -> None:
    usage = {"input_tokens": 100, "cache_read_input_tokens": 500, "cache_read_in_input": True}

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.cost == pytest.approx(500 * 3e-07)


def test_embedded_cache_read_counts_toward_the_long_context_tier() -> None:
    """The tier follows prompt size, not the share billed at the uncached rate."""
    usage = {
        "input_tokens": 300_000,
        "cache_read_input_tokens": 299_000,
        "cache_read_in_input": True,
    }

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.long_context is True
    assert priced.cost == pytest.approx(1_000 * 6e-06 + 299_000 * 6e-07)


def test_saved_stays_signed_for_a_write_only_turn() -> None:
    """A 5-minute cache write costs 1.25x uncached input, so writing loses money."""
    usage = {"input_tokens": 0, "cache_creation_input_tokens": 100_000, "output_tokens": 10}

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.cost > priced.uncached_cost
    assert priced.saved < 0


def test_negative_and_non_numeric_token_counts_are_ignored() -> None:
    usage = {
        "input_tokens": -5,
        "cache_read_input_tokens": "many",
        "cache_creation_input_tokens": True,
        "output_tokens": 10,
    }

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    assert priced.cost == pytest.approx(10 * 1.5e-05)


def test_model_from_path_matches_gemini_style_urls() -> None:
    assert pricing.model_from_path("/v1beta/models/gemini-2.5-pro:generateContent") == "gemini-2.5-pro"
    assert pricing.model_from_path("/v1/messages") == ""
    assert pricing.model_from_path(None) == ""  # type: ignore[arg-type]


def test_vendored_table_only_carries_the_fields_the_adapter_reads() -> None:
    """The shipped table must match what the refresh script keeps.

    The allowlist is read from the script rather than restated here: a hand-copied
    list means the next field the script starts keeping fails this test on the
    refresh commit instead of on the commit that added it.
    """
    payload = json.loads(pricing.PRICES_PATH.read_text(encoding="utf-8"))

    extra = {field for entry in payload["models"].values() for field in entry} - set(refresh.KEPT_FIELDS)
    assert extra == set()


def test_provenance_pins_the_exact_upstream_snapshot() -> None:
    meta = pricing.pricing_metadata()

    # A quoted figure is only reproducible if the snapshot names the commit it
    # came from: the upstream file changes several times a day.
    commit = meta["upstream_commit"]
    assert re.fullmatch(r"[0-9a-f]{7,40}", commit)
    assert commit in meta["source_url"]


def test_a_model_without_cache_write_pricing_is_left_unpriced() -> None:
    # gpt-4o carries no cache-creation cost, so a turn that writes cache cannot
    # be priced; reporting the write as free would be confidently wrong.
    rates = resolve_rates("gpt-4o")
    assert rates is not None
    assert rates.cache_write is None

    writing = {"input_tokens": 1_000, "cache_creation_input_tokens": 5_000, "output_tokens": 10}
    assert entry_cost("gpt-4o", writing) is None

    priced = entry_cost("gpt-4o", {"input_tokens": 1_000, "output_tokens": 10})
    assert priced is not None
    assert priced.cost == pytest.approx(1_000 * 2.5e-06 + 10 * 1e-05)


def test_mixed_ttl_cache_writes_are_billed_per_bucket() -> None:
    usage = {
        "input_tokens": 100,
        "cache_creation_input_tokens": 10_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 4_000, "ephemeral_1h_input_tokens": 6_000},
        "output_tokens": 5,
    }

    assert pricing.cache_write_buckets(usage) == (4_000, 6_000)

    priced = entry_cost(SONNET_4, usage)
    assert priced is not None
    expected = 100 * 3e-06 + 4_000 * 3.75e-06 + 6_000 * 6e-06 + 5 * 1.5e-05
    assert priced.cost == pytest.approx(expected)

    # The per-bucket figure sits between billing the whole write at either rate.
    flat = dict(usage)
    flat.pop("cache_creation")
    all_5m = entry_cost(SONNET_4, flat)
    all_1h = entry_cost(SONNET_4, flat, cache_ttl_1h=True)
    assert all_5m is not None and all_1h is not None
    assert all_5m.cost < priced.cost < all_1h.cost


def test_the_reported_ttl_split_wins_over_the_breakpoint_flag() -> None:
    usage = {
        "cache_creation_input_tokens": 10_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 4_000, "ephemeral_1h_input_tokens": 6_000},
    }

    # A request can mix breakpoint TTLs, so the flag derived from the request is
    # only a fallback for when the response does not break the write down.
    assert pricing.cache_write_buckets(usage, cache_ttl_1h=True) == (4_000, 6_000)
    assert pricing.cache_write_buckets({"cache_creation_input_tokens": 900}, cache_ttl_1h=True) == (0, 900)
    assert pricing.cache_write_buckets({"cache_creation_input_tokens": 900}) == (900, 0)


def test_an_inconsistent_ttl_split_is_rescaled_to_the_reported_total() -> None:
    usage = {
        "cache_creation_input_tokens": 10_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 40, "ephemeral_1h_input_tokens": 60},
    }

    assert pricing.cache_write_buckets(usage) == (4_000, 6_000)


def test_non_finite_token_counts_do_not_raise() -> None:
    # A trace can carry 1e309, which JSON decodes to inf; int(inf) raises.
    priced = entry_cost(SONNET_4, {"input_tokens": float("inf"), "output_tokens": 5})
    assert priced is not None
    assert priced.cost == pytest.approx(5 * 1.5e-05)
    assert pricing.cache_write_buckets({"cache_creation_input_tokens": float("nan")}) == (0, 0)


def test_bedrock_paths_keep_the_version_suffix_and_regional_prefix() -> None:
    # A Bedrock id ends in a version that belongs to the id, while a Vertex path
    # appends the method after the colon. Truncating the Bedrock form at the colon
    # drops to the bare model, which bills 3e-06 instead of the regional 3.3e-06.
    versioned = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"
    assert pricing.model_from_path(f"/model/{versioned}/converse") == versioned
    assert pricing.model_from_path("/model/jp.anthropic.claude-sonnet-4-5-20250929-v1%3A0/invoke") == versioned

    regional = resolve_rates(versioned)
    bare = resolve_rates("claude-sonnet-4-5-20250929")
    assert regional is not None and bare is not None
    assert regional.input == pytest.approx(3.3e-06)
    assert bare.input == pytest.approx(3e-06)


def test_one_hour_premium_survives_the_long_context_tier() -> None:
    # LiteLLM has no combined *_above_1hr_above_200k_tokens field, so the two
    # adjustments have to be composed. Taking the tiered 5-minute rate alone would
    # silently drop the TTL premium above 200K.
    base = resolve_rates(SONNET_4)
    large = resolve_rates(SONNET_4, prompt_tokens=LONG_CONTEXT_THRESHOLD + 1)
    assert base is not None and large is not None

    assert (base.cache_write_5m, base.cache_write_1h) == (pytest.approx(3.75e-06), pytest.approx(6e-06))
    assert large.cache_write_5m == pytest.approx(7.5e-06)
    assert large.cache_write_1h == pytest.approx(1.2e-05)

    usage = {"input_tokens": 300_000, "cache_creation_input_tokens": 10_000, "output_tokens": 5}
    one_hour = entry_cost(SONNET_4, usage, cache_ttl_1h=True)
    five_minute = entry_cost(SONNET_4, usage)
    assert one_hour is not None and five_minute is not None
    assert one_hour.cost - five_minute.cost == pytest.approx(10_000 * (1.2e-05 - 7.5e-06))


def test_arbitrary_precision_token_counts_do_not_raise() -> None:
    # A 400-digit JSON integer decodes to an arbitrary-precision int, whose
    # multiplication by a float rate raises OverflowError. It is a malformed
    # capture, not a bill, so it is dropped like any other unusable count.
    assert pricing._int(10**400) == 0
    assert pricing.cache_write_buckets({"cache_creation_input_tokens": 10**400}) == (0, 0)

    priced = entry_cost(SONNET_4, {"input_tokens": 10**400, "output_tokens": 5})
    assert priced is not None
    assert priced.cost == pytest.approx(5 * 1.5e-05)


@pytest.mark.parametrize(
    "key",
    [
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_token_details",
        "output_token_details",
        "input_tokens_details",
        "output_tokens_details",
    ],
)
def test_audio_turns_are_left_unpriced(key: str) -> None:
    # The vendored table carries no audio fields at all, and normalization folds
    # audio into the aggregate counts, so pricing from those aggregates would come
    # out confidently low. Refusing puts the turn in the "unpriced" count instead.
    usage = {"input_tokens": 1_000, "output_tokens": 10, key: {"audio_tokens": 12}}

    assert pricing.audio_tokens(usage) == 12
    assert entry_cost("gpt-4o-realtime-preview", usage) is None
    assert entry_cost(SONNET_4, usage) is None

    text_only = {"input_tokens": 1_000, "output_tokens": 10, key: {"audio_tokens": 0}}
    assert pricing.audio_tokens(text_only) == 0
    assert entry_cost(SONNET_4, text_only) is not None


def test_audio_tokens_ignores_shapes_it_cannot_read() -> None:
    assert pricing.audio_tokens(None) == 0
    assert pricing.audio_tokens("nope") == 0  # type: ignore[arg-type]
    assert pricing.audio_tokens({"prompt_tokens_details": "nope"}) == 0
    assert pricing.audio_tokens({"prompt_tokens_details": {"cached_tokens": 5}}) == 0


def test_audio_tokens_reads_gemini_modality_arrays() -> None:
    usage = {
        "input_tokens": 1_000,
        "output_tokens": 10,
        "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 40}, {"modality": "TEXT", "tokenCount": 960}],
        "candidatesTokensDetails": [{"modality": "TEXT", "tokenCount": 10}],
    }
    assert pricing.audio_tokens(usage) == 40
    assert entry_cost("gemini-2.5-flash", usage) is None


def test_azure_host_uses_the_azure_namespace() -> None:
    assert pricing.provider_namespace("https://my-resource.openai.azure.com/openai/deployments/gpt") == "azure"
    azure = resolve_rates("gpt-4o-2024-11-20", provider="azure")
    openai = resolve_rates("gpt-4o-2024-11-20")
    assert azure is not None and openai is not None
    assert azure.model == "azure/gpt-4o-2024-11-20"
    assert azure.input == pytest.approx(2.75e-06)
    assert openai.input == pytest.approx(2.5e-06)
    # A declared Azure namespace must not fall through to the cheaper OpenAI key.
    assert pricing.is_priced_model("gpt-4o-2024-11-20", provider="azure") is True


def test_a_namespace_without_its_own_key_still_prices_from_the_bare_id(tmp_path: Path, monkeypatch) -> None:
    """A namespace only refuses the bare rate when it prices the model differently.

    With no ``azure/`` key in the table the bare figure is the only price known
    for this model, so dropping it would erase real Azure traffic from the cost
    totals rather than protect it from a wrong rate.
    """
    table = {
        "__meta__": {},
        "models": {
            "gpt-only-openai": {
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 2e-06,
            }
        },
    }
    path = tmp_path / "model_prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(pricing, "PRICES_PATH", path)
    pricing._price_table.cache_clear()

    assert resolve_rates("gpt-only-openai") is not None
    azure = resolve_rates("gpt-only-openai", provider="azure")
    assert azure is not None and azure.input == pytest.approx(1e-06)
    assert pricing.is_priced_model("gpt-only-openai", provider="azure") is True


def test_a_namespace_refuses_the_bare_rate_only_where_the_rates_differ(tmp_path: Path, monkeypatch) -> None:
    """The gate is the rate itself, not a hand-kept list of provider names.

    ``azure/`` diverges, so its traffic must not reach the cheaper bare key.
    ``mirror/`` repeats the bare figures, so the bare fallback is what prices it
    and must survive -- that is how every Vertex Claude id is priced today. A
    list of strict namespaces got this right for two providers and silently
    mispriced the other four the table carries.
    """
    table = {
        "__meta__": {},
        "models": {
            "shared-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
            "azure/shared-model": {"input_cost_per_token": 5e-06, "output_cost_per_token": 9e-06},
            "mirror/shared-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
        },
    }
    path = tmp_path / "model_prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(pricing, "PRICES_PATH", path)
    pricing._price_table.cache_clear()

    diverging = resolve_rates("shared-model", provider="azure")
    assert diverging is not None
    assert diverging.model == "azure/shared-model"
    assert diverging.input == pytest.approx(5e-06)

    mirrored = resolve_rates("shared-model", provider="mirror")
    assert mirrored is not None
    assert mirrored.input == pytest.approx(1e-06)

    # A route prefix hides the provider's key from the candidate list, so the
    # segment fallback is the other way the cheaper bare rate gets reached.
    routed = resolve_rates("my-pool/shared-model", provider="azure")
    assert routed is not None
    assert routed.model == "azure/shared-model"
    assert routed.input == pytest.approx(5e-06)

    # An unknown deployment name still resolves to nothing rather than guessing.
    assert resolve_rates("my-shared-model-deployment", provider="azure") is None


def test_every_diverging_namespace_key_holds_its_own_rate() -> None:
    """Guard the whole bundled table, not the four providers review happened to name.

    Each namespaced key whose rates differ from the bare id is a distinct SKU;
    resolving it to the bare entry bills another product's price. Each key that
    only mirrors the bare rates must still resolve, or real traffic goes
    unpriced. Both directions are checked against the shipped table so a price
    refresh that adds a diverging provider fails here instead of in a bill.
    """
    pricing._price_table.cache_clear()
    models = pricing._price_table()["models"]
    bare = {k.lower(): v for k, v in models.items() if "/" not in k and isinstance(v, dict)}

    diverging: list[str] = []
    mirrored: list[str] = []
    for key, entry in models.items():
        if "/" not in key or not isinstance(entry, dict):
            continue
        namespace, _, tail = key.partition("/")
        peer = bare.get(tail.lower())
        if not isinstance(peer, dict):
            continue
        target = diverging if pricing._rate_fields(entry) != pricing._rate_fields(peer) else mirrored
        target.append(key)

    assert diverging, "the bundled table must still carry diverging namespaced keys"
    assert mirrored, "the bundled table must still carry mirrored namespaced keys"

    for key in diverging:
        namespace, _, tail = key.partition("/")
        found = pricing._lookup(tail, namespace)
        assert found is not None, f"{key} left {tail} unpriced under {namespace}"
        assert found[0] == key, f"{tail} under {namespace} resolved to {found[0]}, not {key}"

    for key in mirrored:
        namespace, _, tail = key.partition("/")
        assert pricing._lookup(tail, namespace) is not None, f"{key} lost the bare fallback"


def test_web_search_calls_are_left_unpriced_without_a_search_rate() -> None:
    usage = {"input_tokens": 100, "output_tokens": 10}
    assert entry_cost("gpt-4o-2024-11-20", usage) is not None
    assert entry_cost("gpt-4o-2024-11-20", usage, search_calls=1) is None


def test_web_search_charge_is_applied_when_the_table_has_a_rate(tmp_path: Path, monkeypatch) -> None:
    table = {
        "__meta__": {},
        "models": {
            "gpt-4o-2024-11-20": {
                "input_cost_per_token": 2.5e-06,
                "output_cost_per_token": 1e-05,
                "search_context_cost_per_query": 0.035,
            }
        },
    }
    path = tmp_path / "model_prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(pricing, "PRICES_PATH", path)
    pricing._price_table.cache_clear()

    priced = entry_cost("gpt-4o-2024-11-20", {"input_tokens": 100, "output_tokens": 10}, search_calls=2)
    assert priced is not None
    assert priced.cost == pytest.approx(100 * 2.5e-06 + 10 * 1e-05 + 0.07)
    assert priced.uncached_cost == pytest.approx(priced.cost)


def test_is_priced_model_reports_what_the_table_can_resolve() -> None:
    assert pricing.is_priced_model(SONNET_4) is True
    assert pricing.is_priced_model("us.anthropic.claude-sonnet-4-20250514-v1:0") is True
    assert pricing.is_priced_model("some-gateway-deployment-alias") is False
    assert pricing.is_priced_model("") is False
    assert pricing.is_priced_model("   ") is False
    assert pricing.is_priced_model(None) is False  # type: ignore[arg-type]


def test_gemini_thinking_tokens_are_billed_as_output() -> None:
    # thoughtsTokenCount is excluded from candidatesTokenCount but billed at the
    # output rate, so counting only the visible answer undercharges the turn.
    usage = normalize_usage({"promptTokenCount": 1_000, "candidatesTokenCount": 200, "thoughtsTokenCount": 800})

    assert usage["output_tokens"] == 1_000

    priced = entry_cost("gemini-2.5-pro", usage)
    assert priced is not None
    assert priced.cost == pytest.approx(1_000 * 1.25e-06 + 1_000 * 1e-05)


def test_provider_namespace_reads_the_captured_host() -> None:
    assert pricing.provider_namespace("https://openrouter.ai/api/v1") == "openrouter"
    assert pricing.provider_namespace("https://api.orcarouter.ai/v1") == "orcarouter"
    assert pricing.provider_namespace("generativelanguage.googleapis.com") == "gemini"
    assert pricing.provider_namespace("https://us-central1-aiplatform.googleapis.com") == "vertex_ai"
    assert pricing.provider_namespace("https://api.anthropic.com") == ""
    assert pricing.provider_namespace("https://acct.openai.azure.com") == "azure"
    assert pricing.provider_namespace(None) == ""
    assert (
        pricing.provider_namespace(
            {
                "upstream_base_url": "https://openrouter.ai/api/v1",
                "request": {"headers": {"host": "openrouter.ai"}, "path": "/api/v1/chat/completions"},
            }
        )
        == "openrouter"
    )


def test_every_supported_route_host_resolves_to_a_priced_namespace() -> None:
    """A host missing from the map costs the whole capture, not a few percent.

    These namespaces are the only spelling their ids have in the table: all 22
    ``moonshot/`` keys and all 44 ``xai/`` ones lack a bare twin, so a Kimi or
    Grok capture whose host does not map resolves to nothing and vanishes from
    the cost totals. Each route below is one the support matrix lists as
    reachable, checked end to end from captured host to a real rate.
    """
    routes = [
        ("https://api.moonshot.ai/v1", "moonshot", "kimi-k2-turbo-preview"),
        ("https://api.kimi.com/coding/v1", "moonshot", "kimi-k2-thinking"),
        ("https://cli-chat-proxy.grok.com/v1", "xai", "grok-4"),
        ("https://api.x.ai/v1", "xai", "grok-code-fast-1"),
        ("https://api.deepseek.com", "deepseek", "deepseek-v3.2"),
    ]
    for host, namespace, model in routes:
        assert pricing.provider_namespace(host) == namespace, host
        found = pricing._lookup(model, namespace)
        assert found is not None, f"{model} unpriced on {host}"
        assert found[0] == f"{namespace}/{model}", f"{model} on {host} resolved to {found[0]}"
        rates = resolve_rates(model, provider=namespace)
        assert rates is not None and rates.input and rates.input > 0

    # Anthropic and OpenAI keep their rates on the bare id, so no prefix is
    # right for them; mapping their hosts to a namespace would unprice them.
    assert pricing.provider_namespace("https://api.anthropic.com") == ""
    assert resolve_rates("claude-sonnet-4-5") is not None


def test_openrouter_namespace_beats_the_direct_provider_entry() -> None:
    # The request model is the shared DeepSeek id; without the captured
    # OpenRouter host, lookup would bill DeepSeek's own 2.8e-7 input rate.
    direct = resolve_rates("deepseek/deepseek-chat")
    routed = resolve_rates("deepseek/deepseek-chat", provider="openrouter")
    assert direct is not None and routed is not None

    assert direct.input == pytest.approx(2.8e-07)
    assert routed.input == pytest.approx(1.4e-07)
    assert routed.model == "openrouter/deepseek/deepseek-chat"
    assert pricing.is_priced_model("deepseek/deepseek-chat", provider="openrouter") is True

    priced = entry_cost("deepseek/deepseek-chat", {"input_tokens": 100}, provider="openrouter")
    assert priced is not None
    assert priced.cost == pytest.approx(100 * 1.4e-07)
    assert priced.model == "openrouter/deepseek/deepseek-chat"


def test_orcarouter_namespace_falls_through_to_the_underlying_model_rate() -> None:
    # OrcaRouter names its own zero-markup routes under ``orcarouter/`` but the
    # vendored table carries no ``orcarouter/`` keys; the namespace must still be
    # recognized so the ``/v1`` base URL resolves like OpenRouter, and pricing
    # falls through to the concrete underlying vendor model.
    assert pricing.provider_namespace("https://api.orcarouter.ai/v1") == "orcarouter"

    # A route-qualified id resolves to the vendor model, not an orcarouter key.
    routed = resolve_rates("orcarouter/deepseek/deepseek-chat", provider="orcarouter")
    assert routed is not None
    assert routed.model == "deepseek/deepseek-chat"

    # The bare id under the OrcaRouter namespace is priced at the vendor rate.
    bare = resolve_rates("deepseek/deepseek-chat")
    via_orca = resolve_rates("deepseek/deepseek-chat", provider="orcarouter")
    assert bare is not None and via_orca is not None
    assert via_orca.input == bare.input == pytest.approx(2.8e-07)
    assert pricing.is_priced_model("deepseek/deepseek-chat", provider="orcarouter") is True


def test_gemini_namespace_beats_the_bare_vertex_entry() -> None:
    # gemini-2.0-flash-001 is 1.5e-7 on the bare/Vertex entry and 1e-7 on the
    # Gemini Developer API entry. Billing the wrong one is a silent 50% error.
    bare = resolve_rates("gemini-2.0-flash-001")
    gemini = resolve_rates("gemini-2.0-flash-001", provider="gemini")
    vertex = resolve_rates("gemini-2.0-flash-001", provider="vertex_ai")
    assert bare is not None and gemini is not None and vertex is not None

    assert bare.input == pytest.approx(1.5e-07)
    assert gemini.input == pytest.approx(1e-07)
    assert gemini.model == "gemini/gemini-2.0-flash-001"
    assert vertex.input == pytest.approx(1.5e-07)
    assert vertex.model == "gemini-2.0-flash-001"


def test_already_qualified_model_is_not_prefixed_again() -> None:
    rates = resolve_rates("openrouter/deepseek/deepseek-chat", provider="openrouter")
    assert rates is not None
    assert rates.model == "openrouter/deepseek/deepseek-chat"
    assert rates.input == pytest.approx(1.4e-07)
