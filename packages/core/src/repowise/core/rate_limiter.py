"""Async rate limiter with sliding-window RPM and TPM limits.

Every provider passes through a RateLimiter before making API calls.
On 429 responses, providers call on_rate_limit_error() which performs
exponential backoff with full jitter (to avoid thundering herd).

Design:
    - Uses a 60-second sliding window for both requests-per-minute (RPM)
      and tokens-per-minute (TPM).
    - The lock is released while sleeping so other coroutines can acquire it
      and check their own limits independently.
    - In multi-provider scenarios, each provider has its own RateLimiter
      instance constructed with its own RateLimitConfig.

Usage:
    config = RateLimitConfig(requests_per_minute=50, tokens_per_minute=100_000)
    limiter = RateLimiter(config)
    await limiter.acquire(estimated_tokens=2000)  # blocks until window allows
    response = await api_call()
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-provider rate limit configuration.

    Attributes:
        requests_per_minute: Maximum API requests within any 60-second window.
        tokens_per_minute:   Maximum tokens (input + output) within any 60-second window.
    """

    requests_per_minute: int
    tokens_per_minute: int


# Default rate limit configs for known providers.
# These are conservative defaults; operators can override via config.
PROVIDER_DEFAULTS: dict[str, RateLimitConfig] = {
    "anthropic": RateLimitConfig(requests_per_minute=50, tokens_per_minute=100_000),
    "openai": RateLimitConfig(requests_per_minute=60, tokens_per_minute=150_000),
    "gemini": RateLimitConfig(requests_per_minute=60, tokens_per_minute=1_000_000),
    # Ollama runs locally — effectively unlimited, but we cap to avoid OOM
    "ollama": RateLimitConfig(requests_per_minute=1_000, tokens_per_minute=10_000_000),
    "litellm": RateLimitConfig(requests_per_minute=60, tokens_per_minute=150_000),
    "zai": RateLimitConfig(requests_per_minute=60, tokens_per_minute=150_000),
    # MiniMax: conservative default (Starter tier). Set MINIMAX_TIER for plan-specific limits.
    "minimax": RateLimitConfig(requests_per_minute=5, tokens_per_minute=25_000),
}


class RateLimiter:
    """Async sliding-window rate limiter for RPM and TPM limits.

    Thread-safety: uses asyncio.Lock, safe for single-event-loop use.
    Not safe for use across multiple event loops.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        # Timestamps of requests within the last 60 seconds
        self._request_times: list[float] = []
        # (timestamp, token_count) pairs within the last 60 seconds
        self._token_records: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int = 1_000) -> None:
        """Block until rate limits permit the next request.

        Checks both RPM and TPM using a 60-second sliding window.
        Releases the lock while sleeping so other coroutines can proceed.

        Args:
            estimated_tokens: Expected token usage for the upcoming request.
                              Over-estimate slightly to avoid exceeding TPM.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                self._prune_old_records(now)

                rpm_ok = len(self._request_times) < self._config.requests_per_minute
                current_tpm = sum(n for _, n in self._token_records)
                tpm_ok = current_tpm + estimated_tokens <= self._config.tokens_per_minute

                if rpm_ok and tpm_ok:
                    self._request_times.append(now)
                    self._token_records.append((now, estimated_tokens))
                    return

                # Compute how long to sleep before re-checking
                sleep_time = 0.1  # minimum poll interval

                if not rpm_ok and self._request_times:
                    oldest_request = self._request_times[0]
                    time_until_slot = 60.0 - (now - oldest_request) + 0.01
                    sleep_time = max(sleep_time, time_until_slot)

                if not tpm_ok and self._token_records:
                    oldest_token_time = self._token_records[0][0]
                    time_until_slot = 60.0 - (now - oldest_token_time) + 0.01
                    sleep_time = max(sleep_time, time_until_slot)

            # Sleep outside the lock so other coroutines can proceed
            await asyncio.sleep(sleep_time)

    async def on_rate_limit_error(self, attempt: int) -> None:
        """Perform exponential backoff on a 429 response.

        Wait time: 2^attempt seconds + uniform jitter in [0, 1).
        Maximum wait is capped at 64 seconds to avoid excessive delays.

        Args:
            attempt: Zero-based retry attempt number. Pass 0 on the first 429.
        """
        base_wait = min(2**attempt, 64)
        jitter = random.uniform(0, 1)
        await asyncio.sleep(base_wait + jitter)

    def _prune_old_records(self, now: float) -> None:
        """Remove records older than 60 seconds from the sliding window."""
        cutoff = now - 60.0
        self._request_times = [t for t in self._request_times if t > cutoff]
        self._token_records = [(t, n) for t, n in self._token_records if t > cutoff]

    @property
    def config(self) -> RateLimitConfig:
        """The rate limit configuration for this limiter."""
        return self._config
