"""Tests for the behavior-oriented domain graph (generation.domain_graph).

Pure-logic units (context assembly, parsing + node-id validation, render,
fingerprint cache, persistence mapping) plus the async synthesis orchestrator
driven by a stub LLM - no live LLM, no DB.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from repowise.core.generation.domain_graph import (
    DomainGraph,
    DomainNode,
    FlowNode,
    StepNode,
    cache,
    context,
    flatten_edges,
    flatten_nodes,
    parsing,
    render,
    synthesize_domain_graph,
)
from repowise.core.generation.domain_graph.synthesis import (
    DOMAIN_NAMING_SYSTEM,
)


def _node(path: str, *, pagerank: float = 0.0, summary: str = "") -> dict:
    return {"id": f"file:{path}", "filePath": path, "pagerank": pagerank, "summary": summary}


def _layers() -> list[dict]:
    return [
        {"id": "layer:ingestion", "name": "Ingestion", "nodeIds": ["file:a.py", "file:b.py"]},
        {"id": "layer:health", "name": "Health", "nodeIds": ["file:c.py"]},
        {"id": "layer:empty", "name": "Empty", "nodeIds": []},
    ]


def _nodes() -> list[dict]:
    return [
        _node("a.py", pagerank=0.9, summary="parses files"),
        _node("b.py", pagerank=0.1, summary="walks tree"),
        _node("c.py", pagerank=0.5, summary="scores health"),
    ]


# ---------------------------------------------------------------------------
# context (a)
# ---------------------------------------------------------------------------


def test_build_layer_clusters_drops_empty_and_ranks_by_pagerank():
    clusters = context.build_layer_clusters(_layers(), _nodes())
    ids = [c.layer_id for c in clusters]
    assert ids == ["layer:ingestion", "layer:health"]  # empty layer dropped
    ingestion = clusters[0]
    assert ingestion.file_count == 2
    assert ingestion.top_files[0] == "a.py"  # highest pagerank first


def test_member_node_ids_union_sorted():
    members = context.member_node_ids(["layer:ingestion", "layer:health"], _layers(), _nodes())
    assert members == ["file:a.py", "file:b.py", "file:c.py"]


def test_derive_cross_domain_edges_counts_boundary_imports():
    domains = [
        DomainNode(slug="idx", name="Indexing", member_node_ids=["file:a.py", "file:b.py"]),
        DomainNode(slug="hlth", name="Health", member_node_ids=["file:c.py"]),
    ]
    edges = [
        {"source": "file:a.py", "target": "file:c.py"},  # crosses idx -> hlth
        {"source": "file:a.py", "target": "file:b.py"},  # internal to idx
    ]
    cross = context.derive_cross_domain_edges(domains, edges)
    assert len(cross) == 1
    assert (cross[0].source, cross[0].target, cross[0].weight) == ("domain:idx", "domain:hlth", 1)


def test_heaviest_internal_edges_only_within_members():
    members = {"file:a.py", "file:b.py"}
    edges = [
        {"source": "file:a.py", "target": "file:b.py", "weight": 5},
        {"source": "file:a.py", "target": "file:c.py", "weight": 9},  # leaves member set
    ]
    pairs = context.heaviest_internal_edges(members, edges)
    assert pairs == [("a.py", "b.py")]


# ---------------------------------------------------------------------------
# parsing + node-id validation (c)
# ---------------------------------------------------------------------------


def test_parse_domains_rejects_unknown_layers_and_dedupes_claims():
    content = json.dumps(
        {
            "domains": [
                {"slug": "idx", "name": "Indexing", "member_layer_ids": ["layer:ingestion", "layer:bogus"]},
                {"slug": "dup", "name": "Dup", "member_layer_ids": ["layer:ingestion"]},  # already claimed
                {"slug": "hlth", "name": "Health", "member_layer_ids": ["layer:health"]},
            ]
        }
    )
    domains = parsing.parse_domains(content, {"layer:ingestion", "layer:health"})
    slugs = [d.slug for d in domains]
    assert slugs == ["idx", "hlth"]  # bogus rejected, dup dropped (no members left)
    assert domains[0].member_layer_ids == ["layer:ingestion"]


def test_resolve_flows_rejects_hallucinated_node_ids():
    # The key anti-hallucination guarantee: an unknown file path is dropped, and
    # a step left with no real member is removed entirely.
    content = json.dumps(
        {
            "flows": [
                {
                    "slug": "ingest",
                    "name": "Ingest",
                    "steps": [
                        {"order": 1, "name": "parse", "implements": ["a.py", "ghost.py"]},
                        {"order": 2, "name": "phantom", "implements": ["ghost.py"]},
                    ],
                }
            ]
        }
    )
    flows = parsing.resolve_flows(content, {"file:a.py", "file:b.py"})
    assert len(flows) == 1
    steps = flows[0].steps
    assert len(steps) == 1  # phantom step dropped
    assert steps[0].order == 1
    assert steps[0].implements == ["file:a.py"]  # ghost.py never survives
    # The hallucinated id appears nowhere in the resolved graph.
    assert all("ghost" not in nid for s in steps for nid in s.implements)


def test_resolve_flows_renumbers_orders_contiguously():
    content = json.dumps(
        {
            "flows": [
                {
                    "slug": "f",
                    "name": "F",
                    "steps": [
                        {"order": 7, "name": "first", "implements": ["a.py"]},
                        {"order": 9, "name": "second", "implements": ["b.py"]},
                    ],
                }
            ]
        }
    )
    flows = parsing.resolve_flows(content, {"file:a.py", "file:b.py"})
    assert [s.order for s in flows[0].steps] == [1, 2]


def test_resolve_flows_drops_flow_with_no_valid_steps():
    content = json.dumps(
        {"flows": [{"slug": "f", "name": "F", "steps": [{"order": 1, "implements": ["ghost.py"]}]}]}
    )
    assert parsing.resolve_flows(content, {"file:a.py"}) == []


def test_parse_json_tolerates_code_fences():
    assert parsing.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parsing.parse_json("garbage") is None


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_flow_page_lists_steps_and_marks_hotspots():
    domain = DomainNode(slug="idx", name="Indexing")
    flow = FlowNode(
        slug="ingest",
        name="Ingest",
        steps=[StepNode(order=1, name="parse", implements=["file:a.py", "file:b.py"])],
    )
    title, _summary, md = render.render_flow_page(domain, flow, hotspot_node_ids={"file:a.py"})
    assert title == "Ingest (Indexing)"
    assert "`a.py`" in md and "`b.py`" in md
    assert "hotspot" in md  # step touches a hotspot file


def test_render_domain_page_lists_flows():
    domain = DomainNode(
        slug="idx",
        name="Indexing",
        summary="Builds the index.",
        flows=[FlowNode(slug="ingest", name="Ingest", steps=[StepNode(order=1, name="p", implements=["file:a.py"])])],
    )
    title, _summary, md = render.render_domain_page(domain)
    assert title == "Indexing"
    assert "Ingest" in md


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_domain_fingerprint_changes_with_members_and_summaries():
    fp1 = cache.domain_fingerprint(["file:a.py", "file:b.py"], {"file:a.py": "x", "file:b.py": "y"})
    fp2 = cache.domain_fingerprint(["file:b.py", "file:a.py"], {"file:a.py": "x", "file:b.py": "y"})
    fp3 = cache.domain_fingerprint(["file:a.py", "file:b.py"], {"file:a.py": "CHANGED", "file:b.py": "y"})
    assert fp1 == fp2  # order-independent
    assert fp1 != fp3  # summary change invalidates


def test_domain_fingerprint_changes_with_internal_coupling():
    members = ["file:a.py", "file:b.py"]
    summaries = {"file:a.py": "x", "file:b.py": "y"}
    base = cache.domain_fingerprint(members, summaries)
    coupled = cache.domain_fingerprint(members, summaries, [("file:a.py", "file:b.py")])
    assert base != coupled  # a new internal edge invalidates the cache


def test_reuse_flows_matches_on_slug_and_fingerprint():
    flows = [FlowNode(slug="f", name="F", steps=[StepNode(order=1, name="s", implements=["file:a.py"])])]
    prior = DomainGraph(domains=[DomainNode(slug="idx", name="Indexing", fingerprint="abc", flows=flows)])
    match = DomainNode(slug="idx", name="Indexing", fingerprint="abc")
    miss = DomainNode(slug="idx", name="Indexing", fingerprint="different")
    assert cache.reuse_flows(prior, match) is not None
    assert cache.reuse_flows(prior, miss) is None
    assert cache.reuse_flows(None, match) is None


# ---------------------------------------------------------------------------
# persistence mapping (d)
# ---------------------------------------------------------------------------


def test_flatten_nodes_and_edges_shapes():
    g = DomainGraph(
        domains=[
            DomainNode(
                slug="idx",
                name="Indexing",
                member_node_ids=["file:a.py"],
                flows=[FlowNode(slug="ingest", name="Ingest", steps=[StepNode(order=1, name="parse", implements=["file:a.py"])])],
            )
        ]
    )
    nodes = flatten_nodes(g)
    kinds = [n["kind"] for n in nodes]
    assert kinds == ["domain", "flow", "step"]
    step_row = nodes[2]
    assert step_row["node_id"] == "step:idx/ingest/1"
    assert step_row["parent_id"] == "flow:idx/ingest"
    edges = flatten_edges(g)
    types = {e["edge_type"] for e in edges}
    assert types == {"contains_flow", "flow_step"}


# ---------------------------------------------------------------------------
# synthesis (async orchestration with a stub LLM)
# ---------------------------------------------------------------------------


class _StubLLM:
    """Returns canned JSON keyed on whether it's the naming or a flow prompt."""

    def __init__(self, naming: str, flows_by_domain: dict[str, str]):
        self._naming = naming
        self._flows = flows_by_domain
        self.calls = 0

    async def generate(self, system, user, **kwargs):
        self.calls += 1
        if system == DOMAIN_NAMING_SYSTEM:
            return SimpleNamespace(content=self._naming)
        for name, resp in self._flows.items():
            if name in user:
                return SimpleNamespace(content=resp)
        return SimpleNamespace(content='{"flows": []}')


@pytest.mark.asyncio
async def test_synthesize_end_to_end_validates_and_links():
    naming = json.dumps(
        {
            "domains": [
                {"slug": "idx", "name": "Indexing", "member_layer_ids": ["layer:ingestion"]},
                {"slug": "hlth", "name": "Health", "member_layer_ids": ["layer:health"]},
            ]
        }
    )
    flows = {
        "Indexing": json.dumps(
            {"flows": [{"slug": "ingest", "name": "Ingest", "steps": [
                {"order": 1, "name": "parse", "implements": ["a.py", "ghost.py"]}]}]}
        ),
        "Health": json.dumps(
            {"flows": [{"slug": "score", "name": "Score", "steps": [
                {"order": 1, "name": "rate", "implements": ["c.py"]}]}]}
        ),
    }
    llm = _StubLLM(naming, flows)
    edges = [{"source": "file:a.py", "target": "file:c.py"}]  # idx -> hlth
    graph = await synthesize_domain_graph(_layers(), _nodes(), edges, llm)

    assert [d.slug for d in graph.domains] == ["idx", "hlth"]
    idx = graph.domains[0]
    assert idx.flows[0].steps[0].implements == ["file:a.py"]  # ghost rejected
    assert llm.calls == 3  # 1 naming + 2 flow
    assert len(graph.cross_domain) == 1
    assert (graph.cross_domain[0].source, graph.cross_domain[0].target) == ("domain:idx", "domain:hlth")


@pytest.mark.asyncio
async def test_synthesize_drops_orphan_domains():
    naming = json.dumps(
        {"domains": [
            {"slug": "idx", "name": "Indexing", "member_layer_ids": ["layer:ingestion"]},
            {"slug": "hlth", "name": "Health", "member_layer_ids": ["layer:health"]},
        ]}
    )
    flows = {
        "Indexing": json.dumps({"flows": [{"slug": "ingest", "name": "Ingest", "steps": [
            {"order": 1, "name": "parse", "implements": ["a.py"]}]}]}),
        "Health": '{"flows": []}',  # no flows -> Health is an orphan
    }
    graph = await synthesize_domain_graph(_layers(), _nodes(), [], _StubLLM(naming, flows))
    assert [d.slug for d in graph.domains] == ["idx"]  # Health dropped


@pytest.mark.asyncio
async def test_synthesize_empty_when_no_clusters():
    graph = await synthesize_domain_graph([], [], [], _StubLLM("{}", {}))
    assert graph.is_empty()


@pytest.mark.asyncio
async def test_synthesize_reuses_cached_domain_without_llm_call():
    naming = json.dumps(
        {"domains": [{"slug": "idx", "name": "Indexing", "member_layer_ids": ["layer:ingestion"]}]}
    )
    # First run to learn the fingerprint + flows.
    flows = {"Indexing": json.dumps({"flows": [{"slug": "ingest", "name": "Ingest", "steps": [
        {"order": 1, "name": "parse", "implements": ["a.py"]}]}]})}
    first = await synthesize_domain_graph(_layers(), _nodes(), [], _StubLLM(naming, flows))
    prior = first

    # Second run with the prior graph: the flow call must be skipped (only the
    # naming call fires) because the member fingerprint is unchanged.
    llm2 = _StubLLM(naming, flows)
    second = await synthesize_domain_graph(_layers(), _nodes(), [], llm2, prior=prior)
    assert llm2.calls == 1  # naming only; flows reused
    assert second.domains[0].flows[0].steps[0].implements == ["file:a.py"]
