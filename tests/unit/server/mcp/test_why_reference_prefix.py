from __future__ import annotations

import pytest

from repowise.core.persistence.models import DecisionAlias, DecisionRecord
from repowise.server.mcp_server import get_why


@pytest.mark.asyncio
async def test_why_reference_resolves_unambiguous_prefix(session, setup_mcp) -> None:
    full_id = "3e0f68f833e1c4a87f9ebe94b08dd813"
    session.add(
        DecisionRecord(
            id=full_id,
            repository_id=setup_mcp,
            title="Use Redis for rate limiting",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    await session.commit()

    result = await get_why(id="3e0f68f8")
    assert result["resolved"] is True
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["id"] == full_id


@pytest.mark.asyncio
async def test_why_reference_reports_ambiguous_prefix(session, setup_mcp) -> None:
    id1 = "4a1b2c3d111111111111111111111111"
    id2 = "4a1b2c3d222222222222222222222222"
    session.add(
        DecisionRecord(
            id=id1,
            repository_id=setup_mcp,
            title="Decision One",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    session.add(
        DecisionRecord(
            id=id2,
            repository_id=setup_mcp,
            title="Decision Two",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    await session.commit()

    result = await get_why(id="4a1b2c3d")
    assert result["resolved"] is False
    assert result["ambiguous"] is True
    assert result["candidates"] == [id1, id2]
    assert "ambiguous" in result["error"].lower()


@pytest.mark.asyncio
async def test_why_reference_resolves_alias_prefix(session, setup_mcp) -> None:
    live_id = "5f6e7d8c9b0a1234567890abcdef1234"
    alias_id = "99887766554433221100aabbccddeeff"
    session.add(
        DecisionRecord(
            id=live_id,
            repository_id=setup_mcp,
            title="Consolidated architecture",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    session.add(
        DecisionAlias(
            alias_id=alias_id,
            repository_id=setup_mcp,
            decision_id=live_id,
            reason="merged",
        )
    )
    await session.commit()

    result = await get_why(id="99887766")
    assert result["resolved"] is True
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["id"] == live_id


@pytest.mark.asyncio
async def test_why_reference_ignores_superseded_alias_prefix(session, setup_mcp) -> None:
    live_id = "5f6e7d8c9b0a1234567890abcdef1234"
    superseded_id = "11223344556677889900aabbccddeeff"
    session.add(
        DecisionRecord(
            id=live_id,
            repository_id=setup_mcp,
            title="Consolidated architecture",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    session.add(
        DecisionAlias(
            alias_id=superseded_id,
            repository_id=setup_mcp,
            decision_id=live_id,
            reason="superseded",
        )
    )
    await session.commit()

    result = await get_why(id="11223344")
    assert result["resolved"] is False
    assert result["decisions"] == []


@pytest.mark.asyncio
async def test_why_reference_rejects_short_id_fragment(session, setup_mcp) -> None:
    full_id = "3e0f68f833e1c4a87f9ebe94b08dd813"
    session.add(
        DecisionRecord(
            id=full_id,
            repository_id=setup_mcp,
            title="Use Redis for rate limiting",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    )
    await session.commit()

    # Prefixes shorter than 8 chars do not resolve even if only 1 record matches
    result_short = await get_why(id="3e")
    assert result_short["resolved"] is False
    assert result_short["decisions"] == []

    result_single = await get_why(id="3")
    assert result_single["resolved"] is False
    assert result_single["decisions"] == []

    from repowise.server.mcp_server.tool_why.reference import _resolve_reference_id

    records = [
        DecisionRecord(
            id=full_id,
            repository_id=setup_mcp,
            title="Use Redis for rate limiting",
            status="proposed",
            context="ctx",
            decision="dec",
            rationale="why",
            affected_files_json="[]",
        )
    ]
    resolved_empty, cands_empty = await _resolve_reference_id(session, "", records)
    assert resolved_empty == ""
    assert cands_empty == []

    resolved_short, cands_short = await _resolve_reference_id(session, "3e", records)
    assert resolved_short == "3e"
    assert cands_short == []
