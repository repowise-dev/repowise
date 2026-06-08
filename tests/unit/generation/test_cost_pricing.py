"""Pricing-table lookups for the cost tracker.

Regression guard for the nano tier: an unlisted model silently falls back to
the Sonnet-tier default rate, which overstates a cheap indexing run by ~40x.
"""

from __future__ import annotations

from repowise.core.generation.cost_tracker import (
    _FALLBACK_PRICING,
    get_model_pricing,
)


def test_nano_models_priced_at_nano_rate_not_fallback() -> None:
    for model in ("gpt-5-nano", "gpt-5.4-nano"):
        pricing = get_model_pricing(model)
        assert pricing == {"input": 0.05, "output": 0.40}
        assert pricing != _FALLBACK_PRICING


def test_unknown_model_still_falls_back() -> None:
    assert get_model_pricing("totally-made-up-model-9000") == _FALLBACK_PRICING
