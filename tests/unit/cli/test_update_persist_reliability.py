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


# ---------------------------------------------------------------------------
# Lock contention: exactly one winner
# ---------------------------------------------------------------------------


def test_concurrent_acquire_has_exactly_one_winner(tmp_path: Path) -> None:
    from repowise.core.update_lock import try_acquire_update_lock

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda i: try_acquire_update_lock(tmp_path, f"c{i}"), range(8))
        )

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
