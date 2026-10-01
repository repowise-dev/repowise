"""Shared subprocess concurrency resolution for CLI-backed providers."""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

DEFAULT_CONCURRENCY = 4


def resolve_concurrency(env_var: str, provider: str) -> int:
    """Read ``env_var`` as a subprocess concurrency limit.

    Falls back to ``DEFAULT_CONCURRENCY`` when unset or not an integer, and
    clamps to a minimum of 1 so ``0`` cannot deadlock the semaphore.
    """
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        log.warning(f"{provider}.concurrency.invalid", value=raw, using=DEFAULT_CONCURRENCY)
        return DEFAULT_CONCURRENCY
    return max(1, value)
