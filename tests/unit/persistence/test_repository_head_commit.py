"""upsert_repository records the git HEAD it was indexed against (freshness).

Regression for the bug where ``Repository.head_commit`` was never written, so
the MCP ``_meta`` HEAD-vs-index staleness comparison silently never fired and
agents fell back to the stale CLAUDE.md stamp.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
)
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.crud.repository import _read_head_commit


def _make_git_checkout(root: Path, sha: str, *, branch: str = "main") -> None:
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    (git / "refs" / "heads" / branch).write_text(sha + "\n", encoding="utf-8")


def test_read_head_commit_resolves_ref(tmp_path: Path) -> None:
    sha = "a" * 40
    _make_git_checkout(tmp_path, sha)
    assert _read_head_commit(str(tmp_path)) == sha


def test_read_head_commit_detached(tmp_path: Path) -> None:
    sha = "b" * 40
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(sha + "\n", encoding="utf-8")
    assert _read_head_commit(str(tmp_path)) == sha


def test_read_head_commit_packed_refs(tmp_path: Path) -> None:
    sha = "c" * 40
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{sha} refs/heads/main\n",
        encoding="utf-8",
    )
    assert _read_head_commit(str(tmp_path)) == sha


def test_read_head_commit_non_git_returns_none(tmp_path: Path) -> None:
    assert _read_head_commit(str(tmp_path)) is None


def test_read_head_commit_unresolvable_ref_returns_none(tmp_path: Path) -> None:
    # HEAD points at a ref with no loose file and no packed-refs entry.
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert _read_head_commit(str(tmp_path)) is None


def test_read_head_commit_worktree_missing_gitdir_returns_none(tmp_path: Path) -> None:
    # A pointer at a directory that is not there is not a checkout.
    (tmp_path / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n", encoding="utf-8")
    assert _read_head_commit(str(tmp_path)) is None


def test_read_head_commit_garbled_gitfile_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("not a gitdir\n", encoding="utf-8")
    assert _read_head_commit(str(tmp_path)) is None


_DECOY = "d" * 40


def linked_worktree(root: Path, sha: str, *, how: str, relative: bool = False) -> Path:
    """A hand-built linked worktree. Returns the linked checkout.

    ``<root>/main/.git/worktrees/wt`` has its own ``HEAD`` and a ``commondir``
    of ``../..``. The branch ref lives in the common dir (loose or packed).
    A different SHA sitting in the worktree gitdir's own ``refs/`` must be
    ignored: symbolic refs resolve against the common dir, not the private one.
    """
    main = root / "main"
    common = main / ".git"
    wt_git = common / "worktrees" / "wt"
    wt_git.mkdir(parents=True)
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    (wt_git / "refs" / "heads").mkdir(parents=True)
    (wt_git / "refs" / "heads" / "feat").write_text(_DECOY + "\n", encoding="utf-8")

    if how == "detached":
        (wt_git / "HEAD").write_text(sha + "\n", encoding="utf-8")
    else:
        (wt_git / "HEAD").write_text("ref: refs/heads/feat\n", encoding="utf-8")
        if how == "loose":
            ref = common / "refs" / "heads"
            ref.mkdir(parents=True)
            (ref / "feat").write_text(sha + "\n", encoding="utf-8")
            # Packed copy of a different SHA: the loose ref wins.
            (common / "packed-refs").write_text(
                f"# pack-refs with: peeled fully-peeled sorted\n{_DECOY} refs/heads/feat\n",
                encoding="utf-8",
            )
        elif how == "packed":
            wrong = "e" * 40
            (common / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n"
                f"# {wrong} refs/heads/feat\n"
                f"^{wrong} refs/heads/feat\n"
                f"{sha} refs/heads/feat\n"
                f"^{sha}\n",
                encoding="utf-8",
            )
        else:
            raise ValueError(how)

    checkout = root / "wt"
    checkout.mkdir()
    pointer = "../main/.git/worktrees/wt" if relative else str(wt_git)
    (checkout / ".git").write_text(f"gitdir: {pointer}\n", encoding="utf-8")
    return checkout


def submodule_checkout(root: Path, sha: str) -> Path:
    """A submodule: ``gitdir:`` is relative and there is no ``commondir``."""
    modules = root / "main" / ".git" / "modules" / "libs" / "sub"
    (modules / "refs" / "heads").mkdir(parents=True)
    (modules / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (modules / "refs" / "heads" / "main").write_text(sha + "\n", encoding="utf-8")
    checkout = root / "main" / "libs" / "sub"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: ../../.git/modules/libs/sub\n", encoding="utf-8")
    return checkout


def test_read_head_commit_worktree_loose_ref(tmp_path: Path) -> None:
    sha = "a" * 40
    checkout = linked_worktree(tmp_path, sha, how="loose")
    assert _read_head_commit(str(checkout)) == sha


def test_read_head_commit_worktree_packed_refs(tmp_path: Path) -> None:
    sha = "c" * 40
    checkout = linked_worktree(tmp_path, sha, how="packed")
    assert _read_head_commit(str(checkout)) == sha


def test_read_head_commit_worktree_detached(tmp_path: Path) -> None:
    sha = "b" * 40
    checkout = linked_worktree(tmp_path, sha, how="detached")
    assert _read_head_commit(str(checkout)) == sha


def test_read_head_commit_worktree_relative_gitdir(tmp_path: Path) -> None:
    sha = "f" * 40
    checkout = linked_worktree(tmp_path, sha, how="loose", relative=True)
    assert _read_head_commit(str(checkout)) == sha


def test_read_head_commit_submodule_gitdir(tmp_path: Path) -> None:
    sha = "9" * 40
    checkout = submodule_checkout(tmp_path, sha)
    assert _read_head_commit(str(checkout)) == sha


@pytest.mark.asyncio
async def test_upsert_repository_stamps_and_advances_head(tmp_path: Path) -> None:
    sha1 = "1" * 40
    _make_git_checkout(tmp_path, sha1)

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=tmp_path.name, local_path=str(tmp_path))
            assert repo.head_commit == sha1

        # HEAD moves; a re-index advances the stored commit.
        sha2 = "2" * 40
        (tmp_path / ".git" / "refs" / "heads" / "main").write_text(sha2 + "\n", encoding="utf-8")
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=tmp_path.name, local_path=str(tmp_path))
            assert repo.head_commit == sha2

        # An explicit override wins over the on-disk read.
        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=tmp_path.name,
                local_path=str(tmp_path),
                head_commit="3" * 40,
            )
            assert repo.head_commit == "3" * 40
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_repository_keeps_head_when_path_not_git(tmp_path: Path) -> None:
    """A later upsert from a non-checkout path must not blank a known commit."""
    sha = "d" * 40
    _make_git_checkout(tmp_path, sha)

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
            assert repo.head_commit == sha

        # Same DB row keyed by local_path, but pretend the .git vanished.
        (tmp_path / ".git" / "HEAD").unlink()
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
            assert repo.head_commit == sha  # preserved, not nulled
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_repository_advances_worktree_head(tmp_path: Path) -> None:
    """A linked worktree's own commit is stored, and a later miss does not blank it."""
    sha1 = "a" * 40
    checkout = linked_worktree(tmp_path, sha1, how="loose")

    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    sf = create_session_factory(engine)
    try:
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="wt", local_path=str(checkout))
            assert repo.head_commit == sha1

        sha2 = "b" * 40
        (tmp_path / "main" / ".git" / "refs" / "heads" / "feat").write_text(
            sha2 + "\n", encoding="utf-8"
        )
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="wt", local_path=str(checkout))
            assert repo.head_commit == sha2

        (checkout / ".git").write_text("not a gitdir\n", encoding="utf-8")
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="wt", local_path=str(checkout))
            assert repo.head_commit == sha2
    finally:
        await engine.dispose()
