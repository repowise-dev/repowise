"""``doctor`` actually reconciles the database against its two indexes.

The reconciliation read ``p.page_id`` off a ``Page``, whose primary key is
the column ``id``. It therefore raised on every run, was swallowed by the
handler below it, and reported "Store consistency: Could not check" — which
is recorded as a passing row. Both drift rows were missing from every
doctor run, and ``--repair`` never had anything to repair, because the sets
it repairs from were always empty.

The same defect was found and fixed in the FTS repair block below it. This
half was missed, so the tests pin both the reconciliation and the reason it
stayed invisible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks

PAGE_IN_BOTH = "file_page:kept.py"
PAGE_MISSING = "file_page:dropped.py"


async def _build_repo(tmp_path: Path) -> Path:
    """A repo whose database holds two pages and whose store holds one.

    The reconciliation only reports a missing page when the store is
    non-empty — an empty store reads as "not indexed yet", not as drift — so
    the kept page is what makes the missing one visible.
    """
    import git as gitpython

    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
    )
    from repowise.core.persistence.crud import upsert_page, upsert_repository
    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    # Resolved: on macOS tmp_path is a symlink and the command looks the
    # repository up by the path it resolves to.
    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir()

    engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
    await init_db(engine)
    sf = create_session_factory(engine)
    async with get_session(sf) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for page_id, path in ((PAGE_IN_BOTH, "kept.py"), (PAGE_MISSING, "dropped.py")):
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="file_page",
                title=f"File: {path}",
                content="Body.",
                summary="",
                target_path=path,
                source_hash="",
                model_name="mock",
                provider_name="mock",
            )
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    await fts.index(PAGE_IN_BOTH, "File: kept.py", "Body.", summary="", target_path="kept.py")
    await engine.dispose()

    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
    await store.embed_and_upsert(PAGE_IN_BOTH, "Body.", {"title": "File: kept.py"})
    await store.close()

    return repo_path


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_the_vector_drift_row_is_reported(tmp_path: Path) -> None:
    """One page in the database and not in the store is one page missing."""
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    assert "SQL ↔ Vector Store" in rows
    ok, detail = rows["SQL ↔ Vector Store"]
    assert ok is False
    assert detail == "1 missing, 0 orphaned"


def test_the_full_text_drift_row_is_reported(tmp_path: Path) -> None:
    rows = _rows(asyncio.run(_build_repo(tmp_path)))

    assert "SQL ↔ FTS Index" in rows
    ok, detail = rows["SQL ↔ FTS Index"]
    assert ok is False
    assert detail == "1 missing, 0 orphaned"


def test_a_failed_reconciliation_no_longer_passes_as_a_bare_note(tmp_path: Path) -> None:
    """The row that hid this is reported as OK, so it must at least say why.

    "Could not check" and "nothing to check" were indistinguishable, which is
    how an attribute error survived here across releases.
    """
    repo_path = asyncio.run(_build_repo(tmp_path))
    rows = _rows(repo_path)

    assert "Store consistency" not in rows

    # Force the reconciliation to fail and read what it says about it. The
    # break has to be inside this check and nowhere else — the earlier
    # database row shares most of the same calls, and failing that one first
    # means the reconciliation never runs at all.
    original = repo_checks._decision_vector_ids

    async def _boom(*_a: object, **_kw: object):
        raise RuntimeError("store unreadable")

    repo_checks._decision_vector_ids = _boom
    try:
        broken = _rows(repo_path)
    finally:
        repo_checks._decision_vector_ids = original

    assert "Store consistency" in broken
    _ok, detail = broken["Store consistency"]
    assert "RuntimeError" in detail
    assert "store unreadable" in detail
