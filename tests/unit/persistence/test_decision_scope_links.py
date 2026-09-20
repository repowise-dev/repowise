"""Every writer that moves a decision's scope moves its graph links with it.

The JSON arrays are a read cache; ``decision_node_links`` is what the
edit-time injection hook and the ``get_risk`` directives query. A record whose
scope reached one and not the other is stored and then ignored by every
path-scoped surface.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select

from repowise.core.analysis.decisions.scope import (
    SCOPE_BASIS_REPOSITORY,
    SCOPE_BASIS_STATED,
)
from repowise.core.persistence.crud import (
    recompute_decision_staleness,
    update_decision_by_id,
    update_decision_metadata,
    upsert_decision,
)
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.persistence.decision_graph import (
    expected_node_links,
    get_governed_nodes,
    get_governing_decisions,
    sync_links_from_record,
)
from repowise.core.persistence.decision_migration import backfill_decision_node_links
from repowise.core.persistence.models import DecisionNodeLink
from tests.unit.persistence.helpers import insert_repo


async def _add(session, repo_id: str, **kwargs):
    return await upsert_decision(
        session,
        repository_id=repo_id,
        title=kwargs.pop("title", "Use one queue"),
        decision="One queue, not three.",
        source="cli",
        **kwargs,
    )


async def _links(session, decision_id: str) -> set[tuple[str, str]]:
    rows = await session.execute(
        select(DecisionNodeLink.node_id, DecisionNodeLink.link_type).where(
            DecisionNodeLink.decision_id == decision_id
        )
    )
    return set(rows.all())


async def test_decision_add_reaches_the_governing_lookup(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])

    governing = await get_governing_decisions(async_session, repo.id, "a/one.py")
    assert [r.id for r in governing] == [rec.id]


async def test_a_first_write_states_its_basis_like_a_restate_does(async_session):
    """Both arms of ``upsert_decision``, or the same call gives two bases."""
    repo = await insert_repo(async_session)
    first = await _add(async_session, repo.id, affected_files=["a/one.py"])
    assert first.scope_basis == SCOPE_BASIS_STATED

    again = await _add(async_session, repo.id, affected_files=["a/one.py", "b/two.py"])
    assert again.scope_basis == SCOPE_BASIS_STATED


async def test_restating_a_record_replaces_its_links(async_session):
    repo = await insert_repo(async_session)
    first = await _add(async_session, repo.id, affected_files=["a/one.py"])
    again = await _add(async_session, repo.id, affected_files=["b/two.py"])

    assert again.id == first.id
    assert await _links(async_session, first.id) == {("b/two.py", "file")}


async def test_a_non_binding_basis_links_nothing_despite_its_files(async_session):
    """The gate, not the empty case: files present, basis that does not bind."""
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])
    assert await _links(async_session, rec.id) == {("a/one.py", "file")}

    rec.scope_basis = SCOPE_BASIS_REPOSITORY
    await sync_links_from_record(async_session, rec)

    assert await _links(async_session, rec.id) == set()


async def test_confirm_with_a_scope_links_the_files_it_chose(async_session):
    """``decision add`` points a candidate at ``confirm --scope``; that is this."""
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=[])
    assert await _links(async_session, rec.id) == set()

    await accept_decision(
        async_session,
        rec,
        accepter="dev",
        scope=["c/three.py"],
        reason="one queue is enough",
    )

    # The module is derived from the accepter's files, not left as it was.
    assert await _links(async_session, rec.id) == {("c/three.py", "file"), ("c", "module")}


async def test_an_accepted_scope_leaves_no_module_from_the_old_one(async_session):
    """Half a scope moving is worse than none: the stale module still governs."""
    repo = await insert_repo(async_session)
    rec = await _add(
        async_session, repo.id, affected_files=["old/one.py"], affected_modules=["old"]
    )
    assert ("old", "module") in await _links(async_session, rec.id)

    await accept_decision(
        async_session, rec, accepter="dev", scope=["new/two.py"], reason="moved"
    )

    assert await _links(async_session, rec.id) == {("new/two.py", "file"), ("new", "module")}


async def test_a_dismissed_record_governs_nothing(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])
    assert await _links(async_session, rec.id)

    rec.status = "dismissed"
    await sync_links_from_record(async_session, rec)

    assert await _links(async_session, rec.id) == set()
    assert await get_governing_decisions(async_session, repo.id, "a/one.py") == []


async def test_the_backfill_unlinks_a_dismissed_record(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])
    rec.status = "dismissed"
    await async_session.flush()

    assert await backfill_decision_node_links(async_session, repo.id) == 1

    assert await _links(async_session, rec.id) == set()


async def test_the_module_repair_relinks_what_it_rewrites(async_session):
    """The repair runs after the backfill, so its own rewrite must relink."""
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["packages/core/one.py"])
    # The legacy derivation: module = first path segment.
    rec.affected_modules_json = json.dumps(["packages"])
    await sync_links_from_record(async_session, rec)
    assert ("packages", "module") in await _links(async_session, rec.id)

    await recompute_decision_staleness(async_session, repo.id, {})

    links = await _links(async_session, rec.id)
    assert ("packages", "module") not in links
    assert ("packages/core", "module") in links


async def test_metadata_and_by_id_patches_move_the_links(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])

    await update_decision_metadata(
        async_session, rec.id, affected_files=["d/four.py"], affected_modules=["d"]
    )
    assert await _links(async_session, rec.id) == {("d/four.py", "file"), ("d", "module")}

    await update_decision_by_id(async_session, rec.id, affected_files=["e/five.py"])
    nodes = await get_governed_nodes(async_session, rec.id)
    assert any(n.node_id == "e/five.py" for n in nodes)
    assert not any(n.node_id == "d/four.py" for n in nodes)


async def test_clearing_a_scope_clears_the_links(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])

    await update_decision_metadata(async_session, rec.id, affected_files=[])

    assert await _links(async_session, rec.id) == set()


async def test_expected_links_ignore_a_malformed_array(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])
    rec.affected_files_json = "{not json"

    assert expected_node_links(rec) == ([], [])


async def test_backfill_relinks_a_record_written_before_the_writer(async_session):
    repo = await insert_repo(async_session)
    rec = await _add(async_session, repo.id, affected_files=["a/one.py"])
    # The pre-fix vintage: arrays written, graph never touched.
    await async_session.execute(
        delete(DecisionNodeLink).where(DecisionNodeLink.decision_id == rec.id)
    )
    await async_session.flush()
    assert await _links(async_session, rec.id) == set()

    assert await backfill_decision_node_links(async_session, repo.id) == 1

    assert await _links(async_session, rec.id) == {("a/one.py", "file")}


async def test_backfill_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    await _add(async_session, repo.id, affected_files=["a/one.py"])

    assert await backfill_decision_node_links(async_session, repo.id) == 0
