"""Unit tests for Java + Kotlin type-reference resolution.

Validates that field / parameter / return / generic-arg / heritage type
positions emit ``type_use`` edges across files via JvmWorkspaceIndex.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.type_ref_resolution import resolve_type_refs


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _make_repo_with(
    tmp_path: Path,
    files: dict[str, tuple[str, str]],  # rel -> (language, content)
) -> tuple[dict[str, ParsedFile], ResolverContext]:
    """Parse every file, wire up a graph with file + symbol nodes, return
    (parsed_files, resolver_context) ready for ``resolve_type_refs``.
    """
    parsed_files: dict[str, ParsedFile] = {}
    graph = nx.DiGraph()
    path_set: set[str] = set()
    stem_map: dict[str, list[str]] = {}

    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        fi = _file_info(rel, abs_, lang)
        pf = parse_file(fi, content.encode("utf-8"))
        parsed_files[rel] = pf
        path_set.add(rel)
        stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(rel)

        graph.add_node(rel, node_type="file", path=rel)
        for sym in pf.symbols:
            graph.add_node(sym.id, node_type="symbol", name=sym.name)
            graph.add_edge(rel, sym.id, edge_type="defines")

    ctx = ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=graph,
        repo_path=tmp_path,
    )
    return parsed_files, ctx


def _type_use_edges(graph: nx.DiGraph) -> list[tuple[str, str, str]]:
    return [
        (u, v, ",".join(d.get("type_uses", [])))
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == "type_use"
        or "type_uses" in d
    ]


class TestJavaTypeRefs:
    def test_field_type_resolves_to_sibling(self, tmp_path: Path) -> None:
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/A.java": (
                    "java",
                    "package com.foo;\npublic class A {\n  private Bar bar;\n}\n",
                ),
                "src/main/java/com/foo/Bar.java": (
                    "java",
                    "package com.foo;\npublic class Bar {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            "src/main/java/com/foo/A.java" == u
            and "src/main/java/com/foo/Bar.java" == v
            and "Bar" in names
            for u, v, names in edges
        ), edges

    def test_constructor_param_type_resolves(self, tmp_path: Path) -> None:
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/Svc.java": (
                    "java",
                    "package com.foo;\npublic class Svc {\n  public Svc(Repo r) {}\n}\n",
                ),
                "src/main/java/com/foo/Repo.java": (
                    "java",
                    "package com.foo;\npublic class Repo {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/java/com/foo/Svc.java"
            and v == "src/main/java/com/foo/Repo.java"
            for u, v, _ in edges
        ), edges

    def test_generic_arg_resolves(self, tmp_path: Path) -> None:
        """``List<UserPref>`` should resolve UserPref even though List is a builtin."""
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/Holder.java": (
                    "java",
                    "package com.foo;\nimport java.util.List;\n"
                    "public class Holder {\n  private List<UserPref> prefs;\n}\n",
                ),
                "src/main/java/com/foo/UserPref.java": (
                    "java",
                    "package com.foo;\npublic class UserPref {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/java/com/foo/Holder.java"
            and v == "src/main/java/com/foo/UserPref.java"
            for u, v, _ in edges
        ), edges

    def test_builtin_filtered(self, tmp_path: Path) -> None:
        """``String foo`` should NOT emit a type_use edge."""
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/A.java": (
                    "java",
                    "package com.foo;\npublic class A {\n"
                    "  private String name; private int count;\n}\n",
                ),
                "src/main/java/com/foo/B.java": (
                    "java",
                    "package com.foo;\npublic class B {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        # No String / int → no edges to anywhere
        assert all(v != "src/main/java/com/foo/B.java" for _, v, _ in edges), edges

    def test_extends_emits_type_use(self, tmp_path: Path) -> None:
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/Child.java": (
                    "java",
                    "package com.foo;\npublic class Child extends Parent {}\n",
                ),
                "src/main/java/com/foo/Parent.java": (
                    "java",
                    "package com.foo;\npublic class Parent {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/java/com/foo/Child.java"
            and v == "src/main/java/com/foo/Parent.java"
            for u, v, _ in edges
        ), edges


class TestKotlinTypeRefs:
    def test_primary_ctor_param_resolves(self, tmp_path: Path) -> None:
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/kotlin/com/foo/Svc.kt": (
                    "kotlin",
                    "package com.foo\nclass Svc(val r: Repo)\n",
                ),
                "src/main/kotlin/com/foo/Repo.kt": (
                    "kotlin",
                    "package com.foo\nclass Repo\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/kotlin/com/foo/Svc.kt"
            and v == "src/main/kotlin/com/foo/Repo.kt"
            for u, v, _ in edges
        ), edges

    def test_nullable_type_resolves(self, tmp_path: Path) -> None:
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/kotlin/com/foo/Holder.kt": (
                    "kotlin",
                    "package com.foo\nclass Holder { val r: Repo? = null }\n",
                ),
                "src/main/kotlin/com/foo/Repo.kt": (
                    "kotlin",
                    "package com.foo\nclass Repo\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/kotlin/com/foo/Holder.kt"
            and v == "src/main/kotlin/com/foo/Repo.kt"
            for u, v, _ in edges
        ), edges

    def test_kotlin_resolves_to_java_sibling(self, tmp_path: Path) -> None:
        """Kotlin file references a Java sibling in the same package — no import."""
        parsed, ctx = _make_repo_with(
            tmp_path,
            {
                "src/main/java/com/foo/Caller.kt": (
                    "kotlin",
                    "package com.foo\nclass Caller(val svc: Service)\n",
                ),
                "src/main/java/com/foo/Service.java": (
                    "java",
                    "package com.foo;\npublic class Service {}\n",
                ),
            },
        )
        resolve_type_refs(parsed, ctx, ctx.graph)
        edges = _type_use_edges(ctx.graph)
        assert any(
            u == "src/main/java/com/foo/Caller.kt"
            and v == "src/main/java/com/foo/Service.java"
            for u, v, _ in edges
        ), edges
