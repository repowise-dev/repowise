"""``load_stored_git_meta`` reads the store without creating one, and says so
when it could not read it at all.

``None`` (could not read) and ``{}`` (read fine, no rows) drive different
behavior in the caller, so conflating them is the failure mode this file
exists to prevent.
"""

from __future__ import annotations

from repowise.core.pipeline.incremental import load_stored_git_meta


async def test_a_repo_without_a_store_yields_nothing_and_creates_nothing(tmp_path):
    """Reading must never conjure an empty database.

    A repo whose ``wiki.db`` was deleted to force a rebuild would otherwise be
    handed an empty one here, and an empty database reads as "indexed"
    downstream. The same guard, for the same reason, sits on the head-commit
    stamper and the workspace updater.
    """
    assert await load_stored_git_meta(tmp_path) is None
    assert not (tmp_path / ".repowise" / "wiki.db").exists()
    assert not (tmp_path / ".repowise").exists()


async def test_an_unreadable_store_is_unknown_not_empty(tmp_path):
    """Best-effort, but the failure has to stay distinguishable.

    ``None`` means "could not read", and the caller then narrows what the
    update may overwrite. Returning ``{}`` would instead assert "this
    repository has no git history", which is the one answer that makes the
    caller rewrite the whole index with verdicts scored against nothing.
    """
    store = tmp_path / ".repowise"
    store.mkdir()
    (store / "wiki.db").write_bytes(b"this is not a sqlite database")

    assert await load_stored_git_meta(tmp_path) is None
