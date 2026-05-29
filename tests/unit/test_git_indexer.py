"""Unit tests for GitIndexer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    c.committed_datetime = committed_datetime or datetime.now(UTC)
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
        assert (
            indexer._is_significant_commit("Bump lodash from 4.17.15 to 4.17.21", "Alice") is False
        )
        assert (
            indexer._is_significant_commit("chore: update dependencies across the board", "Alice")
            is False
        )
        assert (
            indexer._is_significant_commit("ci: fix the github actions workflow", "Alice") is False
        )
        assert (
            indexer._is_significant_commit("style: run prettier on the codebase", "Alice") is False
        )
        assert (
            indexer._is_significant_commit("build: update webpack config for prod", "Alice")
            is False
        )
        assert (
            indexer._is_significant_commit("release: v2.3.0 official release cut", "Alice") is False
        )

        # Author-based filtering (bot accounts)
        assert (
            indexer._is_significant_commit(
                "chore(deps): bump axios from 0.21.1 to 0.21.2",
                "dependabot[bot]",
            )
            is False
        )
        assert (
            indexer._is_significant_commit(
                "fix(deps): update dependency express to v5",
                "renovate[bot]",
            )
            is False
        )
        assert (
            indexer._is_significant_commit(
                "chore: automated release pipeline",
                "github-actions[bot]",
            )
            is False
        )

        # Too short (< 12 chars)
        assert indexer._is_significant_commit("fix typo", "Alice") is False

        # --- Should pass (return True) ---
        assert (
            indexer._is_significant_commit(
                "feat: add authentication module with OAuth2 support", "Alice"
            )
            is True
        )
        assert (
            indexer._is_significant_commit("fix: resolve race condition in worker queue", "Bob")
            is True
        )

        # Revert commits are now significant (high signal for risk/decisions)
        assert (
            indexer._is_significant_commit("revert: undo the last commit change", "Alice") is True
        )

        # Soft-skip rescued by decision-signal keyword
        assert (
            indexer._is_significant_commit("build: migrate from webpack to vite", "Alice") is True
        )
        assert (
            indexer._is_significant_commit("chore: deprecate legacy auth module", "Alice") is True
        )

        # Short but meaningful messages (>= 12 chars) now pass
        assert indexer._is_significant_commit("fix auth race", "Alice") is True


# ---------------------------------------------------------------------------
# 2. test_co_change_detection
# ---------------------------------------------------------------------------


class TestCoChangeDetection:
    """Files changed together >= 3 times are detected as co-change partners."""

    def test_co_change_detection(self) -> None:
        indexer = GitIndexer("/tmp/repo")

        mock_repo = MagicMock()
        all_files = {"a.py", "b.py", "c.py"}

        # Simulate `git log --name-only --no-merges --format=%x00%ct` output.
        # 4 commits where a.py and b.py always change together.
        # c.py only changes in commit 0.
        # Timestamps are recent (within decay window) so weights stay high.
        import time

        now = int(time.time())
        raw_log = (
            f"\x00{now}\na.py\nb.py\nc.py\n"  # commit 0
            f"\x00{now - 86400}\na.py\nb.py\n"  # commit 1 (1 day ago)
            f"\x00{now - 172800}\na.py\nb.py\n"  # commit 2 (2 days ago)
            f"\x00{now - 259200}\na.py\nb.py\n"  # commit 3 (3 days ago)
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

        # With temporal decay, score is close to 4 (all commits very recent)
        co_count = next(p["co_change_count"] for p in result["a.py"] if p["file_path"] == "b.py")
        assert co_count >= 3.9  # decay-weighted, very recent → near 4.0

        # Verify last_co_change date is present
        entry = next(p for p in result["a.py"] if p["file_path"] == "b.py")
        assert "last_co_change" in entry
        assert entry["last_co_change"] is not None


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
            metadata_list.append(
                {
                    "file_path": f"low_{i}.py",
                    "commit_count_90d": 1,
                    "is_hotspot": False,
                }
            )
        # Two high-churn files
        metadata_list.append(
            {
                "file_path": "hot_a.py",
                "commit_count_90d": 50,
                "is_hotspot": False,
            }
        )
        metadata_list.append(
            {
                "file_path": "hot_b.py",
                "commit_count_90d": 40,
                "is_hotspot": False,
            }
        )

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

        # Build 15 commits all older than 90 days.
        # _index_file now uses repo.git.log with NUL-delimited format:
        # \x00<sha>\x1f<author>\x1f<email>\x1f<unix_ts>\x1f<parents>\x1f<subject>\x1f<body>
        old_date = datetime.now(UTC) - timedelta(days=180)
        log_lines = []
        for i in range(15):
            ts = int((old_date - timedelta(days=i)).timestamp())
            log_lines.append(
                f"\x00sha{i:04d}\x1fAlice\x1falice@example.com\x1f{ts}\x1f\x1ffeat: old commit {i}\x1f"
            )
        mock_repo.git.log.return_value = "\n".join(log_lines)

        meta = indexer._index_file("stable_file.py", mock_repo)

        assert meta["commit_count_total"] == 15
        assert meta["commit_count_90d"] == 0
        assert meta["is_stable"] is True


class TestGitWindowAnchor:
    """REPOWISE_GIT_WINDOW_ANCHOR anchors recency windows to the repo's most
    recent commit instead of wall-clock now() — default off (product unchanged),
    on for historical T0 scoring so windowed signals aren't silently empty."""

    def _mock_repo_with_old_commits(self) -> tuple[MagicMock, datetime]:
        mock_repo = MagicMock()
        old_date = datetime.now(UTC) - timedelta(days=180)
        log_lines = []
        for i in range(15):
            ts = int((old_date - timedelta(days=i)).timestamp())
            log_lines.append(
                f"\x00sha{i:04d}\x1fAlice\x1falice@example.com\x1f{ts}\x1f\x1ffeat: old {i}\x1f"
            )
        mock_repo.git.log.return_value = "\n".join(log_lines)
        # HEAD tip = the newest of the (old) batch.
        mock_repo.head.commit.committed_date = int(old_date.timestamp())
        return mock_repo, old_date

    def test_default_uses_wall_clock(self, monkeypatch) -> None:
        monkeypatch.delenv("REPOWISE_GIT_WINDOW_ANCHOR", raising=False)
        indexer = GitIndexer("/tmp/repo")
        mock_repo, _ = self._mock_repo_with_old_commits()
        meta = indexer._index_file("f.py", mock_repo)
        # 180-day-old commits are outside a now()-anchored 90d window.
        assert meta["commit_count_90d"] == 0

    def test_anchor_to_head_commit(self, monkeypatch) -> None:
        monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
        indexer = GitIndexer("/tmp/repo")
        mock_repo, _ = self._mock_repo_with_old_commits()
        meta = indexer._index_file("f.py", mock_repo)
        # Anchored to the latest commit, all 15 fall within its preceding 90d.
        assert meta["commit_count_90d"] == 15


# ---------------------------------------------------------------------------
# 4b. test_numstat_parsing
# ---------------------------------------------------------------------------


class TestNumstatParsing:
    """Validate the single-git-log numstat parser only counts the target file."""

    def _build_log_output(self, file_path: str, entries: list[dict]) -> str:
        """Build a mock git log --numstat output string.

        Each entry: {sha, author, email, ts, parents, subject, body, numstat_lines}
        where numstat_lines is a list of (added, deleted, path) tuples.

        The trailing ``\\x1f%b`` field mirrors the real log format — git always
        emits the body field (empty when the commit has no body), so the parser
        keys off the 7th field separator.
        """
        lines = []
        for e in entries:
            header = (
                f"\x00{e['sha']}\x1f{e.get('author', 'Dev')}"
                f"\x1f{e.get('email', 'dev@x.com')}"
                f"\x1f{e['ts']}\x1f{e.get('parents', '')}"
                f"\x1f{e.get('subject', 'some commit')}"
                f"\x1f{e.get('body', '')}"
            )
            lines.append(header)
            for added, deleted, path in e.get("numstat_lines", []):
                lines.append(f"{added}\t{deleted}\t{path}")
            lines.append("")  # blank line between commits (git does this)
        return "\n".join(lines)

    def test_only_target_file_counted(self) -> None:
        """Churn stats must only include the target file, not other files in the same commit."""
        indexer = GitIndexer("/tmp/repo")
        mock_repo = MagicMock()

        now = datetime.now(UTC)
        recent_ts = int((now - timedelta(days=5)).timestamp())

        raw = self._build_log_output(
            "src/app.py",
            [
                {
                    "sha": "aaa11111",
                    "ts": recent_ts,
                    "subject": "feat: big change",
                    "numstat_lines": [
                        ("100", "50", "src/app.py"),  # target: should count
                        ("500", "300", "src/other.py"),  # not target: must NOT count
                        ("200", "100", "src/utils.py"),  # not target: must NOT count
                    ],
                },
            ],
        )
        mock_repo.git.log.return_value = raw

        meta = indexer._index_file("src/app.py", mock_repo)

        assert meta["lines_added_90d"] == 100
        assert meta["lines_deleted_90d"] == 50
        assert meta["avg_commit_size"] == 150.0  # (100+50)/1

    def test_binary_file_stats_ignored(self) -> None:
        """Binary files emit '-' for added/deleted — should contribute 0."""
        indexer = GitIndexer("/tmp/repo")
        mock_repo = MagicMock()

        recent_ts = int((datetime.now(UTC) - timedelta(days=5)).timestamp())

        raw = self._build_log_output(
            "icon.png",
            [
                {
                    "sha": "bbb22222",
                    "ts": recent_ts,
                    "subject": "feat: add icon",
                    "numstat_lines": [
                        ("-", "-", "icon.png"),
                    ],
                },
            ],
        )
        mock_repo.git.log.return_value = raw

        meta = indexer._index_file("icon.png", mock_repo)

        assert meta["lines_added_90d"] == 0
        assert meta["lines_deleted_90d"] == 0

    def test_empty_output_returns_defaults(self) -> None:
        """Empty git log output returns default meta without errors."""
        indexer = GitIndexer("/tmp/repo")
        mock_repo = MagicMock()
        mock_repo.git.log.return_value = ""

        meta = indexer._index_file("new_file.py", mock_repo)

        assert meta["commit_count_total"] == 0
        assert meta["lines_added_90d"] == 0

    def test_malformed_header_skipped(self) -> None:
        """Lines with fewer than 6 fields are gracefully skipped."""
        indexer = GitIndexer("/tmp/repo")
        mock_repo = MagicMock()

        recent_ts = int((datetime.now(UTC) - timedelta(days=5)).timestamp())
        # First record is malformed (only 3 fields), second is valid
        raw = (
            f"\x00badsha\x1fAlice\x1falice@x.com\n"  # only 3 fields
            f"\x00goodsha\x1fBob\x1fbob@x.com\x1f{recent_ts}\x1f\x1ffeat: valid commit\x1f\n"
            f"10\t5\ttest.py\n"
        )
        mock_repo.git.log.return_value = raw

        meta = indexer._index_file("test.py", mock_repo)

        assert meta["commit_count_total"] == 1
        assert meta["lines_added_90d"] == 10

    def test_merge_commit_detected(self) -> None:
        """Commits with multiple parent SHAs are flagged as merge commits."""
        indexer = GitIndexer("/tmp/repo")
        mock_repo = MagicMock()

        recent_ts = int((datetime.now(UTC) - timedelta(days=5)).timestamp())
        raw = (
            f"\x00sha1\x1fAlice\x1fa@x.com\x1f{recent_ts}"
            f"\x1fparent1 parent2\x1fMerge branch main\x1f\n"
            f"5\t2\tmerged.py\n"
        )
        mock_repo.git.log.return_value = raw

        meta = indexer._index_file("merged.py", mock_repo)

        assert meta["merge_commit_count_90d"] == 1

    def test_rename_tracking_with_follow(self) -> None:
        """When follow_renames=True, numstat lines with old paths are counted."""
        indexer = GitIndexer("/tmp/repo", follow_renames=True)
        mock_repo = MagicMock()

        recent_ts = int((datetime.now(UTC) - timedelta(days=5)).timestamp())
        old_ts = int((datetime.now(UTC) - timedelta(days=30)).timestamp())

        # Simulates --follow output: recent commit uses new name,
        # older commit uses rename notation, oldest uses old name.
        raw = (
            f"\x00sha1\x1fAlice\x1fa@x.com\x1f{recent_ts}\x1f\x1ffeat: update\x1f\n"
            f"10\t3\tsrc/new_name.py\n"
            f"\n"
            f"\x00sha2\x1fAlice\x1fa@x.com\x1f{old_ts}\x1f\x1frename file\x1f\n"
            f"0\t0\t{{src/old_name.py => src/new_name.py}}\n"
            f"\n"
            f"\x00sha3\x1fAlice\x1fa@x.com\x1f{old_ts - 86400}\x1f\x1ffeat: old work\x1f\n"
            f"20\t5\tsrc/old_name.py\n"
        )
        mock_repo.git.log.return_value = raw

        meta = indexer._index_file("src/new_name.py", mock_repo)

        assert meta["commit_count_total"] == 3
        # Both the new-name and old-name stats should be counted
        assert meta["lines_added_90d"] == 10 + 20  # sha1 + sha3
        assert meta["lines_deleted_90d"] == 3 + 5


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
        # 1 commit with x.py + z.py (also below min_count=3)
        import time

        now = int(time.time())
        raw_log = (
            f"\x00{now}\nx.py\ny.py\n"
            f"\x00{now - 86400}\nx.py\ny.py\n"
            f"\x00{now - 172800}\nx.py\nz.py\n"
        )
        mock_repo.git.log.return_value = raw_log

        result = indexer._compute_co_changes(mock_repo, all_files, commit_limit=500, min_count=3)

        # No pairs should appear since none reach min_count=3
        assert result == {}


# ---------------------------------------------------------------------------
# 6b. test_change_entropy
# ---------------------------------------------------------------------------


class TestChangeEntropy:
    """Hassan HCM: focused single-file commits → ~0 entropy; wide scattered
    commits → high entropy; commits above the file-set cap are excluded."""

    def test_change_entropy_focused_vs_scattered(self) -> None:
        import time

        from repowise.core.ingestion.git_indexer.co_change import (
            compute_co_changes_and_entropy,
        )

        scattered_peers = [f"peer_{i}.py" for i in range(9)]
        huge_peers = [f"huge_{i}.py" for i in range(34)]
        all_files = {"focused.py", "scattered.py", "bigcommit.py"}
        all_files.update(scattered_peers)
        all_files.update(huge_peers)

        now = int(time.time())
        blocks: list[str] = []
        # 3 focused commits: focused.py changes ALONE (|F| == 1 → log2(1) == 0).
        for i in range(3):
            blocks.append(f"\x00{now - i * 86400}\nfocused.py\n")
        # 3 scattered commits: scattered.py amid 9 peers (|F| == 10).
        for i in range(3):
            files = "\n".join(["scattered.py", *scattered_peers])
            blocks.append(f"\x00{now - i * 86400}\n{files}\n")
        # 1 mass-edit commit (|F| == 35 > 30): bigcommit.py must get NO entropy.
        big_files = "\n".join(["bigcommit.py", *huge_peers])
        blocks.append(f"\x00{now}\n{big_files}\n")

        mock_repo = MagicMock()
        mock_repo.git.log.return_value = "".join(blocks)

        _co, entropy = compute_co_changes_and_entropy(
            mock_repo, all_files, commit_limit=2000, min_count=2
        )

        # Focused file changed alone → no entropy entry.
        assert entropy.get("focused.py", 0.0) == 0.0
        # Scattered file accrued positive entropy.
        assert entropy.get("scattered.py", 0.0) > 0.0
        # Mass-edit commit excluded → bigcommit.py gets nothing from it.
        assert entropy.get("bigcommit.py", 0.0) == 0.0
        # Scattered clearly dominates focused.
        assert entropy["scattered.py"] > entropy.get("focused.py", 0.0)

    def test_change_entropy_percentile_silent_when_all_zero(self) -> None:
        """ESSENTIAL-tier shape (no entropy on any file) → all pct stay 0.0."""
        from repowise.core.ingestion.git_indexer.enrich import compute_percentiles

        metadata_list = [
            {"file_path": f"f{i}.py", "commit_count_90d": 5, "change_entropy": 0.0}
            for i in range(5)
        ]
        compute_percentiles(metadata_list)
        for m in metadata_list:
            assert m["change_entropy_pct"] == 0.0

    def test_change_entropy_percentile_ranks_nonzero(self) -> None:
        """Only files with positive entropy are ranked; zero-entropy files stay 0.0."""
        from repowise.core.ingestion.git_indexer.enrich import compute_percentiles

        metadata_list = [
            {"file_path": "zero.py", "change_entropy": 0.0},
            {"file_path": "low.py", "change_entropy": 0.5},
            {"file_path": "mid.py", "change_entropy": 1.0},
            {"file_path": "high.py", "change_entropy": 2.0},
        ]
        compute_percentiles(metadata_list)
        pct = {m["file_path"]: m["change_entropy_pct"] for m in metadata_list}
        assert pct["zero.py"] == 0.0
        # Three nonzero files ranked 0, 1, 2 over n=3 → 0.0, 0.333, 0.667.
        assert pct["high.py"] > pct["mid.py"] > pct["low.py"]
        assert pct["high.py"] == pytest.approx(2 / 3)


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
