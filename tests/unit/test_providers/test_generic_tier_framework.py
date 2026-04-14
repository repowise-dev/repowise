"""Unit tests for BaseProvider.resolve_rate_limiter (generic tier framework).

These tests verify the tier resolution logic independent of any specific provider.
Any provider that defines RATE_LIMIT_TIERS gets this behavior for free via
BaseProvider.resolve_rate_limiter().
"""

from __future__ import annotations

import pytest

from repowise.core.providers.llm.base import BaseProvider
from repowise.core.rate_limiter import RateLimitConfig, RateLimiter


def test_resolve_rate_limiter_with_tier():
    """resolve_rate_limiter should create a limiter from tier config."""
    tiers = {
        "basic": RateLimitConfig(requests_per_minute=5, tokens_per_minute=10_000),
        "premium": RateLimitConfig(requests_per_minute=50, tokens_per_minute=100_000),
    }
    limiter = BaseProvider.resolve_rate_limiter(tier="premium", tiers=tiers)
    assert limiter is not None
    assert limiter.config.requests_per_minute == 50


def test_resolve_rate_limiter_tier_overrides_explicit():
    """Tier should take precedence over explicit rate_limiter."""
    tiers = {"pro": RateLimitConfig(requests_per_minute=30, tokens_per_minute=100_000)}
    explicit = RateLimiter(RateLimitConfig(requests_per_minute=999, tokens_per_minute=999_999))
    limiter = BaseProvider.resolve_rate_limiter(tier="pro", tiers=tiers, rate_limiter=explicit)
    assert limiter is not explicit
    assert limiter.config.requests_per_minute == 30


def test_resolve_rate_limiter_explicit_without_tier():
    """Without tier, explicit rate_limiter should be returned."""
    explicit = RateLimiter(RateLimitConfig(requests_per_minute=42, tokens_per_minute=420_000))
    limiter = BaseProvider.resolve_rate_limiter(rate_limiter=explicit)
    assert limiter is explicit


def test_resolve_rate_limiter_none_when_nothing_provided():
    """Should return None when neither tier nor rate_limiter is provided."""
    limiter = BaseProvider.resolve_rate_limiter()
    assert limiter is None


def test_resolve_rate_limiter_invalid_tier():
    """Invalid tier should raise ValueError."""
    tiers = {"basic": RateLimitConfig(requests_per_minute=5, tokens_per_minute=10_000)}
    with pytest.raises(ValueError, match="Unknown tier"):
        BaseProvider.resolve_rate_limiter(tier="enterprise", tiers=tiers)


def test_resolve_rate_limiter_tier_but_no_tiers_defined():
    """Tier with empty tiers dict should raise ValueError."""
    with pytest.raises(ValueError, match="defines no tiers"):
        BaseProvider.resolve_rate_limiter(tier="pro", tiers={})


def test_resolve_rate_limiter_case_insensitive():
    """Tier matching should be case-insensitive."""
    tiers = {"pro": RateLimitConfig(requests_per_minute=30, tokens_per_minute=100_000)}
    limiter = BaseProvider.resolve_rate_limiter(tier="PRO", tiers=tiers)
    assert limiter.config.requests_per_minute == 30


def test_base_provider_default_empty_tiers():
    """BaseProvider should have empty RATE_LIMIT_TIERS by default."""
    assert BaseProvider.RATE_LIMIT_TIERS == {}
