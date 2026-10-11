"""Shared synthetic-repo builders for KG curation/invariant tests.

Not a test module (no ``test_`` prefix → not collected). Builds parsed files +
a mock ``GraphBuilder`` and runs the real skeleton + curation, so invariant
tests exercise the production code paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

from repowise.core.analysis.kg_curation import curate_knowledge_graph
from repowise.core.analysis.knowledge_graph import (
    KnowledgeGraphResult,
    build_knowledge_graph_skeleton,
)


@dataclass
class FakeFileInfo:
    path: str
    language: str = "python"
    size_bytes: int = 1000
    is_test: bool = False
    is_config: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False
    line_count: int = 100


@dataclass
class FakeSymbol:
    name: str = "thing"
    kind: str = "function"
    start_line: int = 1
    end_line: int = 10


@dataclass
class FakeParsedFile:
    file_info: FakeFileInfo
    symbols: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)


def _community_info(cid: int, label: str, members: list[str]):
    return SimpleNamespace(
        community_id=cid,
        label=label,
        members=members,
        size=len(members),
        cohesion=0.8,
        dominant_language="python",
    )


def build_repo(
    paths: list[str],
    *,
    tests: set[str] | None = None,
    entries: set[str] | None = None,
    edges: list[tuple[str, str]] | None = None,
    barrels: set[str] | None = None,
):
    """Build a synthetic repo: parsed files + a mock GraphBuilder."""
    import networkx as nx

    tests = tests or set()
    entries = entries or set()
    barrels = barrels or set()

    parsed = []
    g = nx.DiGraph()
    for p in paths:
        is_test, is_entry = p in tests, p in entries
        if p in barrels:
            pf = FakeParsedFile(
                FakeFileInfo(p, is_test=is_test, is_entry_point=is_entry),
                symbols=[],
                imports=[SimpleNamespace(is_reexport=True)],
                exports=["A"],
            )
        else:
            pf = FakeParsedFile(
                FakeFileInfo(p, is_test=is_test, is_entry_point=is_entry),
                symbols=[FakeSymbol()],
            )
        parsed.append(pf)
        attrs = {"node_type": "file", "language": "python"}
        if is_test:
            attrs["is_test"] = True
        if is_entry:
            attrs["is_entry_point"] = True
        g.add_node(p, **attrs)
    for u, v in edges or []:
        g.add_edge(u, v, edge_type="imports", confidence=1.0)

    # One community per file → the "103 layers" pathology curation must absorb.
    communities = {p: i for i, p in enumerate(paths)}
    infos = {i: _community_info(i, f"cluster_{i}", [p]) for i, p in enumerate(paths)}
    pagerank = {p: 1.0 / max(len(paths), 1) for p in paths}

    builder = MagicMock()
    builder.graph.return_value = g
    builder.pagerank.return_value = pagerank
    builder.betweenness_centrality.return_value = {}
    builder.community_detection.return_value = communities
    builder.community_info.return_value = infos
    repo_structure = SimpleNamespace(
        is_monorepo=True, total_files=len(paths), entry_points=sorted(entries)
    )
    return SimpleNamespace(parsed=parsed, builder=builder, repo_structure=repo_structure)


def build_skeleton(repo) -> KnowledgeGraphResult:
    return build_knowledge_graph_skeleton(
        parsed_files=repo.parsed,
        graph_builder=repo.builder,
        repo_structure=repo.repo_structure,
        tech_stack=[],
        external_systems=[],
    )


def curate(repo, **kw) -> KnowledgeGraphResult:
    return curate_knowledge_graph(
        build_skeleton(repo),
        parsed_files=repo.parsed,
        graph_builder=repo.builder,
        repo_structure=repo.repo_structure,
        community_info=repo.builder.community_info(),
        enabled=kw.pop("enabled", True),
        **kw,
    )
