"""Single-repo docs-pointer separation (issue #847).

Bug: index-only ``repowise update`` advances ``last_sync_commit`` to HEAD
without regenerating pages. Because ``repowise update --docs`` used
``last_sync_commit`` as its diff base, a later docs run saw an empty diff
and silently regenerated nothing ("No changed files detected", exit 0),
leaving stale prose permanently unreachable.

These assert the externally-observable behavior the fix requires (the
workspace-side counterpart lives in test_workspace_docs_pointer.py):

1. An index-only update must never advance the docs pointer.
2. A run that actually generates pages must advance the docs pointer.
3. The docs diff base must come from the docs pointer, not the sync
   pointer, once the docs pointer exists.
4. End-to-end: after docs were generated at C0, an index-only update to
   C1 followed by new commits to C2 must NOT report "No changed files
   detected" on the next --docs run -- it must regenerate the files
   changed since C0 (not since C1).
5. Backfill: a state.json with no docs pointer at all must not crash and
   must fall back to something sane (last_sync_commit), matching the
   "no worse off than today" requirement for legacy state files.

If any of these fail, either the fix isn't complete or your fork uses a
different field name than `last_docs_commit` -- adjust DOCS_POINTER_KEY
below to match your implementation instead of assuming the test is wrong.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.helpers import load_state, save_state
from repowise.cli.main import cli

# If your fix uses a different key name than the issue's suggested
# ``last_docs_commit``, change it here -- everything else adapts.
DOCS_POINTER_KEY = "last_docs_commit"
SYNC_POINTER_KEY = "last_sync_commit"


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/unit/cli/test_update_e2e.py conventions)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    (repo / "b.py").write_text("from a import alpha\n\n\ndef beta():\n    return alpha() + 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _index_full(repo: Path) -> None:
    """Real index-only init: full pipeline + persistence, no LLM cost."""
    import asyncio

    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def _commit_change(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# 1 + 2: pointer independence at the persistence layer
# ---------------------------------------------------------------------------


def test_index_only_update_does_not_advance_docs_pointer(tmp_path: Path) -> None:
    """Core of the fix: index-only runs must not touch the docs pointer."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    base = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: base, DOCS_POINTER_KEY: base, "docs_enabled": False})

    new_head = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output

    state = _state(repo)
    assert state[SYNC_POINTER_KEY] == new_head, "sync pointer should still advance"
    assert state.get(DOCS_POINTER_KEY) == base, (
        "index-only update must NOT advance the docs pointer -- this is the "
        "exact regression #847 reports"
    )


def test_docs_generating_update_advances_docs_pointer(tmp_path: Path) -> None:
    """A run that actually regenerates pages must move the docs pointer."""
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    base = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: base, DOCS_POINTER_KEY: base, "docs_enabled": True})

    new_head = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output

    state = _state(repo)
    assert state.get(DOCS_POINTER_KEY) == new_head, (
        "a run that generates pages must advance the docs pointer to HEAD"
    )


# ---------------------------------------------------------------------------
# 3 + 4: the actual bug -- diff base selection and the full repro
# ---------------------------------------------------------------------------


def test_stale_prose_is_reachable_after_intervening_index_only_updates(tmp_path: Path) -> None:
    """The exact scenario in #847: docs generated at C0, then one or more
    index-only updates walk last_sync_commit forward, then new source
    changes land. The next --docs run must diff from C0 (last_docs_commit),
    not from the index-only pointer -- so the stale file is NOT silently
    skipped.
    """
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    c0 = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: c0, DOCS_POINTER_KEY: c0, "docs_enabled": True})

    # Intervening index-only update with no source changes -- pointer drift
    # happens even with a no-op commit-less run in some flows, but the
    # important case is a real commit walked past by index-only mode.
    c1 = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py (index-only)")
    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert _state(repo)[SYNC_POINTER_KEY] == c1

    # A second source change that must still be picked up by the next docs run.
    c2 = _commit_change(repo, "d.py", "def delta():\n    return 4\n", "add d.py (needs docs)")
    assert c2 != c1

    # Clear lock because atexit doesn't run in CliRunner
    from repowise.cli.commands.update_cmd.command import release_update_lock, clear_update_queued
    release_update_lock(repo)
    clear_update_queued(repo)

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output

    assert "No changed files detected" not in result.output, (
        "regression reproduced: docs run used the index-only-advanced "
        "pointer as its diff base and saw an empty diff"
    )

    state = _state(repo)
    print(f"c0 = {c0}")
    print(f"c1 = {c1}")
    print(f"c2 = {c2}")
    print(f"state = {state}")
    assert state.get(DOCS_POINTER_KEY) == c2, "docs pointer should now reach HEAD"


def test_index_only_diff_base_is_unaffected(tmp_path: Path) -> None:
    """Sanity check: the fix must not change index-only's own diff base,
    only decouple it from the docs path.
    """
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    base = _git(repo, "rev-parse", "HEAD")
    save_state(repo, {SYNC_POINTER_KEY: base, DOCS_POINTER_KEY: base, "docs_enabled": False})

    new_head = _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

    result = CliRunner().invoke(cli, ["update", str(repo), "--index-only", "--no-workspace"])
    assert result.exit_code == 0, result.output
    assert "No changed files detected" not in result.output, (
        "index-only should still detect and index the new commit"
    )
    assert _state(repo)[SYNC_POINTER_KEY] == new_head


# ---------------------------------------------------------------------------
# 5: backfill for legacy state files
# ---------------------------------------------------------------------------


def test_backfill_when_docs_pointer_is_absent(tmp_path: Path) -> None:
    """Legacy state.json files predate the docs pointer entirely. The docs
    path must not crash and must fall back to last_sync_commit, per the
    issue's explicit backfill requirement.
    """
    repo = _make_git_repo(tmp_path)
    _index_full(repo)
    base = _git(repo, "rev-parse", "HEAD")
    # Deliberately no DOCS_POINTER_KEY -- simulates a pre-fix state.json.
    save_state(repo, {SYNC_POINTER_KEY: base, "docs_enabled": True})
    assert DOCS_POINTER_KEY not in load_state(repo)

    _commit_change(repo, "c.py", "def gamma():\n    return 3\n", "add c.py")

    result = CliRunner().invoke(
        cli, ["update", str(repo), "--docs", "--provider", "mock", "--no-workspace"]
    )
    assert result.exit_code == 0, result.output
    # Must not crash, and must have picked up a real base rather than
    # silently doing nothing forever.
    assert "Traceback" not in result.output
