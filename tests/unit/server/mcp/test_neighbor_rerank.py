"""Retrieval-seeded neighborhood re-rank: lock the mechanism deterministically.

``expand_via_neighbor_rerank`` seeds from the top hits, walks 1-2 hops over the
imports/calls graph (skipping hubs/plumbing), and re-ranks the reached files by
fused embedding+lexical relevance so a far endpoint that lost the corpus-wide
retrieval takes the bottom served slot — without reordering the protected head.

No LLM, no real embedder: the two stores are stubbed to a fixed order, so these
assert the graph walk, the head protection, and the buried/absent injection paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.persistence.models import GraphEdge, Page
from repowise.server.mcp_server._neighbor_rerank import (
    _rrf_fuse,
    _walk_neighborhood,
    expand_via_neighbor_rerank,
    is_flow_question,
)

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_Q = "how does a changed file travel from ingestion into the persisted store"


def _page(rid: str, path: str) -> Page:
    return Page(
        id=f"file_page:{path}",
        repository_id=rid,
        page_type="file_page",
        title=path,
        content=f"# {path}",
        summary=f"summary of {path}",
        target_path=path,
        source_hash=path,
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        confidence=1.0,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _edge(rid: str, src: str, tgt: str) -> GraphEdge:
    return GraphEdge(
        id=f"{src}->{tgt}",
        repository_id=rid,
        source_node_id=src,
        target_node_id=tgt,
        edge_type="imports",
        confidence=1.0,
    )


class _Result:
    def __init__(self, target_path: str) -> None:
        self.target_path = target_path


class _Searcher:
    """Returns a fixed ranked order, ignoring the query — deterministic fusion."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def search(self, question: str, limit: int = 10) -> list[_Result]:
        return [_Result(p) for p in self._order]


class _Ctx:
    def __init__(self, order: list[str]) -> None:
        self.vector_store = _Searcher(order)
        self.fts = _Searcher(order)


_SEEDS = [f"pkg/mod/s{i}.py" for i in range(5)]
_FAR = "pkg/other/far.py"


def _hits(paths_scores: list[tuple[str, float]]) -> list[dict]:
    return [{"target_path": p, "score": s, "_sources": {"fts"}} for p, s in paths_scores]


# --- pure helpers ----------------------------------------------------------


def test_gate_fires_on_flow_shapes():
    assert is_flow_question(_Q)
    assert is_flow_question("how does retrieval feed synthesis in get_answer")
    assert is_flow_question("how does a changed file get its symbols re-persisted")


def test_gate_off_for_terse_or_plain_questions():
    assert not is_flow_question("verify_and_heal")
    assert not is_flow_question("where is filter_dicts_by_key defined")
    assert not is_flow_question("")


def test_rrf_rewards_agreement():
    # Present in both lists beats present in one; higher rank beats lower.
    fused = _rrf_fuse(["a", "b", "c"], ["a", "b", "d"])
    assert fused["a"] > fused["b"]  # both agree, a ranked higher
    assert fused["b"] > fused["c"]  # b in both lists, c in one


def test_walk_skips_hub_and_plumbing():
    # s0 -> hub (degree 20) -> gold; the walk lands on hub but never expands it,
    # so gold behind the hub is not reached.
    adj: dict[str, set[str]] = {}
    for i in range(20):
        adj.setdefault("pkg/hub.py", set()).add(f"pkg/leaf{i}.py")
        adj.setdefault(f"pkg/leaf{i}.py", set()).add("pkg/hub.py")
    adj.setdefault("pkg/mod/s0.py", set()).add("pkg/hub.py")
    adj["pkg/hub.py"].add("pkg/mod/s0.py")
    adj.setdefault("pkg/hub.py", set()).add("pkg/gold.py")
    adj.setdefault("pkg/gold.py", set()).add("pkg/hub.py")

    pool = _walk_neighborhood(adj, ["pkg/mod/s0.py"])
    assert "pkg/hub.py" in pool  # reached (could itself be the gold)
    assert "pkg/gold.py" not in pool  # but not expanded through


# --- full stage ------------------------------------------------------------


async def test_absent_far_endpoint_injected(session, repo_id):
    """A relevant far endpoint absent from hits is fabricated into a served slot."""
    session.add(_page(repo_id, _FAR))
    session.add(_edge(repo_id, _SEEDS[4], _FAR))
    await session.commit()

    hits = _hits([(p, 5.0 - i) for i, p in enumerate(_SEEDS)])
    ctx = _Ctx([_FAR, *_SEEDS])  # far ranks first in the neighbourhood
    out = await expand_via_neighbor_rerank(session, repo_id, hits, _Q, ctx)

    far = [h for h in out if h["target_path"] == _FAR]
    assert len(far) == 1
    assert far[0]["_expanded_from"] == "neighbor"
    assert out.index(far[0]) < len(_SEEDS)  # rose into the served top-5


async def test_buried_endpoint_rises(session, repo_id):
    """A far endpoint retrieved but ranked below the cap rises, not duplicated."""
    session.add(_edge(repo_id, _SEEDS[4], _FAR))
    await session.commit()

    hits = _hits([(p, 5.0 - i) for i, p in enumerate(_SEEDS)] + [(_FAR, 0.01)])
    ctx = _Ctx([_FAR, *_SEEDS])
    out = await expand_via_neighbor_rerank(session, repo_id, hits, _Q, ctx)

    far = [h for h in out if h["target_path"] == _FAR]
    assert len(far) == 1  # boosted in place, no duplicate
    assert out.index(far[0]) < len(_SEEDS)  # was rank 6, now in top-5


async def test_head_is_never_reordered(session, repo_id):
    """The top hits keep their positions — an already-served gold can't be demoted."""
    session.add(_page(repo_id, _FAR))
    session.add(_edge(repo_id, _SEEDS[4], _FAR))
    await session.commit()

    hits = _hits([(p, 5.0 - i) for i, p in enumerate(_SEEDS)])
    ctx = _Ctx([_FAR, *_SEEDS])
    out = await expand_via_neighbor_rerank(session, repo_id, hits, _Q, ctx)

    assert [h["target_path"] for h in out[:4]] == _SEEDS[:4]


async def test_non_flow_question_is_noop(session, repo_id):
    hits = _hits([(p, 5.0 - i) for i, p in enumerate(_SEEDS)])
    ctx = _Ctx([_FAR, *_SEEDS])
    out = await expand_via_neighbor_rerank(session, repo_id, hits, "where is s0 defined", ctx)
    assert out is hits


async def test_empty_stores_are_noop(session, repo_id):
    """No relevance signal (both stores empty) → nothing to rank → no-op."""
    session.add(_edge(repo_id, _SEEDS[4], _FAR))
    await session.commit()

    hits = _hits([(p, 5.0 - i) for i, p in enumerate(_SEEDS)])
    ctx = _Ctx([])  # searchers return nothing
    out = await expand_via_neighbor_rerank(session, repo_id, hits, _Q, ctx)
    assert out is hits
