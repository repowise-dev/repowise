"""Apex REST handlers are bound from their file's module symbol, as Spring routes are."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _graph(repo: Path, files: dict[str, str]) -> nx.DiGraph:
    parser = ASTParser()
    parsed = {}
    for rel, text in files.items():
        (repo / rel).write_text(text)
        fi = FileInfo(
            path=rel,
            abs_path=str(repo / rel),
            language="apex",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parser.parse_file(fi, text.encode())
    builder = GraphBuilder(repo)
    for pf in parsed.values():
        builder.add_file(pf)
    graph = builder.build()
    ctx = ResolverContext(path_set=set(parsed), stem_map={}, graph=nx.DiGraph(), repo_path=repo)
    add_framework_edges(graph, parsed, ctx, tech_stack=[])
    return graph


def _bound(graph: nx.DiGraph) -> set[tuple[str, str]]:
    return {(s, t) for s, t, d in graph.edges(data=True) if d.get("edge_type") == "framework_binds"}


def test_each_verb_method_is_bound_and_a_plain_method_is_not(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        {
            "AccountApi.cls": (
                "@RestResource(urlMapping='/accounts/*')\n"
                "global with sharing class AccountApi {\n"
                "    @HttpGet\n"
                "    global static Account doGet() { return helper(); }\n"
                "    @HttpPost global static void doPost() { }\n"
                "    static Account helper() { return null; }\n"
                "}\n"
            )
        },
    )
    assert _bound(graph) == {
        ("AccountApi.cls::__module__", "AccountApi.cls::AccountApi::doGet"),
        ("AccountApi.cls::__module__", "AccountApi.cls::AccountApi::doPost"),
    }


def test_a_class_without_a_rest_resource_gains_nothing(tmp_path: Path) -> None:
    graph = _graph(
        tmp_path,
        {"Plain.cls": "public class Plain {\n    @HttpGet global static void g() { }\n}\n"},
    )
    assert _bound(graph) == set()
