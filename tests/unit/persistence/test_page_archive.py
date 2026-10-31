"""Retiring a live identity must not erase its prose, notes or version history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import networkx as nx
import pytest
from sqlalchemy import select

from repowise.core.generation.models import GenerationConfig
from repowise.core.persistence.models import Base, Page, PageVersion
from repowise.core.pipeline.persist import (
    _sweep_stale_generated_pages,
    sweep_absent_cycle_pages,
    sweep_absent_symbol_spotlights,
    sweep_deselected_file_keyed_pages,
    sweep_deselected_pages,
    sweep_expired_tombstones,
    sweep_retired_pages,
    sweep_superseded_generated_pages,
)
from tests.unit.persistence.helpers import insert_repo

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def _add_page(session, repo_id, page_id, *, members=(), notes="Original notes"):
    page_type, target = page_id.split(":", 1)
    session.add(
        Page(
            id=page_id,
            repository_id=repo_id,
            page_type=page_type,
            target_path=target,
            title="Original title",
            content="Unique original prose",
            summary="Original summary",
            digest="Original agent digest",
            source_hash="source",
            content_hash="subject",
            model_name="model",
            provider_name="template",
            input_tokens=31,
            output_tokens=47,
            cached_tokens=13,
            generation_level=3,
            version=2,
            confidence=0.8,
            metadata_json=json.dumps({"file_paths": list(members), "extra": "kept"}),
            human_notes=notes,
            pinned=True,
            parent_page_id="repo_overview:demo",
            display_order=7,
            section_number="2.7",
            structural_key="original-key",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await session.flush()
    session.add(
        PageVersion(
            page_id=page_id,
            repository_id=repo_id,
            version=1,
            page_type=page_type,
            title="Previous title",
            content="Unique previous prose",
            source_hash="previous-source",
            model_name="previous-model",
            provider_name="previous-provider",
            input_tokens=17,
            output_tokens=23,
            confidence=0.9,
            archived_at=NOW,
        )
    )
    await session.flush()


async def _original_record(session, model, predicate):
    row = (await session.execute(select(model.__table__).where(predicate))).mappings().one()
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in row.items()}


async def _archived(session, page_id):
    table = Base.metadata.tables.get("page_archive")
    assert table is not None, "Retired pages need a native archive, not lost history"
    return (await session.execute(select(table).where(table.c.page_id == page_id))).mappings().one()


async def test_native_page_pruning_preserves_complete_history(async_session):
    from repowise.core.pipeline.persist.prune import delete_pages

    repo = await insert_repo(async_session)
    page_id = "file_page:src/retired.py"
    await _add_page(async_session, repo.id, page_id)
    original = await _original_record(async_session, Page, Page.id == page_id)
    previous = await _original_record(async_session, PageVersion, PageVersion.page_id == page_id)

    await delete_pages(async_session, repo.id, [page_id])

    archived = await _archived(async_session, page_id)
    assert json.loads(archived["record_json"]) == original
    versions = Base.metadata.tables["page_version_archive"]
    rows = (
        await async_session.execute(select(versions).where(versions.c.archive_id == archived["id"]))
    ).mappings().all()
    assert [json.loads(row["record_json"]) for row in rows] == [previous]
    assert await async_session.get(Page, page_id) is None


@pytest.mark.parametrize(
    ("sweep", "page_id", "reason"),
    [
        ("retired", "architecture_diagram:old", "retired"),
        ("stale", "module_page:old", "stale_generation"),
        ("cycle", "scc_page:old", "cycle_absent"),
        ("symbol", "symbol_spotlight:src/demo.py::removed", "symbol_absent"),
        ("deselected", "file_page:tests/test_demo.py", "deselected"),
        ("file_keyed", "api_contract:tests/test_demo.py", "deselected"),
        ("expired", "file_page:src/removed.py", "expired_tombstone"),
        ("superseded", "module_page:old", "superseded"),
    ],
)
async def test_each_sweep_preserves_complete_retired_records(async_session, sweep, page_id, reason):
    session = async_session
    repo = await insert_repo(session)
    await _add_page(session, repo.id, page_id, members=["src/a.py"])
    if sweep == "expired":
        from datetime import timedelta

        from repowise.core.pipeline.persist import TOMBSTONE_RETENTION

        page = await session.get(Page, page_id)
        page.freshness_status = "tombstone"
        page.metadata_json = json.dumps(
            {"tombstoned_at": (NOW - TOMBSTONE_RETENTION - timedelta(days=1)).isoformat()}
        )
        await session.flush()
    original = await _original_record(session, Page, Page.id == page_id)
    previous = await _original_record(session, PageVersion, PageVersion.page_id == page_id)
    generated = [
        SimpleNamespace(
            page_type="module_page",
            page_id="module_page:new",
            metadata={"file_paths": ["src/a.py"]},
        )
    ]
    graph = nx.DiGraph()
    graph.add_node(
        "kept", node_type="symbol", kind="function", name="kept", file_path="src/demo.py"
    )
    builder = SimpleNamespace(
        graph=lambda: graph, strongly_connected_components=lambda: [], pagerank=lambda: {}
    )
    parsed = SimpleNamespace(
        file_info=SimpleNamespace(
            path="tests/test_demo.py",
            language="python",
            size_bytes=5000,
            is_test=True,
            is_api_contract=False,
            is_entry_point=False,
        ),
        symbols=[],
    )
    calls = {
        "retired": lambda: sweep_retired_pages(session, repo.id),
        "stale": lambda: _sweep_stale_generated_pages(session, repo.id, generated),
        "cycle": lambda: sweep_absent_cycle_pages(session, repo.id, builder),
        "symbol": lambda: sweep_absent_symbol_spotlights(session, repo.id, builder),
        "deselected": lambda: sweep_deselected_pages(
            session, repo.id, [parsed], builder, GenerationConfig()
        ),
        "file_keyed": lambda: sweep_deselected_file_keyed_pages(session, repo.id, [parsed]),
        "expired": lambda: sweep_expired_tombstones(session, repo.id, now=NOW),
        "superseded": lambda: sweep_superseded_generated_pages(session, repo.id, generated),
    }

    assert await calls[sweep]() == [page_id]
    assert (
        await session.execute(select(Page.id).where(Page.id == page_id))
    ).scalar_one_or_none() is None
    archived = await _archived(session, page_id)
    assert archived["reason"] == reason
    assert archived["repository_id"] == repo.id
    assert json.loads(archived["record_json"]) == original
    versions = Base.metadata.tables["page_version_archive"]
    rows = (
        (await session.execute(select(versions).where(versions.c.archive_id == archived["id"])))
        .mappings()
        .all()
    )
    assert [json.loads(row["record_json"]) for row in rows] == [previous]
    assert (
        await session.execute(select(PageVersion.id).where(PageVersion.page_id == page_id))
    ).all() == []


@pytest.mark.parametrize("tied", [False, True])
async def test_notes_follow_greatest_coverage_then_lexical_successor(async_session, tied):
    session = async_session
    repo = await insert_repo(session)
    members = ["src/a.py", "src/b.py", "src/c.py"]
    await _add_page(session, repo.id, "module_page:old", members=members, notes="Keep this insight")
    a_members = members[:2] if tied else members[:1]
    await _add_page(session, repo.id, "module_page:a", members=a_members, notes="Successor A notes")
    await _add_page(
        session, repo.id, "module_page:b", members=members[1:], notes="Successor B notes"
    )
    generated = [
        SimpleNamespace(
            page_type="module_page", page_id="module_page:b", metadata={"file_paths": members[1:]}
        ),
        SimpleNamespace(
            page_type="module_page", page_id="module_page:a", metadata={"file_paths": a_members}
        ),
    ]

    assert await sweep_superseded_generated_pages(session, repo.id, generated) == [
        "module_page:old"
    ]
    winner, loser = ("a", "b") if tied else ("b", "a")
    winning_notes = (
        await session.execute(select(Page.human_notes).where(Page.id == f"module_page:{winner}"))
    ).scalar_one()
    assert (
        "Keep this insight" in winning_notes
    ), "Regrouping must carry human notes to the best successor"
    assert "module_page:old" in winning_notes
    assert winning_notes.startswith(f"Successor {winner.upper()} notes")
    assert (
        await session.execute(select(Page.human_notes).where(Page.id == f"module_page:{loser}"))
    ).scalar_one() == f"Successor {loser.upper()} notes"
    assert (await _archived(session, "module_page:old"))["superseded_by"] == f"module_page:{winner}"


async def test_retention_keeps_boundary_recent_and_other_repository_events(async_session):
    from datetime import timedelta

    from repowise.core.persistence.crud import purge_retired_pages, retire_pages
    from repowise.core.persistence.models import PageArchive, PageVersionArchive

    session = async_session
    repo = await insert_repo(session)
    other = await insert_repo(session, name="other", local_path="/tmp/other")
    for owner, target, age in [
        (repo, "expired", 91),
        (repo, "boundary", 90),
        (repo, "recent", 89),
        (other, "foreign", 91),
    ]:
        page_id = f"module_page:{target}"
        await _add_page(session, owner.id, page_id)
        await retire_pages(session, owner.id, [page_id], "deselected")
        archive = (
            await session.execute(select(PageArchive).where(PageArchive.page_id == page_id))
        ).scalar_one()
        archive.retired_at = NOW - timedelta(days=age)
    await session.flush()

    assert await purge_retired_pages(session, repo.id, now=NOW) == 1
    remaining = (await session.execute(select(PageArchive.page_id))).scalars().all()
    assert set(remaining) == {"module_page:boundary", "module_page:recent", "module_page:foreign"}
    assert len((await session.execute(select(PageVersionArchive.id))).all()) == 3


async def test_reused_live_identity_does_not_inherit_retired_history(async_session):
    from repowise.core.persistence import crud

    session = async_session
    repo = await insert_repo(session)
    await _add_page(session, repo.id, "module_page:old")
    await crud.retire_pages(session, repo.id, ["module_page:old"], "deselected")
    await crud.upsert_page(
        session,
        repository_id=repo.id,
        page_id="module_page:old",
        page_type="module_page",
        target_path="old",
        title="Fresh identity",
        content="Fresh body",
        source_hash="fresh",
        model_name="new",
        provider_name="new",
    )
    assert (await crud.get_page(session, "module_page:old")).version == 1
    assert await crud.get_page_versions(session, "module_page:old") == []
    assert (
        json.loads((await _archived(session, "module_page:old"))["record_json"])["content"]
        == "Unique original prose"
    )


async def test_caller_rollback_keeps_live_data_and_removes_retirement(session_factory):
    from repowise.core.persistence import crud
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import PageArchive

    async with get_session(session_factory) as session:
        repo = await insert_repo(session)
        await _add_page(session, repo.id, "module_page:old")
    with pytest.raises(RuntimeError, match="abort persistence"):
        async with get_session(session_factory) as session:
            await crud.retire_pages(session, repo.id, ["module_page:old"], "deselected")
            raise RuntimeError("abort persistence")
    async with get_session(session_factory) as session:
        assert (await crud.get_page(session, "module_page:old")).content == "Unique original prose"
        assert len(await crud.get_page_versions(session, "module_page:old")) == 1
        assert (await session.execute(select(PageArchive.id))).all() == []


@pytest.mark.parametrize("target_exists_elsewhere", [False, True])
async def test_successor_relationship_requires_a_live_same_repository_target(
    async_session,
    target_exists_elsewhere,
):
    from repowise.core.persistence import crud

    session = async_session
    repo = await insert_repo(session)
    other = await insert_repo(session, name="other", local_path="/tmp/other")
    await _add_page(session, repo.id, "module_page:old")
    if target_exists_elsewhere:
        await _add_page(session, other.id, "module_page:target", notes="Foreign notes")
    await crud.retire_pages(
        session,
        repo.id,
        ["module_page:old"],
        "superseded",
        {"module_page:old": "module_page:target"},
    )
    assert (await _archived(session, "module_page:old"))["superseded_by"] is None
    if target_exists_elsewhere:
        assert (await crud.get_page(session, "module_page:target")).human_notes == "Foreign notes"
