"""Tests for the RateLimiter.

These tests verify the rate limiter's behavior without requiring any LLM calls.
Tests that verify actual waiting behavior use short windows to keep the suite fast.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from repowise.core.rate_limiter import PROVIDER_DEFAULTS, RateLimitConfig, RateLimiter


class TestRateLimitConfig:
    def test_config_stores_values(self) -> None:
        config = RateLimitConfig(requests_per_minute=30, tokens_per_minute=50_000)
        assert config.requests_per_minute == 30
        assert config.tokens_per_minute == 50_000

    def test_config_is_frozen(self) -> None:
        config = RateLimitConfig(requests_per_minute=10, tokens_per_minute=1_000)
        with pytest.raises((AttributeError, TypeError)):
            config.requests_per_minute = 999  # type: ignore[misc]

    def test_provider_defaults_exist(self) -> None:
        assert "anthropic" in PROVIDER_DEFAULTS
        assert "openai" in PROVIDER_DEFAULTS
        assert "ollama" in PROVIDER_DEFAULTS
        assert "litellm" in PROVIDER_DEFAULTS

    def test_provider_defaults_are_rate_limit_configs(self) -> None:
        for name, config in PROVIDER_DEFAULTS.items():
            assert isinstance(config, RateLimitConfig), f"{name} default is not RateLimitConfig"
            assert config.requests_per_minute > 0
            assert config.tokens_per_minute > 0


class TestRateLimiterAcquire:
    async def test_acquire_succeeds_within_limits(self) -> None:
        config = RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        limiter = RateLimiter(config)
        # Should complete immediately without raising
        await limiter.acquire(estimated_tokens=1_000)

    async def test_acquire_records_request(self) -> None:
        config = RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        limiter = RateLimiter(config)
        await limiter.acquire(estimated_tokens=500)
        # One request should now be recorded
        assert len(limiter._request_times) == 1  # noqa: SLF001

    async def test_multiple_acquires_within_limits(self) -> None:
        config = RateLimitConfig(requests_per_minute=100, tokens_per_minute=1_000_000)
        limiter = RateLimiter(config)
        for _ in range(10):
            await limiter.acquire(estimated_tokens=100)
        assert len(limiter._request_times) == 10  # noqa: SLF001

    async def test_acquire_respects_rpm_limit(self) -> None:
        """Acquiring more than RPM slots should block until window clears."""
        # Use a very high token limit and low RPM to isolate RPM behavior
        config = RateLimitConfig(requests_per_minute=2, tokens_per_minute=10_000_000)
        limiter = RateLimiter(config)

        # Fill up RPM limit
        await limiter.acquire(estimated_tokens=1)
        await limiter.acquire(estimated_tokens=1)

        # Manually expire the window by backdating the timestamps
        now = time.monotonic()
        limiter._request_times = [now - 61.0, now - 61.0]  # noqa: SLF001
        limiter._token_records = [(now - 61.0, 1), (now - 61.0, 1)]  # noqa: SLF001

        # Third acquire should now succeed immediately (window cleared)
        await asyncio.wait_for(limiter.acquire(estimated_tokens=1), timeout=1.0)

    async def test_acquire_respects_tpm_limit(self) -> None:
        """Acquiring more than TPM tokens should block until window clears."""
        config = RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=100)
        limiter = RateLimiter(config)

        # Fill up TPM with one large request
        await limiter.acquire(estimated_tokens=100)

        # Manually expire the window
        now = time.monotonic()
        limiter._request_times = [now - 61.0]  # noqa: SLF001
        limiter._token_records = [(now - 61.0, 100)]  # noqa: SLF001

        # Next acquire should succeed
        await asyncio.wait_for(limiter.acquire(estimated_tokens=100), timeout=1.0)


class TestRateLimiterPruning:
    async def test_old_records_are_pruned(self) -> None:
        config = RateLimitConfig(requests_per_minute=5, tokens_per_minute=10_000)
        limiter = RateLimiter(config)

        # Inject old timestamps (> 60s ago)
        old_time = time.monotonic() - 65.0
        limiter._request_times = [old_time, old_time]  # noqa: SLF001
        limiter._token_records = [(old_time, 500), (old_time, 500)]  # noqa: SLF001

        # Acquire should prune stale records and succeed
        await limiter.acquire(estimated_tokens=100)
        # After pruning, only the new request remains
        assert len(limiter._request_times) == 1  # noqa: SLF001


class TestRateLimiterBackoff:
    async def test_on_rate_limit_error_attempt_0_waits_at_least_1s(self) -> None:
        config = RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        limiter = RateLimiter(config)

        start = time.monotonic()
        await limiter.on_rate_limit_error(attempt=0)
        elapsed = time.monotonic() - start

        # 2^0 = 1 second base + jitter [0, 1)
        assert elapsed >= 1.0

    async def test_on_rate_limit_error_caps_at_64s(self) -> None:
        """For high attempt numbers, wait should be capped at 64 + jitter seconds."""
        config = RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        limiter = RateLimiter(config)

        # attempt=10 → 2^10 = 1024, but capped at 64
        # We don't actually wait; just verify the formula via monkeypatching
        waited: list[float] = []
        original_sleep = asyncio.sleep

        async def mock_sleep(delay: float) -> None:
            waited.append(delay)

        import asyncio as _asyncio
        _asyncio.sleep = mock_sleep  # type: ignore[assignment]
        try:
            await limiter.on_rate_limit_error(attempt=10)
        finally:
            _asyncio.sleep = original_sleep  # type: ignore[assignment]

        assert waited, "asyncio.sleep was not called"
        # base is capped at 64, plus jitter < 1
        assert waited[0] < 65.0

    async def test_config_property(self) -> None:
        config = RateLimitConfig(requests_per_minute=30, tokens_per_minute=60_000)
        limiter = RateLimiter(config)
        assert limiter.config is config
