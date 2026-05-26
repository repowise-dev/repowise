"""Tests for Phase 4 decision REST endpoints.

Covers:
- GET /api/repos/{repo_id}/decisions/{decision_id}/evidence
- GET /api/repos/{repo_id}/decisions/{decision_id}/lineage
- GET /api/repos/{repo_id}/decisions/graph
- 404 behaviour on bad decision_id / wrong repo
- verification field on DecisionRecordResponse
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionEvidence, DecisionNodeLink
from tests.unit.server.conftest import create_test_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_decision(
    session_factory,
    repo_id: str,
    *,
    title: str = "Use SQLite for tests",
    status: str = "active",
    verification: str = "exact",
) -> str:
    """Insert a DecisionRecord and return its id."""
    async with get_session(session_factory) as session:
        rec = await crud.upsert_decision(
            session,
            repository_id=repo_id,
            title=title,
            status=status,
            context="Need zero-config storage for CI.",
            decision="Use aiosqlite in-memory DBs.",
            rationale="Fast, no external deps.",
            affected_modules=["packages/core"],
            affected_files=["packages/core/src/repowise/core/persistence/database.py"],
            source="inline_marker",
            confidence=0.9,
            verification=verification,
        )
        return rec.id


async def _seed_evidence(session_factory, decision_id: str) -> None:
    """Insert a DecisionEvidence row directly."""
    async with get_session(session_factory) as session:
        session.add(
            DecisionEvidence(
                decision_id=decision_id,
                source="inline_marker",
                source_rank=2,
                evidence_file="packages/core/src/repowise/core/persistence/database.py",
                evidence_line=42,
                evidence_commit="abc123def456",
                source_quote="# ADR: Use SQLite for all unit tests.",
                confidence=0.95,
                verification="exact",
            )
        )
        await session.flush()


async def _seed_lineage(session_factory, repo_id: str, old_id: str, new_id: str) -> None:
    """Create a supersedes edge: new_id supersedes old_id."""
    async with get_session(session_factory) as session:
        from repowise.core.persistence import decision_graph

        await decision_graph.upsert_decision_edge(
            session,
            repository_id=repo_id,
            src_decision_id=new_id,
            dst_decision_id=old_id,
            kind="supersedes",
            confidence=0.8,
            evidence="Replaced by newer approach.",
        )


async def _seed_code_link(session_factory, repo_id: str, decision_id: str) -> None:
    """Insert a DecisionNodeLink row directly."""
    async with get_session(session_factory) as session:
        session.add(
            DecisionNodeLink(
                repository_id=repo_id,
                decision_id=decision_id,
                node_id="packages/core/src/repowise/core/persistence/database.py",
                link_type="file",
            )
        )
        await session.flush()


# ---------------------------------------------------------------------------
# Evidence endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_returns_rows(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])
    await _seed_evidence(app.state.session_factory, decision_id)

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert "evidence" in body
    assert len(body["evidence"]) == 1
    ev = body["evidence"][0]
    assert ev["source"] == "inline_marker"
    assert ev["source_rank"] == 2
    assert ev["evidence_file"] == "packages/core/src/repowise/core/persistence/database.py"
    assert ev["evidence_line"] == 42
    assert ev["evidence_commit"] == "abc123def456"
    assert ev["source_quote"] == "# ADR: Use SQLite for all unit tests."
    assert ev["confidence"] == pytest.approx(0.95)
    assert ev["verification"] == "exact"
    # created_at must be an ISO-8601 string
    assert isinstance(ev["created_at"], str)
    assert "T" in ev["created_at"]


@pytest.mark.asyncio
async def test_evidence_empty_when_no_rows(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}/evidence")
    assert resp.status_code == 200
    assert resp.json()["evidence"] == []


@pytest.mark.asyncio
async def test_evidence_404_bad_decision(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/decisions/nonexistent/evidence")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_404_wrong_repo(client: AsyncClient, app) -> None:
    repo_a = await create_test_repo(client)
    repo_b = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo_a["id"])

    resp = await client.get(f"/api/repos/{repo_b['id']}/decisions/{decision_id}/evidence")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lineage endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lineage_single_entry_when_isolated(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"])

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}/lineage")
    assert resp.status_code == 200
    body = resp.json()
    assert "lineage" in body
    assert len(body["lineage"]) == 1
    entry = body["lineage"][0]
    assert entry["id"] == decision_id
    assert entry["relation"] is None


@pytest.mark.asyncio
async def test_lineage_chain_with_supersedes(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    old_id = await _seed_decision(
        app.state.session_factory, repo["id"], title="Old auth strategy", status="superseded"
    )
    new_id = await _seed_decision(
        app.state.session_factory, repo["id"], title="New auth strategy", status="active"
    )
    await _seed_lineage(app.state.session_factory, repo["id"], old_id, new_id)

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{new_id}/lineage")
    assert resp.status_code == 200
    chain = resp.json()["lineage"]
    # Chain is root→current: old then new
    assert len(chain) == 2
    ids_in_chain = [e["id"] for e in chain]
    assert old_id in ids_in_chain
    assert new_id in ids_in_chain
    # The entry for old_id (ancestor) should have relation "supersedes"
    old_entry = next(e for e in chain if e["id"] == old_id)
    assert old_entry["relation"] == "supersedes"


@pytest.mark.asyncio
async def test_lineage_404_bad_decision(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/decisions/nonexistent/lineage")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lineage_404_wrong_repo(client: AsyncClient, app) -> None:
    repo_a = await create_test_repo(client)
    repo_b = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo_a["id"])

    resp = await client.get(f"/api/repos/{repo_b['id']}/decisions/{decision_id}/lineage")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Graph endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_returns_nodes_edges_code_edges(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    id_a = await _seed_decision(
        app.state.session_factory, repo["id"], title="Decision A", status="active"
    )
    id_b = await _seed_decision(
        app.state.session_factory, repo["id"], title="Decision B", status="superseded"
    )
    # A supersedes B
    await _seed_lineage(app.state.session_factory, repo["id"], id_b, id_a)
    # Code link for A
    await _seed_code_link(app.state.session_factory, repo["id"], id_a)

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/graph")
    assert resp.status_code == 200
    body = resp.json()

    assert "nodes" in body
    assert "decision_edges" in body
    assert "code_edges" in body

    node_ids = {n["id"] for n in body["nodes"]}
    assert id_a in node_ids
    assert id_b in node_ids

    # Each node must carry verification + staleness_score
    for node in body["nodes"]:
        assert "verification" in node
        assert "staleness_score" in node
        assert "confidence" in node

    assert len(body["decision_edges"]) == 1
    edge = body["decision_edges"][0]
    assert edge["src"] == id_a
    assert edge["dst"] == id_b
    assert edge["kind"] == "supersedes"
    assert "confidence" in edge
    assert "evidence" in edge

    assert len(body["code_edges"]) == 1
    ce = body["code_edges"][0]
    assert ce["decision_id"] == id_a
    assert ce["link_type"] == "file"


@pytest.mark.asyncio
async def test_graph_empty_repo(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/decisions/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["decision_edges"] == []
    assert body["code_edges"] == []


# ---------------------------------------------------------------------------
# verification field on DecisionRecordResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_record_response_includes_verification(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    decision_id = await _seed_decision(app.state.session_factory, repo["id"], verification="fuzzy")

    resp = await client.get(f"/api/repos/{repo['id']}/decisions/{decision_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"] == "fuzzy"
