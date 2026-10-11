"""Every empty call-graph list carries what it rests on.

An empty ``callers``/``callees``/``used_by`` list reads identically whether the
resolver bound almost every call site in that language or guessed most of them.
``_basis`` attaches the language's call-edge count and the share of those edges
the resolution vocabulary ranks at or below 0.75 confidence, plus a note saying
that call sites the resolver never bound are not counted anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode
from repowise.server.mcp_server._basis import (
    NO_EDGES_NOTE,
    RESOLVED_NOTE,
    call_resolution_bases,
    call_resolution_basis,
    reset_cache,
)

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

_LONELY = "src/auth/service.py::lonely"
_CALLED = "src/auth/service.py::called"
_CALLER = "src/auth/middleware.py::caller"

# Two guesses (0.50 and 0.75), one certainty, one NULL origin. The NULL row
# predates the vocabulary, so it belongs in the denominator and not the numerator:
# 2 of 4 edges are guesses.
_ORIGINS = ["global_unique", "receiver_global", "same_file", None]


def _sym(node_id: str, sid: str, repo_id: str, language: str = "python") -> GraphNode:
    file_path, name = node_id.split("::", 1)
    return GraphNode(
        id=sid,
        repository_id=repo_id,
        node_id=node_id,
        node_type="symbol",
        language=language,
        name=name,
        file_path=file_path,
        kind="function",
        start_line=10,
        end_line=20,
        created_at=_NOW,
    )


async def _seed(session, repo_id: str) -> None:
    """One symbol nothing calls, one with four inbound calls of mixed origin."""
    rows: list[object] = [
        _sym(_LONELY, "b_lonely", repo_id),
        _sym(_CALLED, "b_called", repo_id),
        _sym(_CALLER, "b_caller", repo_id),
        # A typescript symbol with no call edges at all.
        _sym("web/dashboard.ts::render", "b_ts", repo_id, language="typescript"),
    ]
    for i, origin in enumerate(_ORIGINS):
        source = _sym(f"src/callers/c{i}.py::c{i}", f"b_src_{i}", repo_id)
        rows.append(source)
        rows.append(
            GraphEdge(
                id=f"b_edge_{i}",
                repository_id=repo_id,
                source_node_id=source.node_id,
                target_node_id=_CALLED,
                edge_type="calls",
                confidence=0.95,
                resolution_origin=origin,
                created_at=_NOW,
            )
        )
    session.add_all(rows)
    await session.flush()
    reset_cache()


@pytest.mark.asyncio
async def test_basis_shape_and_guessed_share(setup_mcp, session):
    """Two guesses of four edges, with the NULL origin counted only below the line."""
    repo_id = setup_mcp
    await _seed(session, repo_id)

    basis = await call_resolution_basis(session, repo_id, "python", cache_key="k1")
    assert basis == {
        "language": "python",
        "resolved_call_edges": 4,
        "guessed_share": 0.5,
        "unresolved_call_sites": None,
        "note": RESOLVED_NOTE,
    }
    # The count the index cannot produce is named as absent, not guessed at.
    assert "not counted" in RESOLVED_NOTE


@pytest.mark.asyncio
async def test_language_with_no_call_edges_says_so(setup_mcp, session):
    """A language the graph never resolved a call in gets the other note."""
    repo_id = setup_mcp
    await _seed(session, repo_id)

    basis = await call_resolution_basis(session, repo_id, "typescript", cache_key="k1")
    assert basis["resolved_call_edges"] == 0
    assert basis["guessed_share"] is None
    assert basis["note"] == NO_EDGES_NOTE


@pytest.mark.asyncio
async def test_cache_holds_per_key_and_requeries_on_a_new_one(setup_mcp, session):
    """One aggregate query per repo per index commit, and a new key re-reads."""
    repo_id = setup_mcp
    await _seed(session, repo_id)

    first = await call_resolution_basis(session, repo_id, "python", cache_key="k1")
    assert first["resolved_call_edges"] == 4

    # A fifth edge lands. The old key still serves the cached grouping.
    session.add(
        GraphEdge(
            id="b_edge_extra",
            repository_id=repo_id,
            source_node_id=_CALLER,
            target_node_id=_CALLED,
            edge_type="calls",
            confidence=0.95,
            resolution_origin="same_file",
            created_at=_NOW,
        )
    )
    await session.flush()

    assert (await call_resolution_basis(session, repo_id, "python", cache_key="k1")) == first
    fresh = await call_resolution_basis(session, repo_id, "python", cache_key="k2")
    assert fresh["resolved_call_edges"] == 5
    assert fresh["guessed_share"] == 0.4


@pytest.mark.asyncio
async def test_bases_cover_every_language_with_edges(setup_mcp, session):
    """The repo-wide list names only languages that actually have call edges."""
    repo_id = setup_mcp
    await _seed(session, repo_id)

    bases = await call_resolution_bases(session, repo_id, cache_key="k1")
    assert [b["language"] for b in bases] == ["python"]


@pytest.mark.asyncio
async def test_get_context_empty_callers_carry_a_basis(setup_mcp, session):
    """The zero gets its basis; a populated list does not."""
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp)

    lonely = (await get_context([_LONELY], include=["callers"], compact=False))["targets"][_LONELY]
    assert lonely["callers"] == []
    assert lonely["callers_basis"]["language"] == "python"
    assert lonely["callers_basis"]["resolved_call_edges"] == 4

    called = (await get_context([_CALLED], include=["callers"], compact=False))["targets"][_CALLED]
    assert called["callers"]
    assert "callers_basis" not in called


@pytest.mark.asyncio
async def test_dead_code_summary_carries_the_basis(setup_mcp, session):
    """Reachability findings say how much of the graph they rest on."""
    from repowise.server.mcp_server import get_dead_code

    await _seed(session, setup_mcp)

    result = await get_dead_code()
    bases = result["summary"]["call_resolution_basis"]
    assert isinstance(bases, list)
    assert {b["language"] for b in bases} == {"python"}
    assert bases[0]["guessed_share"] == 0.5


@pytest.mark.asyncio
async def test_symbol_card_empty_used_by_carries_a_basis(setup_mcp, session):
    """Nobody uses this symbol's file, and the card says what that rests on."""
    from repowise.server.mcp_server import get_context

    repo_id = setup_mcp
    await _seed(session, repo_id)
    session.add(_sym("src/orphan/util.py::orphaned", "b_orphan", repo_id))
    await session.flush()

    docs = (await get_context(["src/orphan/util.py::orphaned"], compact=False))["targets"][
        "src/orphan/util.py::orphaned"
    ]["docs"]
    assert docs["used_by"] == []
    assert docs["used_by_basis"]["language"] == "python"
    assert docs["used_by_basis"]["note"] == RESOLVED_NOTE
