"""Unit tests for JVM same-package implicit reference edges."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.languages.jvm_same_package import (
    resolve_jvm_same_package_refs,
)
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_workspace import build_jvm_workspace_index


def _write(repo: Path, rel_path: str, text: str) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text)
    return rel_path


def _setup(repo: Path, files: dict[str, str]):
    """Write *files*, build index + graph with file nodes, return (graph, index, texts)."""
    paths = [_write(repo, rel, text) for rel, text in files.items()]
    ctx = ResolverContext(path_set=set(paths), stem_map={}, graph=nx.DiGraph(), repo_path=repo)
    index = build_jvm_workspace_index(ctx)
    graph = nx.DiGraph()
    for p in paths:
        graph.add_node(p, node_type="file")
    texts = {rel: (repo / rel).read_text() for rel in paths}
    return graph, index, texts


class TestJavaSamePackage:
    def test_sibling_type_reference_produces_edge(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Order.java": "package com.shop;\npublic class Order {}\n",
            "src/com/shop/OrderService.java": (
                "package com.shop;\npublic class OrderService {\n"
                "  Order pending;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 1
        edge = graph["src/com/shop/OrderService.java"]["src/com/shop/Order.java"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "same_package"
        assert edge["imported_names"] == ["Order"]

    def test_self_reference_produces_no_edge(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Order.java": (
                "package com.shop;\npublic class Order {\n"
                "  Order next;\n}\n"
            ),
            "src/com/shop/Other.java": "package com.shop;\npublic class Other {}\n",
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_stdlib_name_produces_no_edge(self, tmp_path: Path) -> None:
        # A sibling declaring a java.lang name must not attract edges:
        # bare ``String`` references are overwhelmingly stdlib.
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/String.java": "package com.shop;\npublic class String {}\n",
            "src/com/shop/User.java": (
                "package com.shop;\npublic class User {\n"
                "  String name;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_ambiguous_type_produces_no_edge(self, tmp_path: Path) -> None:
        # Two package files declare ``Config`` — edge to neither.
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/A.java": "package com.shop;\npublic class A {}\nclass Config {}\n",
            "src/com/shop/B.java": "package com.shop;\npublic class B {}\nclass Config {}\n",
            "src/com/shop/C.java": (
                "package com.shop;\npublic class C {\n  Config cfg;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_cross_package_name_produces_no_edge(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Order.java": "package com.shop;\npublic class Order {}\n",
            "src/com/billing/Invoice.java": (
                "package com.billing;\npublic class Invoice {\n  Order o;\n}\n"
            ),
            "src/com/billing/Other.java": "package com.billing;\npublic class Other {}\n",
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_explicit_import_shadows_same_package(self, tmp_path: Path) -> None:
        # A imports com.other.Helper explicitly — JVM semantics say the
        # import wins over the same-package Helper, so no sibling edge.
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Helper.java": "package com.shop;\npublic class Helper {}\n",
            "src/com/shop/User.java": (
                "package com.shop;\nimport com.other.Helper;\n"
                "public class User {\n  Helper h;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_existing_import_edge_wins(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Order.java": "package com.shop;\npublic class Order {}\n",
            "src/com/shop/OrderService.java": (
                "package com.shop;\npublic class OrderService {\n  Order o;\n}\n"
            ),
        })
        graph.add_edge(
            "src/com/shop/OrderService.java",
            "src/com/shop/Order.java",
            edge_type="imports",
            imported_names=["Order"],
        )
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0
        edge = graph["src/com/shop/OrderService.java"]["src/com/shop/Order.java"]
        assert "hint_source" not in edge

    def test_multiple_types_aggregate_on_one_edge(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/shop/Models.java": (
                "package com.shop;\npublic class Order {}\nclass Invoice {}\n"
            ),
            "src/com/shop/Service.java": (
                "package com.shop;\npublic class Service {\n"
                "  Order o;\n  Invoice i;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 1
        edge = graph["src/com/shop/Service.java"]["src/com/shop/Models.java"]
        assert sorted(edge["imported_names"]) == ["Invoice", "Order"]


class TestKotlinSamePackage:
    def test_kotlin_class_and_object_references(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/app/Config.kt": "package com.app\n\nobject Config\n",
            "src/com/app/Model.kt": "package com.app\n\nclass Model\n",
            "src/com/app/Service.kt": (
                "package com.app\n\nclass Service {\n"
                "  val cfg = Config\n  val m = Model()\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 2
        assert graph.has_edge("src/com/app/Service.kt", "src/com/app/Config.kt")
        assert graph.has_edge("src/com/app/Service.kt", "src/com/app/Model.kt")

    def test_kotlin_enum_class_resolves(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/app/Status.kt": "package com.app\n\nenum class Status { OPEN, DONE }\n",
            "src/com/app/Ticket.kt": (
                "package com.app\n\nclass Ticket {\n  var status: Status = Status.OPEN\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 1
        assert graph.has_edge("src/com/app/Ticket.kt", "src/com/app/Status.kt")

    def test_kotlin_stdlib_names_skipped(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/app/Pair.kt": "package com.app\n\nclass Pair\n",
            "src/com/app/Use.kt": (
                "package com.app\n\nclass Use {\n  val p: Pair<Int, Int>? = null\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_kotlin_top_level_function_not_linked(self, tmp_path: Path) -> None:
        # Types only: lowercase top-level callables are out of scope.
        graph, index, texts = _setup(tmp_path, {
            "src/com/app/Helpers.kt": "package com.app\n\nfun formatName(n: String) = n\n",
            "src/com/app/Service.kt": (
                "package com.app\n\nclass Service {\n"
                "  fun run() = formatName(\"x\")\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_cross_language_java_to_kotlin_sibling(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/com/app/Repo.kt": "package com.app\n\nclass Repo\n",
            "src/com/app/Service.java": (
                "package com.app;\npublic class Service {\n  Repo repo;\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 1
        assert graph.has_edge("src/com/app/Service.java", "src/com/app/Repo.kt")


class TestScalaSamePackage:
    def test_scala_sibling_trait_reference(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/main/scala/com/app/Api.scala": "package com.app\n\ntrait Api\n",
            "src/main/scala/com/app/Service.scala": (
                "package com.app\n\nclass Service extends Api\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 1
        assert graph.has_edge(
            "src/main/scala/com/app/Service.scala", "src/main/scala/com/app/Api.scala"
        )

    def test_scala_predef_names_skipped(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/main/scala/com/app/Option.scala": "package com.app\n\nclass Option\n",
            "src/main/scala/com/app/Use.scala": (
                "package com.app\n\nclass Use {\n  val o: Option[Int] = None\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0

    def test_scala_brace_import_shadows_same_package(self, tmp_path: Path) -> None:
        graph, index, texts = _setup(tmp_path, {
            "src/main/scala/com/app/Helper.scala": "package com.app\n\nclass Helper\n",
            "src/main/scala/com/app/Use.scala": (
                "package com.app\n\nimport com.other.{Helper, Misc}\n\n"
                "class Use {\n  val h = new Helper()\n}\n"
            ),
        })
        added = resolve_jvm_same_package_refs(graph, index, texts)
        assert added == 0
