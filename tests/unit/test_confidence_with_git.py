"""Unit tests for compute_confidence_decay_with_git."""

import pytest

from repowise.core.generation.models import compute_confidence_decay_with_git


class TestComputeConfidenceDecayWithGit:
    """Tests for git-informed confidence decay modifiers."""

    def test_hotspot_decays_faster(self) -> None:
        """Hotspot file with direct relationship should decay faster (*0.94)."""
        base_decay = 0.85
        git_meta = {"is_hotspot": True, "is_stable": False}

        result = compute_confidence_decay_with_git(
            base_decay=base_decay,
            relationship="direct",
            git_meta=git_meta,
            commit_message=None,
        )

        assert result == pytest.approx(base_decay * 0.94)

    def test_stable_decays_slower(self) -> None:
        """Stable file with direct relationship should decay slower (*1.03)."""
        base_decay = 0.85
        git_meta = {"is_hotspot": False, "is_stable": True}

        result = compute_confidence_decay_with_git(
            base_decay=base_decay,
            relationship="direct",
            git_meta=git_meta,
            commit_message=None,
        )

        assert result == pytest.approx(base_decay * 1.03)

    def test_rewrite_commit_hard_decay(self) -> None:
        """Commit message containing 'refactor' should apply hard decay (*0.71)."""
        base_decay = 0.85

        result = compute_confidence_decay_with_git(
            base_decay=base_decay,
            relationship="direct",
            git_meta=None,
            commit_message="refactor: extract helper module",
        )

        assert result == pytest.approx(base_decay * 0.71)

    def test_typo_commit_soft_decay(self) -> None:
        """Commit message containing 'typo' should apply soft decay (*1.12)."""
        base_decay = 0.85

        result = compute_confidence_decay_with_git(
            base_decay=base_decay,
            relationship="direct",
            git_meta=None,
            commit_message="fix typo in docstring",
        )

        assert result == pytest.approx(base_decay * 1.12)
