"""C# files the compiler or a build tool consumes are marked reachability roots."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.framework_edges import add_framework_edges

from .test_csharp_framework_edges import _build_parsed_files, _ctx

ISEXTERNALINIT_FILE = (
    "namespace System.Runtime.CompilerServices;\ninternal static class IsExternalInit {}\n"
)
ISEXTERNALINIT_BLOCK = (
    "namespace System.Runtime.CompilerServices { internal static class IsExternalInit {} }\n"
)
FACTORY = (
    "using Microsoft.EntityFrameworkCore.Design;\n"
    "namespace App.Data;\n"
    "public class AppDbContextFactory : IDesignTimeDbContextFactory<AppDbContext> {\n"
    "    public AppDbContext CreateDbContext(string[] args) => null;\n}\n"
)


def _roots(tmp_path: Path, files: dict[str, str]) -> set[str]:
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    parsed = _build_parsed_files(tmp_path)
    graph = nx.DiGraph()
    graph.add_nodes_from(parsed)
    add_framework_edges(graph, parsed, _ctx(tmp_path, parsed))
    return {p for p, d in graph.nodes(data=True) if d.get("is_reachability_root")}


@pytest.mark.parametrize(
    "text",
    [ISEXTERNALINIT_FILE, ISEXTERNALINIT_BLOCK, FACTORY],
    ids=["file-scoped-polyfill", "block-polyfill", "design-time-factory"],
)
def test_consumed_file_is_a_root(tmp_path: Path, text: str) -> None:
    assert _roots(tmp_path, {"A.cs": text}) == {"A.cs"}


def test_polyfill_name_in_another_namespace_is_not_a_root(tmp_path: Path) -> None:
    text = "namespace App;\ninternal static class IsExternalInit {}\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_ordinary_factory_without_the_interface_is_not_a_root(tmp_path: Path) -> None:
    text = "namespace App;\npublic class WidgetFactory : IWidgetFactory { }\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_sibling_type_keeps_the_file_judged(tmp_path: Path) -> None:
    text = FACTORY + "public class DeadSibling { }\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_generator_marker_on_a_bodiless_partial_class(tmp_path: Path) -> None:
    text = "using Vogen;\nnamespace App;\n[EfCoreConverter<UserId>]\ninternal partial class Conv;\n"
    assert _roots(tmp_path, {"A.cs": text}) == {"A.cs"}


def test_generator_marker_on_a_braced_partial_class(tmp_path: Path) -> None:
    text = (
        "using Vogen;\nnamespace App;\n[EfCoreConverter<UserId>]\ninternal partial class Conv { }\n"
    )
    assert _roots(tmp_path, {"A.cs": text}) == {"A.cs"}


def test_file_with_no_marker_and_no_type_is_not_a_root(tmp_path: Path) -> None:
    assert _roots(tmp_path, {"A.cs": "System.Console.WriteLine(1);\n"}) == set()


def test_file_based_app_with_directives_is_a_root(tmp_path: Path) -> None:
    text = '#:sdk Cake.Sdk\n#:package Newtonsoft.Json\nvar t = Argument<string>("target");\n'
    assert _roots(tmp_path, {"cake.cs": text}) == {"cake.cs"}


def test_directives_after_a_leading_comment_still_count(tmp_path: Path) -> None:
    text = "// build script\n#:package Spectre.Console\nSystem.Console.WriteLine(1);\n"
    assert _roots(tmp_path, {"run.cs": text}) == {"run.cs"}


def test_preprocessor_lines_are_not_app_directives(tmp_path: Path) -> None:
    text = "#pragma warning disable CS1591\n#region R\nSystem.Console.WriteLine(1);\n#endregion\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()
