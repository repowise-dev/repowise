"""Unit tests for OrchestratorMode policy."""

from __future__ import annotations

from repowise.core.ingestion.git_indexer import GitIndexTier
from repowise.core.pipeline.modes import OrchestratorMode


def test_standard_mode_full_git_and_docs() -> None:
    assert OrchestratorMode.STANDARD.git_tier is GitIndexTier.FULL
    assert OrchestratorMode.STANDARD.allows_doc_generation is True


def test_fast_mode_essential_git_no_docs() -> None:
    assert OrchestratorMode.FAST.git_tier is GitIndexTier.ESSENTIAL
    assert OrchestratorMode.FAST.allows_doc_generation is False


def test_mode_is_str_enum() -> None:
    # StrEnum members compare equal to their string value — handy for CLI glue.
    assert OrchestratorMode.FAST == "fast"
    assert OrchestratorMode("standard") is OrchestratorMode.STANDARD
