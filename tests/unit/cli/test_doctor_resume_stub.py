"""``doctor`` does not report a stub awaiting ``--resume`` as vector drift.

A stub standing in for a failed model page is held out of the vector store on
purpose: the resume seed reads the store back as the ledger of pages already
written, so a vector here is what would tell the next ``init --resume`` there
is nothing left to write. Reporting it as missing sent people to ``--repair``,
which embedded the stub and silently burned the retry.

FTS still indexes the stub, so the exclusion is vector-side only, and MISSING
only — a leftover vector for a page that has since become a stub is real drift.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repowise.cli.commands.doctor_cmd import repo_checks
from repowise.core.generation.models import STUB_FALLBACK_ERROR

WRITTEN = "module_page:src/written"
STUB = "module_page:src/stub"

BODY = (
    "## Overview\n\n"
    "Resolves each import against the package manifest before descending, so "
    "a symlinked workspace member is visited once rather than once per alias "
    "that reaches it. Cycles break on the real path, never the alias, which "
    "is why two aliases of one directory cannot both claim to own a module. "
    "The manifest is read once per package and cached for the walk.\n"
)


async def _build_repo(tmp_path: Path, *, embed_stub: bool) -> Path:
    """Both pages in SQL and FTS; the stub's vector is present only on request."""
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
        for page_id, path, meta in (
            (WRITTEN, "src/written", {}),
            (STUB, "src/stub", {STUB_FALLBACK_ERROR: "artifact check rejected the response"}),
        ):
            await upsert_page(
                session,
                page_id=page_id,
                repository_id=repo.id,
                page_type="module_page",
                title=f"Module: {path}",
                content=BODY,
                summary="",
                target_path=path,
                source_hash="",
                model_name="mock",
                provider_name="mock",
                metadata=meta,
            )
        await session.commit()

    fts = FullTextSearch(engine)
    await fts.ensure_index()
    for page_id, path in ((WRITTEN, "src/written"), (STUB, "src/stub")):
        await fts.index(page_id, f"Module: {path}", BODY, summary="", target_path=path)
    await engine.dispose()

    store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
    await store.embed_and_upsert(WRITTEN, BODY, {"title": "Module: src/written"})
    if embed_stub:
        await store.embed_and_upsert(STUB, BODY, {"title": "Module: src/stub"})
    await store.close()

    return repo_path


def _rows(repo_path: Path) -> dict[str, tuple[bool, str]]:
    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    return {c.name: (c.ok, c.detail) for c in checks}


def test_an_unembedded_stub_is_not_vector_drift(tmp_path: Path) -> None:
    rows = _rows(asyncio.run(_build_repo(tmp_path, embed_stub=False)))

    ok, detail = rows["SQL ↔ Vector Store"]
    assert ok, detail
    assert "missing" not in detail
    # Not drift, but not nothing either: the wiki has a page there that no
    # model wrote, and "in sync" alone would imply the wiki is complete.
    #
    # The command is named, not just the flag: `--resume` exists only on `init`,
    # so a bare "--resume" sends the reader to try it on `generate` first.
    assert "1 stub(s) awaiting `repowise init --resume`" in detail


def test_the_stub_is_still_indexed_for_full_text(tmp_path: Path) -> None:
    """The exclusion is vector-side only — FTS carries the stub like any page."""
    rows = _rows(asyncio.run(_build_repo(tmp_path, embed_stub=False)))

    assert rows["SQL ↔ FTS Index"] == (True, "in sync")


def test_an_embedded_stub_is_not_reported_as_an_orphan(tmp_path: Path) -> None:
    """A stub that does have a vector is a legitimate SQL row, not an orphan.

    ``--repair`` may already have embedded one before this exclusion existed,
    and that store should read as consistent rather than flipping to the other
    kind of drift.
    """
    rows = _rows(asyncio.run(_build_repo(tmp_path, embed_stub=True)))

    ok, detail = rows["SQL ↔ Vector Store"]
    assert ok, detail
    assert "orphaned" not in detail
