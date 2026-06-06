"""Unit tests for C# same-namespace + global-using implicit reference edges."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.languages.csharp_same_namespace import (
    resolve_csharp_same_namespace_refs,
)


def _graph_for(texts: dict[str, str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in texts:
        g.add_node(p, node_type="file")
    return g


class TestSameNamespace:
    def test_sibling_type_reference_produces_edge(self) -> None:
        texts = {
            "src/Core/Order.cs": "namespace Acme.Core;\npublic class Order {}\n",
            "src/Core/OrderService.cs": (
                "namespace Acme.Core;\npublic class OrderService {\n"
                "  private Order _pending;\n}\n"
            ),
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 1
        edge = g["src/Core/OrderService.cs"]["src/Core/Order.cs"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "same_namespace"
        assert edge["imported_names"] == ["Order"]

    def test_zero_using_enum_file_gets_referenced(self) -> None:
        # Polly regression: declaration-only files (enums) have no usings
        # and same-namespace consumers reference them with none either.
        texts = {
            "src/DelayBackoffType.cs": (
                "namespace Polly;\npublic enum DelayBackoffType { Constant }\n"
            ),
            "src/RetryOptions.cs": (
                "namespace Polly;\npublic class RetryOptions {\n"
                "  public DelayBackoffType BackoffType { get; set; }\n}\n"
            ),
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 1
        assert g.has_edge("src/RetryOptions.cs", "src/DelayBackoffType.cs")

    def test_ambiguous_type_produces_no_edge(self) -> None:
        texts = {
            "src/A/Thing.cs": "namespace Acme;\npublic partial class Thing {}\n",
            "src/B/Thing.cs": "namespace Acme;\npublic partial class Thing {}\n",
            "src/User.cs": "namespace Acme;\npublic class User { Thing t; }\n",
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 0

    def test_bcl_name_produces_no_edge(self) -> None:
        texts = {
            "src/Task.cs": "namespace Acme;\npublic class Task {}\n",
            "src/Runner.cs": "namespace Acme;\npublic class Runner { Task T; }\n",
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 0

    def test_alias_using_shadows(self) -> None:
        texts = {
            "src/Helper.cs": "namespace Acme;\npublic class Helper {}\n",
            "src/Consumer.cs": (
                "using Helper = Other.Place.Helper;\n"
                "namespace Acme;\npublic class Consumer { Helper h; }\n"
            ),
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 0

    def test_cross_namespace_name_produces_no_edge(self) -> None:
        texts = {
            "src/Core/Widget.cs": "namespace Acme.Core;\npublic class Widget {}\n",
            "src/Web/Page.cs": "namespace Acme.Web;\npublic class Page { Widget w; }\n",
        }
        g = _graph_for(texts)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 0

    def test_existing_edge_wins(self) -> None:
        texts = {
            "src/Order.cs": "namespace Acme;\npublic class Order {}\n",
            "src/Svc.cs": "namespace Acme;\npublic class Svc { Order o; }\n",
        }
        g = _graph_for(texts)
        g.add_edge("src/Svc.cs", "src/Order.cs", edge_type="imports", confidence=1.0)
        added = resolve_csharp_same_namespace_refs(g, None, texts, None)
        assert added == 0
        assert "hint_source" not in g["src/Svc.cs"]["src/Order.cs"]


class TestGlobalUsings:
    def _repo(self, tmp_path: Path, files: dict[str, str]) -> Path:
        for rel, text in files.items():
            full = tmp_path / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text)
        return tmp_path

    def test_csproj_using_items_link_zero_using_test_file(self, tmp_path: Path) -> None:
        # Polly.Specs regression: the test project declares
        # <Using Include="Polly.Specs.Helpers"/> so spec files carry no
        # per-file usings at all — they must still link to the helpers.
        repo = self._repo(
            tmp_path,
            {
                "test/Specs/Specs.csproj": (
                    "<Project Sdk=\"Microsoft.NET.Sdk\">\n  <ItemGroup>\n"
                    "    <Using Include=\"Acme.Specs.Helpers\" />\n"
                    "  </ItemGroup>\n</Project>\n"
                ),
                "test/Specs/Helpers/Doer.cs": (
                    "namespace Acme.Specs.Helpers;\npublic class Doer {}\n"
                ),
                "test/Specs/RetrySpecs.cs": (
                    "namespace Acme.Specs;\npublic class RetrySpecs {\n"
                    "  public void Runs() { var d = new Doer(); }\n}\n"
                ),
            },
        )
        traverser = FileTraverser(repo)
        parser = ASTParser()
        builder = GraphBuilder(repo_path=repo)
        for fi in traverser.traverse():
            builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
        g = builder.build()
        edge = g.get_edge_data("test/Specs/RetrySpecs.cs", "test/Specs/Helpers/Doer.cs")
        assert edge is not None
        assert edge.get("hint_source") == "global_using"

    def test_explicit_using_namespace_shadows_global_tier(self, tmp_path: Path) -> None:
        # A file that explicitly `using`s a local namespace resolves through
        # the normal import path — the global tier must not double-guess.
        texts = {
            "src/Helpers/Doer.cs": "namespace Acme.Helpers;\npublic class Doer {}\n",
            "src/Consumer.cs": (
                "using Acme.Helpers;\n"
                "namespace Acme;\npublic class Consumer { Doer d; }\n"
            ),
        }
        g = _graph_for(texts)

        class _FakeIndex:
            file_to_project: ClassVar[dict[Path, Path]] = {
                (tmp_path / "src/Consumer.cs").resolve(): Path("proj.csproj")
            }

            def globals_for_project(self, csproj):
                return {"Acme.Helpers"}

        added = resolve_csharp_same_namespace_refs(g, _FakeIndex(), texts, tmp_path)
        assert added == 0
