"""Reliability contracts of the update persistence layer.

Pins the three PR-3 behaviors: the full-mode persist is one transaction (a
mid-persist failure rolls everything back instead of leaving a torn store),
lock acquisition is atomic under contention (exactly one winner), and the
page checkpointer degrades to a no-op instead of breaking generation.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from repowise.cli.commands.update_cmd.persistence import (
    PageCheckpointer,
    _persist_full_update_async,
)
from repowise.core.generation.models import GeneratedPage


def _page(page_id: str) -> GeneratedPage:
    now = datetime.now(UTC).isoformat()
    return GeneratedPage(
        page_id=page_id,
        page_type="file_page",
        title=page_id,
        content=f"# {page_id}\n",
        source_hash="deadbeef",
        model_name="mock-model",
        provider_name="mock",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        generation_level=1,
        target_path=page_id.split(":", 1)[-1],
        created_at=now,
        updated_at=now,
    )


class _BombPage:
    """A page whose first attribute access explodes mid-upsert."""

    def __getattr__(self, name: str):
        raise RuntimeError("torn mid-persist")


async def _count_pages(repo_path: Path) -> int:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            return len(list((await session.execute(select(Page))).scalars()))
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Torn persist rolls back atomically
# ---------------------------------------------------------------------------


def test_torn_full_persist_rolls_back_all_pages(tmp_path: Path) -> None:
    """Page upserts run in ONE transaction: if the third page fails, the two
    already-upserted pages must not survive as a torn half-persist."""
    (tmp_path / ".repowise").mkdir()

    with pytest.raises(RuntimeError, match="torn mid-persist"):
        asyncio.run(
            _persist_full_update_async(
                repo_path=tmp_path,
                repo_name="repo",
                generated_pages=[_page("file_page:a.py"), _page("file_page:b.py"), _BombPage()],
                file_diffs=[],
                git_meta_map={},
                new_decision_markers=[],
                decision_vector_store=None,
                provider=None,
                partial_health_report=None,
                dead_code_report=None,
                graph_builder=None,
                knowledge_graph_result=None,
                degraded=[],
            )
        )

    assert asyncio.run(_count_pages(tmp_path)) == 0


def test_full_persist_collects_degraded_steps(tmp_path: Path) -> None:
    """A best-effort step failure lands in the degraded list; pages commit."""
    (tmp_path / ".repowise").mkdir()
    degraded: list[str] = []

    asyncio.run(
        _persist_full_update_async(
            repo_path=tmp_path,
            repo_name="repo",
            generated_pages=[_page("file_page:a.py")],
            file_diffs=[],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            # persist_graph_nodes(None) raises -> degraded, not fatal.
            graph_builder=None,
            knowledge_graph_result=None,
            degraded=degraded,
        )
    )

    assert asyncio.run(_count_pages(tmp_path)) == 1
    assert any(entry.startswith("Graph nodes persist:") for entry in degraded)


def test_required_config_persist_rejects_any_degraded_dependency(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()

    with pytest.raises(RuntimeError, match="did not update every dependent store"):
        asyncio.run(
            _persist_full_update_async(
                repo_path=tmp_path,
                repo_name="repo",
                generated_pages=[_page("file_page:a.py")],
                file_diffs=[],
                git_meta_map={},
                new_decision_markers=[],
                decision_vector_store=None,
                provider=None,
                partial_health_report=None,
                dead_code_report=None,
                graph_builder=None,
                knowledge_graph_result=None,
                degraded=[],
                require_config_rebuild_success=True,
            )
        )

    assert asyncio.run(_count_pages(tmp_path)) == 0


def test_failed_fts_cleanup_debt_retries_on_next_persist(tmp_path: Path, monkeypatch) -> None:
    import repowise.core.persistence as persistence
    from repowise.core.pipeline.cleanup_debt import (
        load_cleanup_debt,
        record_cleanup_debt,
    )

    (tmp_path / ".repowise").mkdir()
    orphan = "file_page:removed.py"
    record_cleanup_debt(tmp_path, "fts", {orphan})
    attempts: list[list[str]] = []

    class _FailOnceFTS:
        async def ensure_index(self):
            return None

        async def index(self, *args, **kwargs):
            return None

        async def delete_many(self, page_ids):
            attempts.append(list(page_ids))
            if len(attempts) == 1:
                raise RuntimeError("fts unavailable")

    monkeypatch.setattr(persistence, "FullTextSearch", lambda _engine: _FailOnceFTS())

    async def _persist_once() -> None:
        await _persist_full_update_async(
            repo_path=tmp_path,
            repo_name="repo",
            generated_pages=[],
            file_diffs=[],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=None,
            knowledge_graph_result=None,
            degraded=[],
        )

    asyncio.run(_persist_once())
    assert load_cleanup_debt(tmp_path)["fts"] == {orphan}
    asyncio.run(_persist_once())
    assert attempts == [[orphan], [orphan]]
    assert load_cleanup_debt(tmp_path)["fts"] == set()


def test_required_git_decision_persist_failure_rolls_back(tmp_path: Path, monkeypatch) -> None:
    from dataclasses import dataclass

    import repowise.core.persistence.crud as crud

    (tmp_path / ".repowise").mkdir()

    @dataclass
    class _Decision:
        title: str = "Use durable retries"

    async def _boom(*args, **kwargs):
        raise RuntimeError("decision store down")

    monkeypatch.setattr(crud, "bulk_upsert_decisions", _boom)

    with pytest.raises(RuntimeError, match="decision store down"):
        asyncio.run(
            _persist_full_update_async(
                repo_path=tmp_path,
                repo_name="repo",
                generated_pages=[_page("file_page:a.py")],
                file_diffs=[],
                git_meta_map={},
                new_decision_markers=[_Decision()],
                decision_vector_store=None,
                provider=None,
                partial_health_report=None,
                dead_code_report=None,
                graph_builder=None,
                knowledge_graph_result=None,
                degraded=[],
                require_decision_persist_success=True,
            )
        )

    assert asyncio.run(_count_pages(tmp_path)) == 0


def test_missing_reingested_coverage_is_authoritative_empty(tmp_path: Path, monkeypatch) -> None:
    from repowise.cli.commands.update_cmd.persistence import _coverage_for_rescore

    monkeypatch.setattr(
        "repowise.core.repo_config.load_repo_config",
        lambda _path: {"coverage": {"reingest_on_update": True}},
    )
    monkeypatch.setattr(
        "repowise.core.analysis.health.coverage.discover_artifacts",
        lambda *_args, **_kwargs: [],
    )

    coverage_map, files, source_format, authoritative = asyncio.run(
        _coverage_for_rescore(object(), "repo", tmp_path, [])
    )

    assert coverage_map == {}
    assert files == []
    assert source_format is None
    assert authoritative is True


# ---------------------------------------------------------------------------
# A retirement has to reach an index nobody re-indexes
# ---------------------------------------------------------------------------


async def _seed_pages(repo_path: Path, repo_name: str, page_ids: list[str]) -> None:
    """Write page rows and their FTS entries the way an older release left them."""
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import (
        FullTextSearch,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.models import Page

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name=repo_name, local_path=str(repo_path))
            now = datetime.now(UTC)
            for page_id in page_ids:
                page_type, target = page_id.split(":", 1)
                session.add(
                    Page(
                        id=page_id,
                        repository_id=repo.id,
                        page_type=page_type,
                        title=target,
                        content=f"body of {target}",
                        target_path=target,
                        source_hash="x" * 64,
                        model_name="mock",
                        provider_name="mock",
                        created_at=now,
                        updated_at=now,
                    )
                )
        fts = FullTextSearch(engine)
        await fts.ensure_index()
        for page_id in page_ids:
            await fts.index(page_id, page_id, f"body of {page_id}")
    finally:
        await engine.dispose()


async def _page_ids(repo_path: Path) -> set[str]:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.models import Page

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            return set((await session.execute(select(Page.id))).scalars().all())
    finally:
        await engine.dispose()


async def _fts_ids(repo_path: Path) -> set[str]:
    import aiosqlite

    async with aiosqlite.connect(repo_path / ".repowise" / "wiki.db") as db:
        rows = await (await db.execute("SELECT page_id FROM page_fts")).fetchall()
    return {r[0] for r in rows}


def test_update_decay_paths_use_cascade_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Update must mark the structural dependents stale, not just file pages.

    This pins the new path that expands ``decay_paths`` through the existing
    cascade machinery and then writes the resulting stale ids.
    """

    (tmp_path / ".repowise").mkdir()

    async def _seed() -> None:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.models import Page

        engine = create_engine(get_db_url_for_repo(tmp_path))
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
                for page_id, page_type, target in [
                    ("file_page:src/a.py", "file_page", "src/a.py"),
                    ("module_page:pkg", "module_page", "pkg"),
                    ("scc_page:cycle", "scc_page", "cycle"),
                    ("repo_overview:repo", "repo_overview", "repo"),
                ]:
                    now = datetime.now(UTC)
                    session.add(
                        Page(
                            id=page_id,
                            repository_id=repo.id,
                            page_type=page_type,
                            title=target,
                            content="body",
                            target_path=target,
                            source_hash="x" * 64,
                            model_name="mock",
                            provider_name="mock",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    recorded: dict[str, object] = {}

    class _Deps:
        repo_wide_ids = ("repo_overview:repo",)

        def containers_of(self, path: str) -> set[str]:
            recorded["container_lookup"] = path
            return {"module_page:pkg", "scc_page:cycle"}

    from repowise.core.pipeline.scoped_generation import _load_page_rows as _real_load_page_rows

    async def _fake_load_page_rows(session, repo_id):
        return await _real_load_page_rows(session, repo_id)

    def _fake_build_dependencies(
        *, parsed_files, graph_builder, config, kg_ctx, records, repo_name
    ):
        recorded["build_dependencies"] = {
            "parsed_files": parsed_files,
            "repo_name": repo_name,
            "records": [r.page_id for r in records],
        }
        return _Deps()

    def _fake_expand_cascade(seed_ids, mode, deps):
        recorded["expand_cascade"] = {
            "seed_ids": set(seed_ids),
            "mode": mode,
            "repo_wide_ids": tuple(deps.repo_wide_ids),
        }

        class _Result:
            def __init__(self):
                self.stale_ids = {"module_page:pkg", "scc_page:cycle", "repo_overview:repo"}

        return _Result()

    async def _fake_mark_page_ids_stale(session, repo_id, page_ids):
        recorded["marked"] = set(page_ids)
        return len(recorded["marked"])

    monkeypatch.setattr(
        "repowise.core.pipeline.scoped_generation._load_page_rows", _fake_load_page_rows
    )
    monkeypatch.setattr(
        "repowise.core.generation.scope.build_dependencies", _fake_build_dependencies
    )
    monkeypatch.setattr("repowise.core.generation.cascade.expand_cascade", _fake_expand_cascade)
    monkeypatch.setattr(
        "repowise.core.pipeline.persist.mark_page_ids_stale", _fake_mark_page_ids_stale
    )
    monkeypatch.setattr("repowise.core.repo_config.load_repo_config", lambda _path: {})
    monkeypatch.setattr(
        "repowise.core.generation.GenerationConfig.from_repo_config", lambda _cfg: object()
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.scoped_generation.load_kg_context", lambda _path: object()
    )

    asyncio.run(
        _persist_full_update_async(
            repo_path=tmp_path,
            repo_name="repo",
            # The SCC was recovered deterministically in this same update. It
            # appears in the cascade's stale dependents, but the final decay
            # must not undo the fresh upsert.
            generated_pages=[_page("file_page:a.py"), _page("scc_page:cycle")],
            file_diffs=[],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=type("_G", (), {})(),
            knowledge_graph_result=None,
            degraded=[],
            decay_paths=["src/a.py"],
            parsed_files=[],
        )
    )

    assert recorded["expand_cascade"]["seed_ids"] == {"file_page:src/a.py"}
    assert recorded["expand_cascade"]["mode"] == "none"
    assert recorded["marked"] == {
        "module_page:pkg",
        "repo_overview:repo",
        "file_page:src/a.py",
    }


def test_update_decay_paths_leaves_seed_file_page_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decayed file page must itself end up 'stale' after update.

    Regression test: expand_cascade's mode="none" deliberately excludes the
    seeds from stale_ids (they're the pages "generate" was asked for), but on
    this path nothing regenerates them either. The persistence call has to
    union the seeds back in, or the decayed file page is neither regenerated
    nor marked stale -- invisible to doctor/get_stale_pages/generate --stale,
    which is the exact bug #2099 was filed for.
    """

    (tmp_path / ".repowise").mkdir()

    async def _seed() -> None:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
            init_db,
            upsert_repository,
        )
        from repowise.core.persistence.models import Page

        engine = create_engine(get_db_url_for_repo(tmp_path))
        try:
            await init_db(engine)
            async with get_session(create_session_factory(engine)) as session:
                repo = await upsert_repository(session, name="repo", local_path=str(tmp_path))
                for page_id, page_type, target in [
                    ("file_page:src/a.py", "file_page", "src/a.py"),
                    ("module_page:pkg", "module_page", "pkg"),
                ]:
                    now = datetime.now(UTC)
                    session.add(
                        Page(
                            id=page_id,
                            repository_id=repo.id,
                            page_type=page_type,
                            title=target,
                            content="body",
                            target_path=target,
                            source_hash="x" * 64,
                            model_name="mock",
                            provider_name="mock",
                            freshness_status="fresh",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    class _Deps:
        repo_wide_ids: tuple[str, ...] = ()

        def containers_of(self, path: str) -> set[str]:
            return {"module_page:pkg"}

    monkeypatch.setattr(
        "repowise.core.generation.scope.build_dependencies",
        lambda **kwargs: _Deps(),
    )
    monkeypatch.setattr("repowise.core.repo_config.load_repo_config", lambda _path: {})
    monkeypatch.setattr(
        "repowise.core.generation.GenerationConfig.from_repo_config", lambda _cfg: object()
    )
    monkeypatch.setattr(
        "repowise.core.pipeline.scoped_generation.load_kg_context", lambda _path: object()
    )

    asyncio.run(
        _persist_full_update_async(
            repo_path=tmp_path,
            repo_name="repo",
            generated_pages=[_page("file_page:a.py")],
            file_diffs=[],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=type("_G", (), {})(),
            knowledge_graph_result=None,
            degraded=[],
            decay_paths=["src/a.py"],
            parsed_files=[],
        )
    )

    from repowise.core.persistence.models import Page

    async def _statuses() -> dict[str, str]:
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import create_engine, create_session_factory, get_session

        engine = create_engine(get_db_url_for_repo(tmp_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = (await session.execute(select(Page))).scalars()
                return {r.id: r.freshness_status for r in rows}
        finally:
            await engine.dispose()

    statuses = asyncio.run(_statuses())
    # The bug this pins: without the seed union, file_page:src/a.py stays
    # "fresh" forever -- not regenerated (mode="none" on this path never
    # regenerates) and not marked stale either.
    assert statuses["file_page:src/a.py"] == "stale"
    assert statuses["module_page:pkg"] == "stale"


def test_update_sweeps_pages_retired_since_the_index_was_built(tmp_path: Path) -> None:
    """The contract that makes a retirement reach an existing user.

    An update runs ``file_pages_only``, so the generation ladder returns before
    the repo-wide levels and never visits an onboarding row to notice it should
    not exist. If the sweep is not called here, a user who never re-indexes is
    served the retired pages forever — which is the state this whole retirement
    mechanism exists to prevent.
    """
    from repowise.core.generation.page_redirects import RETIRED_IDS

    (tmp_path / ".repowise").mkdir()
    retired = sorted(RETIRED_IDS)
    assert retired, "no retired ids to exercise"
    survivor = "onboarding:onboarding/key_concepts"
    asyncio.run(_seed_pages(tmp_path, "repo", [*retired, survivor]))

    asyncio.run(
        _persist_full_update_async(
            repo_path=tmp_path,
            repo_name="repo",
            generated_pages=[_page("file_page:a.py")],
            file_diffs=[],
            git_meta_map={},
            new_decision_markers=[],
            decision_vector_store=None,
            provider=None,
            partial_health_report=None,
            dead_code_report=None,
            graph_builder=None,
            knowledge_graph_result=None,
            degraded=[],
        )
    )

    remaining = asyncio.run(_page_ids(tmp_path))
    assert not remaining & set(retired)
    # The survivor shares page_type='onboarding' with all three. Losing it here
    # would mean the sweep is matching on type, and the whole orientation
    # collection would go with the retirement.
    assert survivor in remaining

    # A row deleted from `pages` but left in FTS still answers search in full,
    # hydrated from the FTS copy, pointing at a page that now 404s.
    fts_left = asyncio.run(_fts_ids(tmp_path))
    assert not fts_left & set(retired)
    assert survivor in fts_left


# ---------------------------------------------------------------------------
# Lock contention: exactly one winner
# ---------------------------------------------------------------------------


def test_concurrent_acquire_has_exactly_one_winner(tmp_path: Path) -> None:
    from repowise.core.update_lock import try_acquire_update_lock

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: try_acquire_update_lock(tmp_path, f"c{i}"), range(8)))

    winners = [r for r in results if r is None]
    losers = [r for r in results if r is not None]
    assert len(winners) == 1
    # Every loser saw the winner's live payload, not a half-written file.
    assert all(loser.get("pid") for loser in losers)


# ---------------------------------------------------------------------------
# PageCheckpointer
# ---------------------------------------------------------------------------


def test_checkpointer_persists_pages_as_they_land(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()

    async def _run() -> PageCheckpointer:
        # Real schema, like the update path (init created it long before).
        from repowise.cli.helpers import get_db_url_for_repo
        from repowise.core.persistence import create_engine, init_db

        engine = create_engine(get_db_url_for_repo(tmp_path))
        await init_db(engine)
        await engine.dispose()

        cp = PageCheckpointer(tmp_path, "repo")
        await cp.start()
        cp.on_page_ready(_page("file_page:a.py"))
        cp.on_page_ready(_page("file_page:b.py"))
        await cp.close()
        return cp

    cp = asyncio.run(_run())
    assert cp.failure is None
    assert cp.persisted == 2
    assert asyncio.run(_count_pages(tmp_path)) == 2


def test_checkpointer_failure_degrades_without_hanging(tmp_path: Path) -> None:
    """No schema (init_db never ran here): the first write fails, the sink
    flips off, and close() still returns promptly."""
    (tmp_path / ".repowise").mkdir()

    async def _run() -> PageCheckpointer:
        cp = PageCheckpointer(tmp_path, "repo")
        await cp.start()
        cp.on_page_ready(_page("file_page:a.py"))
        cp.on_page_ready(_page("file_page:b.py"))
        await cp.close()
        return cp

    cp = asyncio.run(asyncio.wait_for(_run(), timeout=30))
    assert cp.failure is not None
    assert cp.persisted == 0
