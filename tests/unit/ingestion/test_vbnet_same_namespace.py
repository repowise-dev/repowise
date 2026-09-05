"""Unit tests for VB.NET same-namespace edges and partial-class co-fragments.

Mirrors tests/unit/ingestion/test_csharp_same_namespace.py. The VB-specific
cases are the ones C# has no equivalent of: a file that declares no namespace
at all and belongs to its project's ``<RootNamespace>``, and the
Form.vb / Form.Designer.vb ``Partial Class`` split.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.languages.vbnet_same_namespace import (
    resolve_vbnet_same_namespace_refs,
)


def _graph_for(texts: dict[str, str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in texts:
        g.add_node(p, node_type="file")
    return g


def _build(repo: Path) -> nx.DiGraph:
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
    return repo


class TestSameNamespace:
    def test_sibling_type_reference_produces_edge(self) -> None:
        texts = {
            "src/Core/Order.vb": (
                "Namespace Acme.Core\n    Public Class Order\n"
                "    End Class\nEnd Namespace\n"
            ),
            "src/Core/OrderService.vb": (
                "Namespace Acme.Core\n    Public Class OrderService\n"
                "        Private _pending As Order\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        added = resolve_vbnet_same_namespace_refs(g, None, texts, None)
        assert added == 1
        edge = g["src/Core/OrderService.vb"]["src/Core/Order.vb"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "same_namespace"
        assert edge["imported_names"] == ["Order"]

    def test_cross_namespace_name_produces_no_edge(self) -> None:
        texts = {
            "src/Core/Widget.vb": (
                "Namespace Acme.Core\n    Public Class Widget\n"
                "    End Class\nEnd Namespace\n"
            ),
            "src/Web/Page1.vb": (
                "Namespace Acme.Web\n    Public Class Page1\n"
                "        Private w As Widget\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0

    def test_ambiguous_type_produces_no_edge(self) -> None:
        texts = {
            "src/A/Thing.vb": (
                "Namespace Acme\n    Public Class Thing\n    End Class\nEnd Namespace\n"
            ),
            "src/B/Thing.vb": (
                "Namespace Acme\n    Public Class Thing\n    End Class\nEnd Namespace\n"
            ),
            "src/User.vb": (
                "Namespace Acme\n    Public Class User\n"
                "        Private t As Thing\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0

    def test_bcl_name_produces_no_edge(self) -> None:
        texts = {
            "src/Task1.vb": (
                "Namespace Acme\n    Public Class Task\n    End Class\nEnd Namespace\n"
            ),
            "src/Runner.vb": (
                "Namespace Acme\n    Public Class Runner\n"
                "        Private T As Task\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0

    def test_imports_alias_shadows(self) -> None:
        texts = {
            "src/Helper.vb": (
                "Namespace Acme\n    Public Class Helper\n    End Class\nEnd Namespace\n"
            ),
            "src/Consumer.vb": (
                "Imports Helper = Other.Place.Helper\n"
                "Namespace Acme\n    Public Class Consumer\n"
                "        Private h As Helper\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0

    def test_existing_edge_wins(self) -> None:
        texts = {
            "src/Order.vb": (
                "Namespace Acme\n    Public Class Order\n    End Class\nEnd Namespace\n"
            ),
            "src/Svc.vb": (
                "Namespace Acme\n    Public Class Svc\n"
                "        Private o As Order\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        g.add_edge("src/Svc.vb", "src/Order.vb", edge_type="imports", confidence=1.0)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0
        assert "hint_source" not in g["src/Svc.vb"]["src/Order.vb"]


class TestRootNamespace:
    def test_namespace_less_siblings_link_through_the_root(self, tmp_path: Path) -> None:
        # The dominant VB shape: neither file writes a Namespace line, both
        # sit in <RootNamespace> and reference each other with no Imports.
        repo = _write(
            tmp_path,
            {
                "App/App.vbproj": (
                    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                    "    <RootNamespace>Acme</RootNamespace>\n"
                    "  </PropertyGroup>\n</Project>\n"
                ),
                "App/Repository.vb": "Public Class Repository\nEnd Class\n",
                "App/Program.vb": (
                    "Public Module Program\n"
                    "    Public Sub Main()\n"
                    "        Dim r As New Repository()\n"
                    "    End Sub\n"
                    "End Module\n"
                ),
            },
        )
        g = _build(repo)
        edge = g.get_edge_data("App/Program.vb", "App/Repository.vb")
        assert edge is not None
        assert edge.get("hint_source") == "same_namespace"

    def test_separate_root_namespaces_do_not_link(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "Lib/Lib.vbproj": (
                    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                    "    <RootNamespace>Lib</RootNamespace>\n"
                    "  </PropertyGroup>\n</Project>\n"
                ),
                "Lib/Repository.vb": "Public Class Repository\nEnd Class\n",
                "App/App.vbproj": (
                    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                    "    <RootNamespace>App</RootNamespace>\n"
                    "  </PropertyGroup>\n</Project>\n"
                ),
                "App/Program.vb": (
                    "Public Module Program\n"
                    "    Public Sub Main()\n"
                    "        Dim r As New Repository()\n"
                    "    End Sub\n"
                    "End Module\n"
                ),
            },
        )
        g = _build(repo)
        edge = g.get_edge_data("App/Program.vb", "Lib/Repository.vb")
        assert edge is None or edge.get("hint_source") != "same_namespace"


class TestPartialClasses:
    def test_designer_split_links_both_ways(self, tmp_path: Path) -> None:
        # The WinForms shape: MainForm.vb and MainForm.Designer.vb are two
        # fragments of one Partial Class and must not read as disconnected.
        repo = _write(
            tmp_path,
            {
                "App/App.vbproj": (
                    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                    "    <RootNamespace>Acme</RootNamespace>\n"
                    "  </PropertyGroup>\n</Project>\n"
                ),
                "App/MainForm.vb": (
                    "Partial Public Class MainForm\n"
                    "    Public Sub Load()\n    End Sub\n"
                    "End Class\n"
                ),
                "App/MainForm.Designer.vb": (
                    "Partial Class MainForm\n"
                    "    Private components As Object\n"
                    "End Class\n"
                ),
            },
        )
        g = _build(repo)
        forward = g.get_edge_data("App/MainForm.vb", "App/MainForm.Designer.vb")
        back = g.get_edge_data("App/MainForm.Designer.vb", "App/MainForm.vb")
        assert forward is not None and forward.get("hint_source") == "partial_class"
        assert back is not None and back.get("hint_source") == "partial_class"

    def test_non_partial_same_name_types_are_not_linked(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "App/App.vbproj": (
                    '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                    "    <RootNamespace>Acme</RootNamespace>\n"
                    "  </PropertyGroup>\n</Project>\n"
                ),
                "App/A/Helper.vb": (
                    "Namespace A\n    Public Class Helper\n    End Class\nEnd Namespace\n"
                ),
                "App/B/Helper.vb": (
                    "Namespace B\n    Public Class Helper\n    End Class\nEnd Namespace\n"
                ),
            },
        )
        g = _build(repo)
        edge = g.get_edge_data("App/A/Helper.vb", "App/B/Helper.vb")
        assert edge is None or edge.get("hint_source") != "partial_class"


class TestUnicodeIdentifiers:
    def test_non_latin_type_names_link(self) -> None:
        """VB.NET identifiers are Unicode and whole estates are written in
        Chinese, where the shared capitalised-ASCII scan matches nothing."""
        texts = {
            "src/\u754c\u9762\u914d\u8272.vb": (
                "Namespace \u529f\u80fd\n"
                "    Public Class \u754c\u9762\u914d\u8272_v6\n"
                "    End Class\nEnd Namespace\n"
            ),
            "src/\u9ad8\u4eae\u5668.vb": (
                "Namespace \u529f\u80fd\n"
                "    Public Class \u9ad8\u4eae\u5668_v6\n"
                "        Private c As \u754c\u9762\u914d\u8272_v6\n"
                "    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 1
        assert g.has_edge("src/\u9ad8\u4eae\u5668.vb", "src/\u754c\u9762\u914d\u8272.vb")

    def test_lowercase_ascii_identifier_is_not_a_candidate(self) -> None:
        """The widened pattern must still exclude lowercase-leading names, or
        every local variable becomes a type-reference candidate."""
        texts = {
            "src/Order.vb": (
                "Namespace Acme\n    Public Class order\n    End Class\nEnd Namespace\n"
            ),
            "src/Svc.vb": (
                "Namespace Acme\n    Public Class Svc\n"
                "        Private o As order\n    End Class\nEnd Namespace\n"
            ),
        }
        g = _graph_for(texts)
        assert resolve_vbnet_same_namespace_refs(g, None, texts, None) == 0
