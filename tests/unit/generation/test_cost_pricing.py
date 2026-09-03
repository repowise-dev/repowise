"""Pricing-table lookups for the cost tracker.

Regression guard for the nano tier: an unlisted model silently falls back to
the Sonnet-tier default rate, which overstates a cheap indexing run by ~40x.
"""

from __future__ import annotations

from repowise.core.generation.cost_tracker import (
    _FALLBACK_PRICING,
    _family_pricing,
    get_model_pricing,
)


def test_nano_models_priced_at_nano_rate_not_fallback() -> None:
    # Each generation is priced from its own published rate; what matters is
    # that neither lands on the Sonnet-tier fallback.
    for model, expected in (
        ("gpt-5-nano", {"input": 0.05, "output": 0.40}),
        ("gpt-5.4-nano", {"input": 0.20, "output": 1.25}),
    ):
        pricing = get_model_pricing(model)
        assert pricing == expected
        assert pricing != _FALLBACK_PRICING


def test_luna_priced_from_its_own_row_not_the_gpt5_flagship_tier() -> None:
    """The default model's failure mode is over-pricing, not the fallback.

    ``gpt-5.6-luna`` carries no "nano"/"mini" qualifier, so with no exact row
    ``_family_pricing``'s gpt-5 branch prices it at the flagship $2.50/$15,
    12.5x over on output, and nowhere near ``_FALLBACK_PRICING``, so the usual
    "not the fallback" assertion would pass while the number was wrong. Assert
    the exact published rate instead.
    """
    assert get_model_pricing("gpt-5.6-luna") == {"input": 0.20, "output": 1.20}


def test_unknown_model_still_falls_back() -> None:
    assert get_model_pricing("totally-made-up-model-9000") == _FALLBACK_PRICING


def test_subscription_cli_models_are_zero_cost() -> None:
    """A Claude subscription is not metered API spend in the runtime ledger."""
    for model in (
        "codex_cli/gpt-5.5",
        "claude_cli/claude-haiku-4-5",
        "opencode/anthropic/claude-sonnet-4-6",
    ):
        assert get_model_pricing(model) == {"input": 0.0, "output": 0.0}


def test_dated_opus_variant_prices_at_opus_tier_not_sonnet_fallback() -> None:
    # An Opus session id the exact table misses (dated / future point release)
    # must resolve to the Opus tier, not the Sonnet-priced fallback — otherwise
    # an Opus user's savings are undercounted ~5x.
    for model in ("claude-opus-4-8-20260514", "claude-opus-4-9", "claude-opus-5"):
        pricing = get_model_pricing(model)
        assert pricing == {"input": 5.0, "output": 25.0}
        assert pricing != _FALLBACK_PRICING


def test_family_prefix_covers_sonnet_and_haiku_variants() -> None:
    # Sonnet's tier is byte-identical to _FALLBACK_PRICING, so assert the family
    # matcher actually resolved it rather than the value alone.
    assert _family_pricing("claude-sonnet-4-9") == {"input": 3.0, "output": 15.0}
    assert _family_pricing("totally-made-up-model-9000") is None
    assert get_model_pricing("claude-haiku-5") == {"input": 1.0, "output": 5.0}


def test_gpt5_variants_resolve_by_tier_qualifier() -> None:
    assert get_model_pricing("gpt-5.5-nano") == {"input": 0.20, "output": 1.25}
    assert get_model_pricing("gpt-5.5-mini") == {"input": 0.75, "output": 4.50}
    # A plain future gpt-5 variant gets the base GPT-5 tier, not the fallback.
    assert get_model_pricing("gpt-5.5") == {"input": 2.50, "output": 15.0}
