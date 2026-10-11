"""Which edge types ``expand_via_graph`` may walk.

This is a guard on a deliberate asymmetry, not a regression test for a fix.

``expand_via_graph`` is retrieval rescue: it folds one-hop graph neighbours of
the top candidates back into the hit list so a question about a wrapper can
still reach the implementation. It used to call its two queries "importers"
and "importees" while filtering on no edge type at all, which invited a
follow-up to make the code match the comment by excluding every
``NON_DEPENDENCY_EDGE_TYPES`` value.

Doing that would be wrong here. Unlike ``get_risk`` — which states an import
relationship and so must not count a co-change as one — expansion only claims
"read this too", and surfaces what it adds neutrally as ``[graph-expanded]``.
A file that historically moves with the seed is a good answer to that. So
co-change neighbours stay, and only containment edges are excluded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode, Page

_SEED = "src/auth/service.py"
_PARTNER = "src/reports/exporter.py"
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _seed_co_change_partner_with_a_page(factory, repo_id: str) -> None:
    async with factory() as s:
        s.add(
            GraphNode(
                id="gn-partner",
                repository_id=repo_id,
                node_id=_PARTNER,
                node_type="file",
                language="python",
                symbol_count=1,
                is_entry_point=False,
                pagerank=0.3,
                betweenness=0.0,
                community_id=3,
                created_at=_NOW,
            )
        )
        s.add(
            GraphEdge(
                id="ge-cochange-expand",
                repository_id=repo_id,
                source_node_id=_SEED,
                target_node_id=_PARTNER,
                imported_names_json="[]",
                edge_type="co_changes",
                created_at=_NOW,
            )
        )
        s.add(
            Page(
                id=f"file_page:{_PARTNER}",
                repository_id=repo_id,
                page_type="file_page",
                title=f"File: {_PARTNER}",
                content="# exporter\n\nWrites the report bundle.",
                summary="Writes the report bundle.",
                target_path=_PARTNER,
                source_hash="exp1",
                model_name="mock",
                provider_name="mock",
                generation_level=4,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_co_change_neighbour_is_still_expanded(setup_mcp, factory):
    from repowise.server.mcp_server._answer_pipeline import expand_via_graph

    await _seed_co_change_partner_with_a_page(factory, setup_mcp)

    hits = [{"target_path": _SEED, "score": 0.9, "page_type": "file_page"}]
    expanded = await expand_via_graph(hits, SimpleNamespace(session_factory=factory), setup_mcp)

    added = {h["target_path"] for h in expanded if h.get("_expanded_from") == "graph"}
    assert _PARTNER in added, (
        "co-change neighbours are retained on purpose; excluding every "
        "NON_DEPENDENCY_EDGE_TYPES value here would silently narrow recall"
    )


@pytest.mark.asyncio
async def test_containment_neighbours_are_excluded(setup_mcp, factory):
    """The half of the filter that IS a change.

    Containment endpoints are ``path::Name`` symbol nodes, so they could never
    match a ``file_page`` and were fetched only to be discarded. They also fed
    the neighbour set that sizes the hub cutoff, which is why dropping them is
    not merely a saving — see ``_neighbor_degrees``.
    """
    from repowise.server.mcp_server._answer_pipeline import expand_via_graph

    symbol_id = f"{_SEED}::AuthService"
    async with factory() as s:
        s.add(
            GraphEdge(
                id="ge-defines-expand",
                repository_id=setup_mcp,
                source_node_id=_SEED,
                target_node_id=symbol_id,
                imported_names_json="[]",
                edge_type="defines",
                created_at=_NOW,
            )
        )
        await s.commit()

    hits = [{"target_path": _SEED, "score": 0.9, "page_type": "file_page"}]
    expanded = await expand_via_graph(hits, SimpleNamespace(session_factory=factory), setup_mcp)

    assert symbol_id not in {h.get("target_path") for h in expanded}


async def _seed_callee_with_a_page(factory, repo_id: str, path: str, confidence: float) -> None:
    """A ``calls`` edge from a symbol in ``_SEED`` to a symbol in *path*."""
    async with factory() as s:
        s.add(
            GraphNode(
                id=f"gn-{path}",
                repository_id=repo_id,
                node_id=path,
                node_type="file",
                language="python",
                symbol_count=1,
                is_entry_point=False,
                pagerank=0.1,
                betweenness=0.0,
                community_id=4,
                created_at=_NOW,
            )
        )
        s.add(
            GraphEdge(
                id=f"ge-calls-{path}",
                repository_id=repo_id,
                source_node_id=f"{_SEED}::login",
                target_node_id=f"{path}::verify",
                imported_names_json="[]",
                edge_type="calls",
                confidence=confidence,
                created_at=_NOW,
            )
        )
        s.add(
            Page(
                id=f"file_page:{path}",
                repository_id=repo_id,
                page_type="file_page",
                title=f"File: {path}",
                content="# callee",
                summary="The thing the seed calls.",
                target_path=path,
                source_hash=f"h-{path}",
                model_name="mock",
                provider_name="mock",
                generation_level=4,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        await s.commit()


@pytest.mark.asyncio
async def test_call_neighbour_is_expanded(setup_mcp, factory):
    """``calls`` endpoints are ``path::Name`` nodes, so expansion only sees them
    once they are projected onto their file."""
    from repowise.server.mcp_server._answer_pipeline import expand_via_graph

    callee = "src/auth/tokens.py"
    await _seed_callee_with_a_page(factory, setup_mcp, callee, confidence=0.9)

    hits = [{"target_path": _SEED, "score": 0.9, "page_type": "file_page"}]
    expanded = await expand_via_graph(hits, SimpleNamespace(session_factory=factory), setup_mcp)

    added = {h["target_path"] for h in expanded if h.get("_expanded_from") == "graph"}
    assert callee in added
    assert f"{callee}::verify" not in {h.get("target_path") for h in expanded}


@pytest.mark.asyncio
async def test_low_confidence_call_neighbour_is_not_expanded(setup_mcp, factory):
    """The projection travels with its guards; without them it adds noise."""
    from repowise.server.mcp_server._answer_pipeline import expand_via_graph

    callee = "src/auth/guessed.py"
    await _seed_callee_with_a_page(factory, setup_mcp, callee, confidence=0.2)

    hits = [{"target_path": _SEED, "score": 0.9, "page_type": "file_page"}]
    expanded = await expand_via_graph(hits, SimpleNamespace(session_factory=factory), setup_mcp)

    assert callee not in {h["target_path"] for h in expanded if h.get("_expanded_from") == "graph"}


@pytest.mark.asyncio
async def test_cross_extension_call_neighbour_is_not_expanded(setup_mcp, factory):
    from repowise.server.mcp_server._answer_pipeline import expand_via_graph

    callee = "src/auth/tokens.ts"
    await _seed_callee_with_a_page(factory, setup_mcp, callee, confidence=0.95)

    hits = [{"target_path": _SEED, "score": 0.9, "page_type": "file_page"}]
    expanded = await expand_via_graph(hits, SimpleNamespace(session_factory=factory), setup_mcp)

    assert callee not in {h["target_path"] for h in expanded if h.get("_expanded_from") == "graph"}
