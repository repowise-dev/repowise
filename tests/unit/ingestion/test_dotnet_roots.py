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
    [ISEXTERNALINIT_FILE, ISEXTERNALINIT_BLOCK],
    ids=["file-scoped-polyfill", "block-polyfill"],
)
def test_consumed_file_is_a_root(tmp_path: Path, text: str) -> None:
    assert _roots(tmp_path, {"A.cs": text}) == {"A.cs"}


def test_polyfill_name_in_another_namespace_is_not_a_root(tmp_path: Path) -> None:
    text = "namespace App;\ninternal static class IsExternalInit {}\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_sibling_type_keeps_the_file_judged(tmp_path: Path) -> None:
    text = ISEXTERNALINIT_FILE + "public class DeadSibling { }\n"
    assert _roots(tmp_path, {"A.cs": text}) == set()


@pytest.mark.parametrize(
    "text",
    [
        "// [EfCoreConverter<X>] partial class A;\nSystem.Console.WriteLine(1);\n",
        'var s = "[EfCoreConverter<X>]";\n',
        'var s = @"\n[EfCoreConverter<X>]\npartial class A;\n";\n',
        "/* [EfCoreConverter<X>]\npartial class A; */\nSystem.Console.WriteLine(1);\n",
    ],
    ids=["line-comment", "string", "verbatim-string", "block-comment"],
)
def test_generator_marker_in_a_comment_or_string_is_not_a_root(tmp_path: Path, text: str) -> None:
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


@pytest.mark.parametrize(
    "text",
    [
        "using System.Management.Automation;\nnamespace App;\n"
        '[Cmdlet(VerbsCommon.Get, "Thing")]\npublic class GetThing : PSCmdlet { }\n',
        "using Microsoft.CodeAnalysis;\nnamespace App;\n"
        "[Generator]\npublic class Gen : IIncrementalGenerator { }\n",
        "using Microsoft.CodeAnalysis;\nnamespace App;\n"
        "[GeneratorAttribute(LanguageNames.CSharp)]\npublic class Gen : ISourceGenerator { }\n",
        "namespace App;\n"
        '[System.Management.Automation.CmdletAttribute("Get", "Thing")]\n'
        "public class GetThing { }\n",
        "using BenchmarkDotNet.Attributes;\nnamespace App;\n[MemoryDiagnoser]\n"
        "public class SortBench\n{\n    [Benchmark(Baseline = true)]\n"
        "    public void Sort() { }\n}\n",
    ],
    ids=["cmdlet", "generator", "generator-attribute", "qualified-cmdlet", "benchmark"],
)
def test_class_a_host_loads_by_reflection_is_a_root(tmp_path: Path, text: str) -> None:
    assert _roots(tmp_path, {"A.cs": text}) == {"A.cs"}


def test_benchmark_attributes_without_a_benchmark_method_are_not_a_root(
    tmp_path: Path,
) -> None:
    text = (
        "using BenchmarkDotNet.Attributes;\nnamespace App;\n[MemoryDiagnoser]\n"
        "public class NotABench\n{\n    public void Sort() { }\n}\n"
    )
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_cmdlet_next_to_an_unused_class_keeps_the_file_judged(tmp_path: Path) -> None:
    text = (
        "using System.Management.Automation;\nnamespace App;\n"
        '[Cmdlet(VerbsCommon.Get, "Thing")]\npublic class GetThing : PSCmdlet { }\n'
        "public class DeadSibling { }\n"
    )
    assert _roots(tmp_path, {"A.cs": text}) == set()


def test_benchmark_in_a_same_named_class_of_another_namespace_does_not_count(
    tmp_path: Path,
) -> None:
    text = (
        "using BenchmarkDotNet.Attributes;\n"
        "namespace A\n{\n    public class Bench\n    {\n"
        "        [Benchmark]\n        public void Sort() { }\n    }\n}\n"
        "namespace B\n{\n    public class Bench\n    {\n"
        "        public void Sort() { }\n    }\n}\n"
    )
    assert _roots(tmp_path, {"A.cs": text}) == set()
