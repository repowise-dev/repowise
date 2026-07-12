"""Flow-path expansion: connect question-anchored endpoints over the graph.

get_answer's plain 1-hop expansion rescues "right module, wrong file" ranking
misses but cannot reach a far endpoint the question names that retrieval buried
or dropped. ``expand_via_flow_path`` resolves 2+ endpoints (a named symbol's
file, a module named by basename), runs a bounded bidirectional BFS between them
over imports + projected calls edges, and surfaces the files on the path so both
endpoints land in the served top-5.

These lock the mechanism deterministically (no LLM, no synthesis): the far
endpoint is injected when absent and boosted when buried, calls edges are
projected to file granularity, the confidence floor and cross-language guard
hold, and a single-endpoint question is a no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime

from repowise.core.persistence.models import GraphEdge, Page, WikiSymbol
from repowise.server.mcp_server._flow_path import expand_via_flow_path

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


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


def _symbol(rid: str, name: str, file_path: str) -> WikiSymbol:
    return WikiSymbol(
        id=f"{file_path}::{name}",
        repository_id=rid,
        file_path=file_path,
        symbol_id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind="function",
        signature=f"def {name}()",
        start_line=1,
        end_line=5,
        docstring="",
        visibility="public",
        is_async=False,
        complexity_estimate=1,
        language="python",
        parent_name=None,
    )


def _edge(rid: str, src: str, tgt: str, etype: str, conf: float = 1.0) -> GraphEdge:
    return GraphEdge(
        id=f"{src}->{tgt}:{etype}",
        repository_id=rid,
        source_node_id=src,
        target_node_id=tgt,
        edge_type=etype,
        confidence=conf,
    )


# Paths kept short but multi-segment so basename-stem matching is exercised.
_ANSWER = "pkg/server/answer.py"
_RETRIEVAL = "pkg/server/retrieval.py"
_CONF = "pkg/server/confidence.py"


async def test_injects_absent_far_endpoint(session, repo_id):
    """A named module endpoint absent from hits is injected onto the path."""
    session.add(_page(repo_id, _ANSWER))
    session.add(_page(repo_id, _RETRIEVAL))
    session.add(_symbol(repo_id, "get_answer", _ANSWER))
    session.add(_edge(repo_id, _ANSWER, _RETRIEVAL, "imports"))
    await session.commit()

    hits = [{"target_path": _ANSWER, "score": 5.0}]
    combined, paths = await expand_via_flow_path(
        session, repo_id, hits, "how does get_answer use retrieval", {"get_answer"}
    )

    injected = [h for h in combined if h.get("target_path") == _RETRIEVAL]
    assert len(injected) == 1
    assert injected[0]["_expanded_from"] == "flow"
    assert paths and [_ANSWER, _RETRIEVAL] in paths


async def test_boosts_buried_endpoint_without_duplicating(session, repo_id):
    """A far endpoint already ranked below the cap is boosted, not duplicated."""
    session.add(_page(repo_id, _ANSWER))
    session.add(_page(repo_id, _RETRIEVAL))
    session.add(_symbol(repo_id, "get_answer", _ANSWER))
    session.add(_edge(repo_id, _ANSWER, _RETRIEVAL, "imports"))
    await session.commit()

    hits = [
        {"target_path": _ANSWER, "score": 5.0},
        {"target_path": _RETRIEVAL, "score": 0.05},
    ]
    combined, _ = await expand_via_flow_path(
        session, repo_id, hits, "how does get_answer use retrieval", {"get_answer"}
    )

    retr = [h for h in combined if h.get("target_path") == _RETRIEVAL]
    assert len(retr) == 1  # boosted in place, no duplicate row
    assert retr[0]["score"] > 3.0  # 0.7 * 5.0, was 0.05 — now rides into top-5
    assert combined[1]["target_path"] == _RETRIEVAL  # rose to rank 2


async def test_single_anchor_is_noop(session, repo_id):
    """One resolvable endpoint is a plain 'what is X' — the stage does nothing."""
    session.add(_page(repo_id, _ANSWER))
    session.add(_symbol(repo_id, "get_answer", _ANSWER))
    await session.commit()

    hits = [{"target_path": _ANSWER, "score": 5.0}]
    combined, paths = await expand_via_flow_path(
        session, repo_id, hits, "how does get_answer work", {"get_answer"}
    )
    assert combined is hits
    assert paths == []


async def test_calls_edges_projected_to_files(session, repo_id):
    """An endpoint reachable only via a symbol-level calls edge still connects."""
    parser, builder = "pkg/core/parser.py", "pkg/core/builder.py"
    session.add(_page(repo_id, parser))
    session.add(_page(repo_id, builder))
    # No import edge — only a symbol-to-symbol calls edge between the two files.
    session.add(_edge(repo_id, f"{parser}::parse", f"{builder}::build", "calls", conf=0.9))
    await session.commit()

    hits = [{"target_path": parser, "score": 5.0}]
    combined, paths = await expand_via_flow_path(
        session, repo_id, hits, "how does parser feed builder", set()
    )
    assert any(h.get("target_path") == builder for h in combined)
    assert paths


async def test_low_confidence_call_edge_is_dropped(session, repo_id):
    """A calls edge below the confidence floor is not a traversable path."""
    parser, builder = "pkg/core/parser.py", "pkg/core/builder.py"
    session.add(_page(repo_id, parser))
    session.add(_page(repo_id, builder))
    session.add(_edge(repo_id, f"{parser}::parse", f"{builder}::build", "calls", conf=0.2))
    await session.commit()

    hits = [{"target_path": parser, "score": 5.0}]
    combined, paths = await expand_via_flow_path(
        session, repo_id, hits, "how does parser feed builder", set()
    )
    assert paths == []
    assert combined is hits


async def test_multi_hop_path_within_depth_cap(session, repo_id):
    """A 3-hop chain between two named endpoints is found (bidirectional BFS)."""
    parser, mid1, mid2, builder = (
        "pkg/parser.py",
        "pkg/resolver.py",
        "pkg/planner.py",
        "pkg/builder.py",
    )
    for p in (parser, mid1, mid2, builder):
        session.add(_page(repo_id, p))
    # Chain parser - resolver - planner - builder (3 hops); parser and builder
    # are the named endpoints, the interior is discovered by the search.
    session.add(_edge(repo_id, parser, mid1, "imports"))
    session.add(_edge(repo_id, mid1, mid2, "imports"))
    session.add(_edge(repo_id, mid2, builder, "imports"))
    await session.commit()

    hits = [{"target_path": parser, "score": 5.0}]
    combined, paths = await expand_via_flow_path(
        session, repo_id, hits, "how does parser feed the builder", set()
    )
    assert any(h.get("target_path") == builder for h in combined)
    # Undirected search: the path may be reported from either endpoint.
    assert paths and paths[0] in ([parser, mid1, mid2, builder], [builder, mid2, mid1, parser])


async def test_cross_language_anchor_dropped(session, repo_id):
    """A same-named file in another language is not an endpoint of a .py flow."""
    sym_py = "pkg/server/symbols.py"
    sym_ts = "web/src/symbols.ts"
    session.add(_page(repo_id, _ANSWER))
    session.add(_page(repo_id, sym_py))
    session.add(_page(repo_id, sym_ts))
    session.add(_symbol(repo_id, "get_answer", _ANSWER))
    session.add(_edge(repo_id, _ANSWER, sym_py, "imports"))
    await session.commit()

    hits = [{"target_path": _ANSWER, "score": 5.0}]
    combined, _ = await expand_via_flow_path(
        session, repo_id, hits, "how does get_answer build the symbols body", {"get_answer"}
    )
    paths = {h.get("target_path") for h in combined}
    assert sym_py in paths  # in-language endpoint injected
    assert sym_ts not in paths  # off-language same-name match dropped
