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
from repowise.cli.commands.doctor_cmd._checks import stores

PAGE_IN_BOTH = "file_page:kept.py"
PAGE_MISSING = "file_page:dropped.py"


async def _build_repo(tmp_path: Path) -> Path:
    """A repo whose database holds two pages and whose store holds one."""
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
    original = stores._decision_vector_ids

    async def _boom(*_a: object, **_kw: object):
        raise RuntimeError("store unreadable")

    stores._decision_vector_ids = _boom
    try:
        broken = _rows(repo_path)
    finally:
        stores._decision_vector_ids = original

    assert "Store consistency" in broken
    _ok, detail = broken["Store consistency"]
    assert "RuntimeError" in detail
    assert "store unreadable" in detail


def test_an_unopenable_vector_store_is_a_failing_row(tmp_path: Path, monkeypatch) -> None:
    """It used to read as an empty store, and so as "in sync"."""
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    repo_path = asyncio.run(_build_repo(tmp_path))

    async def _boom(self):
        raise RuntimeError("LanceDB is missing or broken (AttributeError: connect_async)")

    monkeypatch.setattr(LanceDBVectorStore, "_ensure_connected", _boom)
    rows = _rows(repo_path)

    assert "Vector store" in rows
    ok, detail = rows["Vector store"]
    assert ok is False
    assert "connect_async" in detail
    assert "repowise reindex" in detail
    assert rows["SQL ↔ Vector Store"] != (True, "in sync")


def test_an_empty_vector_store_is_not_in_sync(tmp_path: Path) -> None:
    import shutil

    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = asyncio.run(_build_repo(tmp_path))
    lance_dir = repo_path / ".repowise" / "lancedb"
    shutil.rmtree(lance_dir)

    async def _empty_table():
        store = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())
        await store._ensure_connected()
        await store.close()

    lance_dir.mkdir()
    asyncio.run(_empty_table())
    ok, detail = _rows(repo_path)["SQL ↔ Vector Store"]

    assert ok is False
    assert detail == "2 missing, 0 orphaned"


def test_repair_does_not_reembed_a_whole_wiki_on_a_paid_embedder(tmp_path: Path, monkeypatch) -> None:
    """An empty store is a whole reindex; doctor points there instead of spending."""
    import shutil

    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = asyncio.run(_build_repo(tmp_path))
    lance_dir = repo_path / ".repowise" / "lancedb"
    shutil.rmtree(lance_dir)
    lance_dir.mkdir()

    async def _empty_table():
        store = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())
        await store._ensure_connected()
        await store.close()

    asyncio.run(_empty_table())
    embedded: list[str] = []

    async def _record(self, page_id, *_a, **_k):
        embedded.append(page_id)

    monkeypatch.setattr(LanceDBVectorStore, "embed_and_upsert", _record)
    monkeypatch.setattr(
        "repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "openai"
    )
    monkeypatch.setattr(
        "repowise.cli.providers.build_embedder", lambda _n, _p=None: MockEmbedder()
    )
    printed: list[str] = []
    monkeypatch.setattr(
        repo_checks.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a)))
    )

    repo_checks._run_repo_checks(repo_path, repair=True)

    assert embedded == []
    out = " ".join(" ".join(printed).split())
    assert "2 page(s) need embedding with openai" in out
    assert "repowise reindex" in out


def test_missing_lancedb_with_real_embedder_reports_missing_and_reindex_fix(
    tmp_path: Path, monkeypatch
) -> None:
    """When .repowise/lancedb is absent and a non-mock embedder is configured,
    all indexable pages are reported missing with fix `repowise reindex`."""
    import shutil

    repo_path = asyncio.run(_build_repo(tmp_path))
    shutil.rmtree(repo_path / ".repowise" / "lancedb")
    (repo_path / ".repowise" / "config.yaml").write_text(
        "embedder: openai\n", encoding="utf-8"
    )

    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    by_id = {c.id: c for c in checks}

    vec = by_id["vector-store-sync"]
    assert vec.ok is False
    assert vec.detail == "2 missing, 0 orphaned"
    assert vec.fix == "repowise reindex"

    coord = by_id["coordinator-drift"]
    assert coord.ok is False
    assert coord.detail == "SQL=2, Vector=0, Drift=100.0%"
    assert coord.fix == "repowise reindex"


def test_missing_lancedb_with_mock_embedder_stays_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """Fast/keyless mode (mock embedder) without .repowise/lancedb stays in sync."""
    import shutil

    repo_path = asyncio.run(_build_repo(tmp_path))
    shutil.rmtree(repo_path / ".repowise" / "lancedb")
    monkeypatch.setattr(
        "repowise.cli.providers.resolve_embedder_for_repo", lambda _p: "mock"
    )

    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    by_id = {c.id: c for c in checks}

    vec = by_id["vector-store-sync"]
    assert vec.ok is True
    assert vec.detail == "in sync"
    assert vec.fix is None

    coord = by_id["coordinator-drift"]
    assert coord.ok is True
    assert coord.detail == "SQL=2, Vector=unknown, Drift=N/A"


def test_missing_lancedb_with_real_embedder_and_no_indexable_pages_stays_ok(
    tmp_path: Path,
) -> None:
    """When no indexable pages exist (e.g. excluded),
    a missing lancedb with a real embedder stays OK."""
    import shutil

    repo_path = asyncio.run(_build_repo(tmp_path))
    shutil.rmtree(repo_path / ".repowise" / "lancedb")
    (repo_path / ".repowise" / "config.yaml").write_text(
        "embedder: openai\nexclude_patterns:\n  - '*.py'\n", encoding="utf-8"
    )

    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    by_id = {c.id: c for c in checks}

    vec = by_id["vector-store-sync"]
    assert vec.ok is True
    assert vec.detail == "in sync"
    assert vec.fix is None
    assert by_id["coordinator-drift"].ok is True


def test_coordinator_drift_counts_decisions_with_live_vector_store(
    tmp_path: Path, monkeypatch
) -> None:
    """When a live LanceDB store holds decision embeddings, _decision_aware_drift
    counts them on the SQL side (including when list_page_ids raises)."""
    from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import get_repository_by_path
    from repowise.core.persistence.models import DecisionRecord
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    repo_path = asyncio.run(_build_repo(tmp_path))
    repowise_dir = repo_path / ".repowise"

    async def _seed_decision_and_missing_page() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            rec = DecisionRecord(
                repository_id=repo.id, title="Use SQLite", decision="because local"
            )
            session.add(rec)
            await session.commit()
            dec_vid = f"{DECISION_VECTOR_PREFIX}{rec.id}"
        await engine.dispose()

        store = LanceDBVectorStore(str(repowise_dir / "lancedb"), embedder=MockEmbedder())
        await store.embed_and_upsert(PAGE_MISSING, "Body.", {"title": "File: dropped.py"})
        await store.embed_and_upsert(dec_vid, "because local", {"title": "Use SQLite"})
        await store.close()

    asyncio.run(_seed_decision_and_missing_page())

    _all_ok, checks = repo_checks._run_repo_checks(repo_path, repair=False)
    by_id = {c.id: c for c in checks}
    assert by_id["coordinator-drift"].ok is True
    assert by_id["coordinator-drift"].detail == "SQL=3, Vector=3, Drift=0.0%"

    async def _check_fallback_and_missing() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{repowise_dir / 'wiki.db'}")
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            res_present = {"sql_pages": 2, "vector_count": 3, "drift": 0.5}
            await stores._decision_aware_drift(session, repo_path, res_present, object())
            assert res_present == {"sql_pages": 3, "vector_count": 3, "drift": 0.0}

            res_missing = {"sql_pages": 2, "vector_count": 0, "drift": 1.0}
            await stores._decision_aware_drift(session, repo_path, res_missing, None)
            assert res_missing == {"sql_pages": 2, "vector_count": 0, "drift": 1.0}
        await engine.dispose()

    asyncio.run(_check_fallback_and_missing())

