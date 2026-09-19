"""A commit footprint does not answer "what governs this file".

Pins the ``get_why`` half of the scope-precision fix. The record stays in the
store, keeps its files and stays findable by text search; what it loses is the
per-file lanes, which is where a decision mined from one commit was answering
for every file that commit happened to touch.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis.decisions.scope import SCOPE_BASIS_FOOTPRINT
from repowise.core.persistence.models import DecisionRecord

_BYSTANDER = "src/auth/middleware.py"


async def _add_footprint_record(factory, *, scope_basis: str) -> str:
    """A wide record naming the fixture's files, as the PR miner writes one."""
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository

    async with factory() as session:
        rid = (await session.execute(select(Repository.id))).scalars().first()
        rec = DecisionRecord(
            id="footprint1",
            repository_id=rid,
            title="Collapse unchanged hook re-reads",
            status="active",
            decision="Collapse repeated reads of an unchanged file",
            rationale="mined from one commit body",
            affected_files_json=json.dumps(
                [_BYSTANDER, "src/auth/service.py", "src/db/models.py"]
            ),
            affected_modules_json=json.dumps(["src/auth"]),
            source="pr",
            scope_basis=scope_basis,
            confidence=0.8,
        )
        session.add(rec)
        await session.commit()
        return rid


@pytest.mark.asyncio
async def test_a_footprint_is_absent_from_every_per_file_lane(setup_mcp, factory):
    from repowise.server.mcp_server import get_why

    await _add_footprint_record(factory, scope_basis=SCOPE_BASIS_FOOTPRINT)

    result = await get_why(_BYSTANDER)
    assert result["mode"] == "path"
    titles = [
        d["title"]
        for lane in ("decisions", "candidates", "history")
        for d in result.get(lane) or []
    ]
    assert "Collapse unchanged hook re-reads" not in titles


@pytest.mark.asyncio
async def test_the_same_record_without_the_basis_does_answer(setup_mcp, factory):
    """The gate is the basis, not the title, the source or the file list.

    Without this the test above passes for the wrong reason -- a fixture that
    never matched the path at all.
    """
    from repowise.server.mcp_server import get_why

    await _add_footprint_record(factory, scope_basis="")

    result = await get_why(_BYSTANDER)
    titles = [
        d["title"]
        for lane in ("decisions", "candidates", "history")
        for d in result.get(lane) or []
    ]
    assert "Collapse unchanged hook re-reads" in titles


@pytest.mark.asyncio
async def test_a_footprint_is_still_reachable_by_text_search(setup_mcp, factory):
    """It stops being specific, not stored. A repo-wide question still finds it."""
    from repowise.server.mcp_server import get_why

    await _add_footprint_record(factory, scope_basis=SCOPE_BASIS_FOOTPRINT)

    result = await get_why("why collapse unchanged hook re-reads")
    assert result["mode"] == "search"
    assert any(
        d["title"] == "Collapse unchanged hook re-reads" for d in result["decisions"]
    )


@pytest.mark.asyncio
async def test_two_targets_refuse_what_one_target_refuses(setup_mcp, factory):
    """``get_why`` routes one target to path mode and two to a second selector.

    They read the same column and must agree, or the same file answers
    differently depending on how many were asked about.
    """
    from repowise.server.mcp_server import get_why

    await _add_footprint_record(factory, scope_basis=SCOPE_BASIS_FOOTPRINT)

    # No query: a question would run the relevance pass and suppress the
    # record for a reason that has nothing to do with its scope, so the
    # assertion would hold whether or not the gate existed.
    result = await get_why(targets=[_BYSTANDER, "src/db/models.py"])
    entries = result.get("target_context") or {}
    assert set(entries) == {_BYSTANDER, "src/db/models.py"}
    for entry in entries.values():
        # Both lanes: the record has no acceptance row, so it would land in
        # ``candidate_decisions``, and asserting only on the governing lane
        # would pass without the gate.
        titles = [
            row["title"]
            for lane in ("governing_decisions", "candidate_decisions")
            for row in entry.get(lane) or []
        ]
        assert "Collapse unchanged hook re-reads" not in titles


@pytest.mark.asyncio
async def test_two_targets_do_answer_without_the_basis(setup_mcp, factory):
    """The companion that keeps the test above from passing vacuously."""
    from repowise.server.mcp_server import get_why

    await _add_footprint_record(factory, scope_basis="")

    result = await get_why(targets=[_BYSTANDER, "src/db/models.py"])
    titles = [
        row["title"]
        for entry in (result.get("target_context") or {}).values()
        for lane in ("governing_decisions", "candidate_decisions")
        for row in entry.get(lane) or []
    ]
    assert "Collapse unchanged hook re-reads" in titles


@pytest.mark.asyncio
async def test_get_context_does_not_print_a_footprint_on_a_file_card(
    setup_mcp, factory
):
    """``get_context`` reads the JSON array, not the decision graph.

    Gating the graph at the write path fixes session injection and the risk
    directives and does nothing here, so this surface needs its own gate --
    and it is the per-file decisions lane agents hit most.
    """
    from repowise.server.mcp_server import get_context

    await _add_footprint_record(factory, scope_basis=SCOPE_BASIS_FOOTPRINT)

    result = await get_context(targets=[_BYSTANDER], include=["decisions"])
    blob = json.dumps(result, default=str)
    assert "Collapse unchanged hook re-reads" not in blob


@pytest.mark.asyncio
async def test_get_context_does_print_the_same_record_without_the_basis(
    setup_mcp, factory
):
    """The negative control: the gate is the basis, not the fixture."""
    from repowise.server.mcp_server import get_context

    await _add_footprint_record(factory, scope_basis="")

    result = await get_context(targets=[_BYSTANDER], include=["decisions"])
    blob = json.dumps(result, default=str)
    assert "Collapse unchanged hook re-reads" in blob
