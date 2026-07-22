"""`.update.pending` marker lifecycle: stale markers must get cleaned up.

The consumer used to clear the marker only on an exact ``pending == head``
match, so once HEAD advanced past the pending commit (or that commit was
rebased away) the marker leaked forever. These tests pin the ancestry-aware
cleanup: keep a strictly-newer marker, clear everything else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.cli.helpers import (
    consume_update_pending,
    read_update_pending,
    write_update_pending,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    ).stdout.strip()


def _repo_with_three_commits(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("0")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c0")
    c0 = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("1")
    _git(repo, "commit", "-am", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-am", "c2")
    c2 = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    return repo, c0, c1, c2


def test_pending_equal_to_head_is_cleared(tmp_path: Path) -> None:
    repo, _c0, c1, _c2 = _repo_with_three_commits(tmp_path)
    write_update_pending(repo, c1)
    consume_update_pending(repo, c1)
    assert read_update_pending(repo) is None


def test_pending_behind_head_is_cleared(tmp_path: Path) -> None:
    """Index advanced past the pending commit (the real leak): must clear."""
    repo, c0, _c1, c2 = _repo_with_three_commits(tmp_path)
    write_update_pending(repo, c0)
    consume_update_pending(repo, c2)
    assert read_update_pending(repo) is None


def test_pending_strictly_ahead_is_kept(tmp_path: Path) -> None:
    """A pending commit newer than what we indexed is real un-indexed work."""
    repo, c0, _c1, c2 = _repo_with_three_commits(tmp_path)
    write_update_pending(repo, c2)
    consume_update_pending(repo, c0)
    assert read_update_pending(repo) == c2


def test_unresolvable_pending_is_cleared(tmp_path: Path) -> None:
    """A rebased/gc'd-away commit no longer resolves; drop the garbage marker."""
    repo, _c0, c1, _c2 = _repo_with_three_commits(tmp_path)
    write_update_pending(repo, "0" * 40)
    consume_update_pending(repo, c1)
    assert read_update_pending(repo) is None


def test_no_marker_is_a_noop(tmp_path: Path) -> None:
    repo, _c0, c1, _c2 = _repo_with_three_commits(tmp_path)
    consume_update_pending(repo, c1)  # must not raise
    assert read_update_pending(repo) is None


def test_none_indexed_head_clears(tmp_path: Path) -> None:
    """No resolvable indexed head to compare against -> treat marker as stale."""
    repo, _c0, c1, _c2 = _repo_with_three_commits(tmp_path)
    write_update_pending(repo, c1)
    consume_update_pending(repo, None)
    assert read_update_pending(repo) is None


def test_core_clear_stale_update_pending_matches_cli(tmp_path: Path) -> None:
    """The core-side helper (used by the workspace updater, which can't import
    CLI helpers) applies the same ancestry rules as the CLI consumer."""
    from repowise.core.workspace.update import clear_stale_update_pending

    repo, c0, _c1, c2 = _repo_with_three_commits(tmp_path)

    write_update_pending(repo, c0)  # behind head -> cleared
    clear_stale_update_pending(repo, c2)
    assert read_update_pending(repo) is None

    write_update_pending(repo, c2)  # ahead of head -> kept
    clear_stale_update_pending(repo, c0)
    assert read_update_pending(repo) == c2


def test_real_update_clears_stale_marker(tmp_path: Path) -> None:
    """End to end: a `repowise update` that catches up drops a stale pending
    marker a bailed update left behind (the observed leak)."""
    import asyncio

    from click.testing import CliRunner

    from repowise.cli.helpers import save_state
    from repowise.cli.main import cli
    from repowise.core.pipeline.full_index import index_repo_full

    repo, c0, _c1, c2 = _repo_with_three_commits(tmp_path)
    asyncio.run(index_repo_full(repo))
    # index_repo_full does not persist state.json in the test harness; record
    # the sync/docs pointers at HEAD so the update resolves to "already current".
    save_state(repo, {"last_sync_commit": c2, "last_docs_commit": c2, "docs_enabled": False})

    # A bailed sibling update left a marker pointing at an older commit; the
    # index has since moved to HEAD (c2), so the marker is obsolete.
    write_update_pending(repo, c0)

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert read_update_pending(repo) is None, "a caught-up update must drop the stale marker"
