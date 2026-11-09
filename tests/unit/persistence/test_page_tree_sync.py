"""Rebuilding the stored tree, which is what keeps updates from flattening it.

An incremental update regenerates a handful of pages and persists them. Those
pages carry whatever placement the generator could work out from the few pages
it had in hand, which is not the tree. The rebuild recomputes placement from
the complete row set afterwards, so an update leaves the tree correct rather
than a little flatter each time.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import upsert_page
from repowise.core.persistence.models import Page
from repowise.core.pipeline.page_tree_sync import rebuild_page_tree
from tests.unit.persistence.helpers import insert_repo


async def _add(session, repo_id, page_type, target, **metadata):
    await upsert_page(
        session,
        page_id=f"{page_type}:{target}",
        repository_id=repo_id,
        page_type=page_type,
        title=target,
        content=f"body of {target}",
        target_path=target,
        source_hash="h" * 64,
        model_name="m",
        provider_name="template",
        metadata=metadata or {},
    )


async def _placement(session, repo_id):
    rows = await session.execute(
        select(Page.id, Page.parent_page_id, Page.display_order, Page.section_number).where(
            Page.repository_id == repo_id
        )
    )
    return {pid: (parent, order, section) for pid, parent, order, section in rows.tuples().all()}


@pytest.fixture
async def wiki(async_session):
    """A small persisted wiki: overview, one module, two files."""
    repo = await insert_repo(async_session)
    rid = repo.id
    await _add(async_session, rid, "repo_overview", "demo")
    await _add(
        async_session,
        rid,
        "module_page",
        "src/ingest",
        file_paths=["src/ingest/a.py", "src/ingest/b.py"],
    )
    await _add(async_session, rid, "file_page", "src/ingest/a.py", layer_id="layer:service")
    await _add(async_session, rid, "file_page", "src/ingest/b.py", layer_id="layer:service")
    await async_session.commit()
    await rebuild_page_tree(async_session, rid)
    await async_session.commit()
    return rid


class TestRebuild:
    async def test_it_places_pages(self, async_session, wiki):
        placed = await _placement(async_session, wiki)
        assert placed["file_page:src/ingest/a.py"][0] == "module_page:src/ingest"
        assert placed["module_page:src/ingest"][0] == "repo_overview:demo"
        assert placed["repo_overview:demo"][0] is None

    async def test_a_second_rebuild_changes_nothing(self, async_session, wiki):
        before = await _placement(async_session, wiki)
        changed = await rebuild_page_tree(async_session, wiki)
        await async_session.commit()
        assert changed == 0
        assert await _placement(async_session, wiki) == before


class TestLayerMetadata:
    @pytest.mark.parametrize("page_type", ["module_page", "scc_page"])
    @pytest.mark.parametrize(
        "stored_layer",
        [
            {},
            {"layer_id": "layer:api", "layer_name": "API"},
            {"layer_id": "layer:service", "layer_name": "Old service name"},
        ],
        ids=["missing", "stale-layer", "stale-name"],
    )
    async def test_layer_only_changes_are_persisted_once(
        self, async_session, wiki, page_type, stored_layer
    ):
        paths = ["src/ingest/a.py", "src/ingest/b.py"]
        for path in paths:
            file = await async_session.get(Page, f"file_page:{path}")
            file_metadata = json.loads(file.metadata_json)
            file_metadata["layer_name"] = "Services"
            file.metadata_json = json.dumps(file_metadata)
        if page_type == "scc_page":
            await _add(async_session, wiki, page_type, "cycle", files=paths)
        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        target = "src/ingest" if page_type == "module_page" else "cycle"
        page = await async_session.get(Page, f"{page_type}:{target}")
        metadata = {
            "file_paths": paths,
            "concept_order": 4,
            "custom": {"labels": ["keep", 7], "enabled": True},
            **stored_layer,
        }
        page.metadata_json = json.dumps(metadata)
        await async_session.commit()
        placement = await _placement(async_session, wiki)

        changed = await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        assert changed == 1
        assert await _placement(async_session, wiki) == placement
        stored = await async_session.scalar(select(Page.metadata_json).where(Page.id == page.id))
        assert json.loads(stored) == {
            **metadata,
            "layer_id": "layer:service",
            "layer_name": "Services",
        }
        assert await rebuild_page_tree(async_session, wiki) == 0
        await async_session.commit()
        assert await async_session.scalar(
            select(Page.metadata_json).where(Page.id == page.id)
        ) == stored


class TestUpdateDoesNotFlattenTheTree:
    async def test_re_upserting_one_page_with_no_placement_is_repaired(
        self, async_session, wiki
    ):
        """The exact incremental-update shape: one regenerated page arrives
        carrying the placement a partial set could work out, which is none."""
        before = await _placement(async_session, wiki)

        await _add(async_session, wiki, "file_page", "src/ingest/a.py", layer_id="layer:service")
        await async_session.commit()
        flattened = await _placement(async_session, wiki)
        assert flattened["file_page:src/ingest/a.py"][0] is None, "precondition"

        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()
        assert await _placement(async_session, wiki) == before


class TestAddRemoveRename:
    async def test_an_added_file_joins_the_tree(self, async_session, wiki):
        await _add(async_session, wiki, "file_page", "src/ingest/c.py", layer_id="layer:service")
        await async_session.commit()
        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        assert placed["file_page:src/ingest/c.py"][0] == "module_page:src/ingest"

    async def test_siblings_renumber_without_gaps_after_an_add(self, async_session, wiki):
        await _add(async_session, wiki, "file_page", "src/ingest/c.py", layer_id="layer:service")
        await async_session.commit()
        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        orders = sorted(
            order
            for pid, (parent, order, _) in placed.items()
            if parent == "module_page:src/ingest"
        )
        assert orders == [1, 2, 3]

    async def test_a_tombstoned_page_leaves_the_tree(self, async_session, wiki):
        """A deleted file must not keep showing up in the navigation."""
        page = await async_session.get(Page, "file_page:src/ingest/b.py")
        page.freshness_status = "tombstone"
        page.metadata_json = json.dumps({"successor_paths": []})
        await async_session.commit()

        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        assert placed["file_page:src/ingest/b.py"] == (None, 0, None)
        # And the surviving sibling closes the gap.
        assert placed["file_page:src/ingest/a.py"][1] == 1

    async def test_a_rename_moves_the_page_and_leaves_no_orphan(self, async_session, wiki):
        """A rename reaches the tree as a tombstone plus a new page.

        There is no continuity between them: the rebuild reads freshness, not
        successor_paths, so the old page is simply unplaced and the new one is
        placed on its own merits. That is what this pins.
        """
        old = await async_session.get(Page, "file_page:src/ingest/b.py")
        old.freshness_status = "tombstone"
        old.metadata_json = json.dumps({"successor_paths": ["src/web/b.py"]})
        await _add(async_session, wiki, "module_page", "src/web", file_paths=["src/web/b.py"])
        await _add(async_session, wiki, "file_page", "src/web/b.py", layer_id="layer:service")
        await async_session.commit()

        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        assert placed["file_page:src/web/b.py"][0] == "module_page:src/web"
        assert placed["file_page:src/ingest/b.py"] == (None, 0, None)

    async def test_no_dangling_parents_after_the_churn(self, async_session, wiki):
        await _add(async_session, wiki, "file_page", "src/ingest/c.py", layer_id="layer:service")
        await async_session.commit()
        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        known = set(placed)
        assert [
            (pid, parent) for pid, (parent, _, _) in placed.items() if parent and parent not in known
        ] == []

    async def test_removing_the_module_reparents_its_files(self, async_session, wiki):
        """The sweep deletes module pages whose key moved. Their files must
        find a new home rather than point at a row that is gone."""
        module = await async_session.get(Page, "module_page:src/ingest")
        await async_session.delete(module)
        await async_session.commit()

        await rebuild_page_tree(async_session, wiki)
        await async_session.commit()

        placed = await _placement(async_session, wiki)
        assert placed["file_page:src/ingest/a.py"][0] == "repo_overview:demo"
