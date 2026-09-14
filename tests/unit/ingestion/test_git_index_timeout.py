"""Unit tests for the git-index file timeout resolver.

The per-file git indexing timeout must not fire early on legitimately
long first-time indexes of large repositories (the same premature-timeout
failure mode as long build/compile commands — issue #1781). It is therefore
configurable via ``REPOWISE_GIT_INDEX_TIMEOUT_S`` and defaults generously.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest

import repowise.core.ingestion.git_indexer._constants as constants


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear the override between tests and reload the module constants."""
    monkeypatch.delenv("REPOWISE_GIT_INDEX_TIMEOUT_S", raising=False)
    yield
    importlib.reload(constants)


def test_default_timeout_is_generous() -> None:
    assert constants._FILE_INDEX_TIMEOUT_SECS == 120.0


def test_env_override_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOWISE_GIT_INDEX_TIMEOUT_S", "300")
    importlib.reload(constants)
    assert constants._FILE_INDEX_TIMEOUT_SECS == 300.0


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "  ", "nan", "inf"])
def test_bad_env_value_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("REPOWISE_GIT_INDEX_TIMEOUT_S", bad)
    importlib.reload(constants)
    assert constants._FILE_INDEX_TIMEOUT_SECS == 120.0
