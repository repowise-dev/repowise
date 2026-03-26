"""Unit tests for GitIndexer."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_commit(
    message: str = "feat: add feature X to the system",
    author_name: str = "Alice",
    author_email: str = "alice@example.com",
    hexsha: str = "abcd1234",
    committed_datetime: datetime | None = None,
    parents: list | None = None,
):
    """Return a lightweight mock commit object mimicking gitpython's Commit."""
    c = MagicMock()
    c.message = message
    c.hexsha = hexsha
    c.author.name = author_name
    c.author.email = author_email
    c.committed_datetime = committed_datetime or datetime.now(timezone.utc)
    c.parents = parents if parents is not None else [MagicMock()]
    return c


def _make_diff(b_path: str, a_path: str | None = None):
    """Return a lightweight mock diff entry."""
    d = MagicMock()
    d.b_path = b_path
    d.a_path = a_path or b_path
    return d


# ---------------------------------------------------------------------------
# 1. test_significant_commit_filter
# ---------------------------------------------------------------------------


class TestSignificantCommitFilter:
    """Merges, bumps, chore, and bot commits are filtered out; normal commits pass.

    Revert commits are kept (high signal for risk and decision archaeology).
    Soft-skip prefixes (chore:, ci:, style:, build:, release:, Bump) are
    normally filtered but rescued when the message contains a decision-signal
    keyword (e.g. "build: migrate from webpack to vite").
    """

    def test_significant_commit_filter(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        # --- Should be filtered (return False) ---

        # Hard-skip: merge commits are always noise
        assert indexer._is_significant_commit("Merge branch 'main' into feature", "Alice") is False

        # Soft-skip: conventional prefixes without decision signal
        assert indexer._is_significant_commit("Bump lodash from 4.17.15 to 4.17.21", "Alice") is False
        assert indexer._is_significant_commit("chore: update dependencies across the board", "Alice") is False
        assert indexer._is_significant_commit("ci: fix the github actions workflow", "Alice") is False
        assert indexer._is_significant_commit("style: run prettier on the codebase", "Alice") is False
        assert indexer._is_significant_commit("build: update webpack config for prod", "Alice") is False
        assert indexer._is_significant_commit("release: v2.3.0 official release cut", "Alice") is False

        # Author-based filtering (bot accounts)
        assert indexer._is_significant_commit(
            "chore(deps): bump axios from 0.21.1 to 0.21.2",
            "dependabot[bot]",
        ) is False
        assert indexer._is_significant_commit(
            "fix(deps): update dependency express to v5",
            "renovate[bot]",
        ) is False
        assert indexer._is_significant_commit(
            "chore: automated release pipeline",
            "github-actions[bot]",
        ) is False

        # Too short (< 12 chars)
        assert indexer._is_significant_commit("fix typo", "Alice") is False

        # --- Should pass (return True) ---
        assert indexer._is_significant_commit(
            "feat: add authentication module with OAuth2 support", "Alice"
        ) is True
        assert indexer._is_significant_commit(
            "fix: resolve race condition in worker queue", "Bob"
        ) is True

        # Revert commits are now significant (high signal for risk/decisions)
        assert indexer._is_significant_commit(
            "revert: undo the last commit change", "Alice"
        ) is True

        # Soft-skip rescued by decision-signal keyword
        assert indexer._is_significant_commit(
            "build: migrate from webpack to vite", "Alice"
        ) is True
        assert indexer._is_significant_commit(
            "chore: deprecate legacy auth module", "Alice"
        ) is True

        # Short but meaningful messages (>= 12 chars) now pass
        assert indexer._is_significant_commit(
            "fix auth race", "Alice"
        ) is True


# ---------------------------------------------------------------------------
# 2. test_co_change_detection
# ---------------------------------------------------------------------------


class TestCoChangeDetection:
    """Files changed together >= 3 times are detected as co-change partners."""

    def test_co_change_detection(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        mock_repo = MagicMock()
        all_files = {"a.py", "b.py", "c.py"}

        # Simulate `git log --name-only --no-merges --format=%x00` output.
        # 4 commits where a.py and b.py always change together.
        # c.py only changes in commit 0.
        raw_log = (
            "\x00\na.py\nb.py\nc.py\n"  # commit 0
            "\x00\na.py\nb.py\n"         # commit 1
            "\x00\na.py\nb.py\n"         # commit 2
            "\x00\na.py\nb.py\n"         # commit 3
        )
        mock_repo.git.log.return_value = raw_log

        result = indexer._compute_co_changes(mock_repo, all_files, commit_limit=500, min_count=3)

        # a.py <-> b.py should appear (co-changed 4 times, >= min_count=3)
        assert "a.py" in result
        partner_paths = [p["file_path"] for p in result["a.py"]]
        assert "b.py" in partner_paths

        assert "b.py" in result
        partner_paths_b = [p["file_path"] for p in result["b.py"]]
        assert "a.py" in partner_paths_b

        # Verify the count is 4
        co_count = next(p["co_change_count"] for p in result["a.py"] if p["file_path"] == "b.py")
        assert co_count == 4


# ---------------------------------------------------------------------------
# 3. test_hotspot_classification
# ---------------------------------------------------------------------------


class TestHotspotClassification:
    """Top 25% churn + activity marks a file as is_hotspot."""

    def test_hotspot_classification(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        # Create 8 files: 6 with low churn, 2 with high churn.
        # The top 25% threshold = index 6 in sorted order (0-indexed).
        # Files with commit_count_90d > p75 AND > 0 should be hotspots.
        metadata_list = []
        for i in range(6):
            metadata_list.append({
                "file_path": f"low_{i}.py",
                "commit_count_90d": 1,
                "is_hotspot": False,
            })
        # Two high-churn files
        metadata_list.append({
            "file_path": "hot_a.py",
            "commit_count_90d": 50,
            "is_hotspot": False,
        })
        metadata_list.append({
            "file_path": "hot_b.py",
            "commit_count_90d": 40,
            "is_hotspot": False,
        })

        indexer._compute_percentiles(metadata_list)

        # The two high-churn files should be hotspots
        hot_a = next(m for m in metadata_list if m["file_path"] == "hot_a.py")
        hot_b = next(m for m in metadata_list if m["file_path"] == "hot_b.py")
        assert hot_a["is_hotspot"] is True
        assert hot_b["is_hotspot"] is True

        # Low-churn files should NOT be hotspots
        for m in metadata_list:
            if m["file_path"].startswith("low_"):
                assert m["is_hotspot"] is False

        # All files should have churn_percentile set
        for m in metadata_list:
            assert "churn_percentile" in m
            assert 0.0 <= m["churn_percentile"] <= 1.0


# ---------------------------------------------------------------------------
# 4. test_stable_classification
# ---------------------------------------------------------------------------


class TestStableClassification:
    """>10 total commits, 0 in last 90 days = is_stable."""

    def test_stable_classification(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        mock_repo = MagicMock()

        # Build 15 commits all older than 90 days
        old_date = datetime.now(timezone.utc) - timedelta(days=180)
        commits = [
            _make_commit(
                hexsha=f"sha{i:04d}",
                committed_datetime=old_date - timedelta(days=i),
            )
            for i in range(15)
        ]
        mock_repo.iter_commits.return_value = commits

        # Mock blame to return a simple result
        mock_repo.blame.return_value = [
            (_make_commit(author_name="Alice", author_email="alice@example.com"), ["line"] * 100),
        ]

        meta = indexer._index_file("stable_file.py", mock_repo)

        assert meta["commit_count_total"] == 15
        assert meta["commit_count_90d"] == 0
        assert meta["is_stable"] is True


# ---------------------------------------------------------------------------
# 5. test_git_unavailable_graceful
# ---------------------------------------------------------------------------


class TestGitUnavailableGraceful:
    """When git is unavailable, index_repo returns empty metadata without crashing."""

    @pytest.mark.asyncio
    async def test_git_unavailable_graceful(self) -> None:
        indexer = GitIndexer("/nonexistent/path")

        # Patch _get_repo to return None (simulating git unavailable)
        with patch.object(indexer, "_get_repo", return_value=None):
            summary, metadata = await indexer.index_repo("test-repo-id")

        assert summary.files_indexed == 0
        assert summary.hotspots == 0
        assert summary.stable_files == 0
        assert metadata == []


# ---------------------------------------------------------------------------
# 6. test_co_change_below_threshold_skipped
# ---------------------------------------------------------------------------


class TestCoChangeBelowThresholdSkipped:
    """Pairs with co-change count < min_count are not stored."""

    def test_co_change_below_threshold_skipped(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        mock_repo = MagicMock()
        all_files = {"x.py", "y.py", "z.py"}

        # Only 2 commits with x.py + y.py together (below default min_count=3)
        parent = MagicMock()
        commits = []
        for i in range(2):
            c = _make_commit(hexsha=f"sha{i}", parents=[parent])
            c.diff.return_value = [_make_diff("x.py"), _make_diff("y.py")]
            commits.append(c)

        # 1 commit with x.py + z.py (below min_count=3)
        c_single = _make_commit(hexsha="sha_z", parents=[parent])
        c_single.diff.return_value = [_make_diff("x.py"), _make_diff("z.py")]
        commits.append(c_single)

        mock_repo.iter_commits.return_value = commits

        result = indexer._compute_co_changes(mock_repo, all_files, commit_limit=500, min_count=3)

        # No pairs should appear since none reach min_count=3
        assert result == {}


# ---------------------------------------------------------------------------
# 7. test_blame_ownership_computed
# ---------------------------------------------------------------------------


class TestBlameOwnershipComputed:
    """Primary owner is set to the dominant author from git blame."""

    def test_blame_ownership_computed(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        mock_repo = MagicMock()

        # Simulate blame output: Alice owns 80 lines, Bob owns 20 lines
        alice_commit = MagicMock()
        alice_commit.author.name = "Alice"
        alice_commit.author.email = "alice@example.com"

        bob_commit = MagicMock()
        bob_commit.author.name = "Bob"
        bob_commit.author.email = "bob@example.com"

        mock_repo.blame.return_value = [
            (alice_commit, ["line"] * 80),
            (bob_commit, ["line"] * 20),
        ]

        name, email, pct = indexer._get_blame_ownership("src/main.py", mock_repo)

        assert name == "Alice"
        assert email == "alice@example.com"
        assert pct == pytest.approx(0.8)
