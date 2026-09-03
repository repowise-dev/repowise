"""Tests for the update path's decision vector store (issue #1370).

``_build_update_vector_store`` swallows failures and returns None so the
decision upsert still works without a store — but the failure must land in
the run's ``degraded`` list, or the panel says nothing while semantic dedup
is silently off.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from repowise.cli.commands.update_cmd.incremental import _build_update_vector_store


def test_failure_is_recorded_in_degraded() -> None:
    """A store build failure must surface in the degraded list."""
    degraded: list[str] = []
    with patch(
        "repowise.cli.providers.build_embedder",
        side_effect=RuntimeError("boom"),
    ):
        assert _build_update_vector_store("/tmp/repo", {"embedder": "ollama"}, degraded) is None
    assert any("Decision vector store" in d and "boom" in d for d in degraded)


def test_failure_without_degraded_stays_silent() -> None:
    """Callers without a degraded list keep the old silent contract."""
    with patch(
        "repowise.cli.providers.build_embedder",
        side_effect=RuntimeError("boom"),
    ):
        assert _build_update_vector_store("/tmp/repo", {"embedder": "ollama"}) is None


def test_success_returns_the_store() -> None:
    """A healthy build returns the store and records nothing."""
    degraded: list[str] = []
    store = object()
    with patch(
        "repowise.cli.providers.build_embedder",
        return_value=object(),
    ), patch(
        "repowise.cli.providers.build_vector_store",
        return_value=store,
    ):
        assert _build_update_vector_store("/tmp/repo", {"embedder": "ollama"}, degraded) is store
    assert degraded == []
