"""Every writer that moves a decision's scope moves its graph links with it.

The JSON arrays are a read cache; ``decision_node_links`` is what the
edit-time injection hook and the ``get_risk`` directives query. A record whose
scope reached one and not the other is stored and then ignored by every
path-scoped surface.
"""

from __future__ import annotations

from repowise.core.persistence.crud import (
    update_decision_by_id,
    update_decision_metadata,
    upsert_decision,
)
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.analysis.decisions.scope import SCOPE_BASIS_REPOSITORY
from repowise.core.persistence.decision_graph import (
    expected_node_links,
    get_governed_nodes,
    get_governing_decisions,
    sync_links_from_record,
)
from repowise.core.persistence.decision_migration import backfill_decision_node_links
from repowise.core.persistence.models import DecisionNodeLink
from sqlalchemy import delete, select
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

    assert await _links(async_session, rec.id) == {("c/three.py", "file")}


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
