"""Shared fixtures for provider unit tests."""

from __future__ import annotations

import pytest

from repowise.core.providers.llm import base as _llm_base


@pytest.fixture(autouse=True)
def fast_retry_waits(monkeypatch):
    """Collapse retry backoff waits so persistent-error tests stay fast.

    The production 429 policy waits up to ~60s cumulatively (see
    ``provider_retry_wait``). ``_WAIT_SCALE`` is read at call time, so
    scaling it down keeps the retry *shape* (attempt counts, retryability)
    under test while making the sleeps negligible.
    """
    monkeypatch.setattr(_llm_base, "_WAIT_SCALE", 0.001)
