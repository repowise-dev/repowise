"""A Go symbol named by its own package, or by an importer through the package, is used.

Shapes from syft, hugo and gitleaks, each reported as dead: a parser passed as a
value from a sibling file, a lexer state function returned by another, a generic
constructor called as ``pkg.New[T](...)``, and a build-tag twin whose calls
resolve to the other file.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.go_name_uses import drop_go_package_uses
from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from tests.unit.dead_code._helpers import _build_graph


def _finding(path, name, span, kind=DeadCodeKind.UNUSED_INTERNAL):
    start, end = span
    return DeadCodeFindingData(
        kind=kind,
        file_path=path,
        symbol_name=name,
        symbol_kind="function",
        confidence=0.65,
        reason="test",
        last_commit_at=None,
        commit_count_90d=0,
        lines=end - start + 1,
        evidence=[],
        safe_to_delete=False,
        primary_owner=None,
        age_days=None,
        start_line=start,
        end_line=end,
    )


def _go(path, *symbols):
    return {
        "language": "go",
        "symbols": [
            {"name": n, "kind": "function", "language": "go", "start_line": s, "end_line": e}
            for n, s, e in symbols
        ],
    }


_PARSER = b"""package dart

// parsePubspec is a parser function for pubspec.yaml contents.
func parsePubspec(path string) error {
\treturn parsePubspec(path)
}
"""


def _kept(findings, source_map, graph):
    return {f.symbol_name for f in drop_go_package_uses(findings, source_map, graph)}


def _parser_case(sibling: bytes, sibling_path="dart/cataloger.go"):
    source = {"dart/parse_pubspec.go": _PARSER, sibling_path: sibling}
    graph = _build_graph(
        {"dart/parse_pubspec.go": _go("dart/parse_pubspec.go", ("parsePubspec", 4, 6)),
         sibling_path: {"language": "go"}}
    )
    return _kept([_finding("dart/parse_pubspec.go", "parsePubspec", (4, 6))], source, graph)


def test_value_passed_from_a_sibling_file_is_a_use():
    sibling = b"package dart\n\nfunc New() C {\n\treturn C{}.WithParserByGlobs(parsePubspec, \"**/pubspec.yaml\")\n}\n"
    assert _parser_case(sibling) == set()


def test_doc_comment_and_recursive_call_are_not_uses():
    assert _parser_case(b"package dart\n") == {"parsePubspec"}


def test_comment_and_strings_in_a_sibling_are_not_uses():
    sibling = b'package dart\n\n// see parsePubspec\nvar s = `parsePubspec runs` + "parsePubspec here"\n'
    assert _parser_case(sibling) == {"parsePubspec"}
    one_word = b'package dart\n\nvar s = map[string]int{"parsePubspec": 1, `parsePubspec`: 2}\n'
    assert _parser_case(one_word) == {"parsePubspec"}


def test_a_local_declared_with_the_same_name_is_not_a_use():
    local = b"package dart\n\nfunc f() {\n\tparsePubspec, err := 5, 6\n\t_ = err\n}\n"
    assert _parser_case(local) == {"parsePubspec"}


def test_selector_on_something_else_is_not_a_use():
    assert _parser_case(b"package dart\n\nvar f = other.parsePubspec\n") == {"parsePubspec"}


def test_another_package_in_the_same_directory_is_not_the_package():
    sibling = b"package dart_test\n\nvar f = parsePubspec\n"
    assert _parser_case(sibling, "dart/parse_pubspec_test.go") == {"parsePubspec"}


def test_another_directory_is_not_the_package():
    assert _parser_case(b"package dart\n\nvar f = parsePubspec\n", "yaml/x.go") == {"parsePubspec"}


_QUEUE = b"package types\n\nfunc NewEvictingQueue[T any](size int) *Queue[T] {\n\treturn nil\n}\n"


def _importer_case(importer: bytes, names):
    source = {"common/types/queue.go": _QUEUE, "commands/server.go": importer}
    graph = _build_graph(
        {"common/types/queue.go": _go("common/types/queue.go", ("NewEvictingQueue", 3, 5)),
         "commands/server.go": {"language": "go"}},
        [("commands/server.go", "common/types/queue.go",
          {"edge_type": "imports", "imported_names": names})],
    )
    finding = _finding("common/types/queue.go", "NewEvictingQueue", (3, 5), DeadCodeKind.UNUSED_EXPORT)
    return _kept([finding], source, graph)


def test_generic_call_through_the_import_qualifier_is_a_use():
    importer = b"package commands\n\nvar q = types.NewEvictingQueue[string](20)\n"
    assert _importer_case(importer, ["types"]) == set()


def test_aliased_import_qualifier_is_a_use():
    importer = b"package commands\n\nvar q = ht.NewEvictingQueue[string](20)\n"
    assert _importer_case(importer, ["ht"]) == set()


def test_other_qualifier_or_bare_name_in_an_importer_is_not_a_use():
    importer = b"package commands\n\nvar q = other.NewEvictingQueue[string](20)\nvar r = NewEvictingQueue\n"
    assert _importer_case(importer, ["types"]) == {"NewEvictingQueue"}


_STDLIB = b"//go:build !re2\n\npackage regexp\n\nfunc MustCompile(s string) *R {\n\treturn nil\n}\n"
_RE2 = b"//go:build re2\n\npackage regexp\n\nfunc MustCompile(s string) *R {\n\treturn nil\n}\n"


def _twin_case(importer: bytes):
    source = {"regexp/stdlib.go": _STDLIB, "regexp/re2.go": _RE2, "config/rule.go": importer}
    graph = _build_graph(
        {"regexp/stdlib.go": _go("regexp/stdlib.go", ("MustCompile", 5, 7)),
         "regexp/re2.go": _go("regexp/re2.go", ("MustCompile", 5, 7)),
         "config/rule.go": {"language": "go"}},
        [("config/rule.go", "regexp/stdlib.go", {"edge_type": "imports", "imported_names": ["regexp"]}),
         ("config/rule.go", "regexp/re2.go", {"edge_type": "imports", "imported_names": ["regexp"]})],
    )
    finding = _finding("regexp/re2.go", "MustCompile", (5, 7), DeadCodeKind.UNUSED_EXPORT)
    return _kept([finding], source, graph)


def test_build_tag_twin_shares_the_importers_use():
    assert _twin_case(b"package config\n\nvar r = regexp.MustCompile(`x`)\n") == set()


def test_build_tag_twin_declaration_alone_is_not_a_use():
    assert _twin_case(b"package config\n") == {"MustCompile"}


def test_same_named_method_header_is_not_a_use():
    source = {
        "hexec/exec.go": b"package hexec\n\nfunc New() *Exec {\n\treturn nil\n}\n",
        "hexec/run.go": b"package hexec\n\nfunc (e *Exec) New(name string) {\n}\n",
    }
    graph = _build_graph(
        {"hexec/exec.go": _go("hexec/exec.go", ("New", 3, 5)),
         "hexec/run.go": _go("hexec/run.go", ("Exec::New", 3, 4))}
    )
    graph.nodes["hexec/run.go::Exec::New"]["name"] = "New"
    finding = _finding("hexec/exec.go", "New", (3, 5), DeadCodeKind.UNUSED_EXPORT)
    assert _kept([finding], source, graph) == {"New"}


def test_analyzer_drops_a_go_parser_registered_in_a_sibling():
    source = {
        "dart/parse_pubspec.go": _PARSER.replace(b"// parsePubspec is", b"// It is"),
        "dart/cataloger.go": b"package dart\n\nvar parsers = []P{parsePubspec}\n",
        "dart/unused.go": b"package dart\n\nfunc stale() {\n}\n",
    }
    graph = _build_graph(
        {
            "dart/parse_pubspec.go": _go("dart/parse_pubspec.go", ("parsePubspec", 4, 6)),
            "dart/cataloger.go": {"language": "go"},
            "dart/unused.go": _go("dart/unused.go", ("stale", 3, 4)),
        }
    )
    for node in ("dart/parse_pubspec.go::parsePubspec", "dart/unused.go::stale"):
        graph.nodes[node]["visibility"] = "private"
    report = DeadCodeAnalyzer(graph, source_map=source).analyze(
        {"detect_unreachable_files": False, "detect_zombie_packages": False}
    )
    assert {f.symbol_name for f in report.findings} == {"stale"}


def _member_case(sibling: bytes, kind: str):
    source = {
        "compare/compare.go": b"package compare\n\nfunc ProbablyEq(a, b any) bool {\n\treturn a == b\n}\n",
        "compare/types.go": sibling,
    }
    graph = _build_graph(
        {"compare/compare.go": _go("compare/compare.go", ("ProbablyEq", 3, 5)),
         "compare/types.go": {"language": "go", "symbols": [
             {"name": "T", "kind": kind, "language": "go", "start_line": 3, "end_line": 6}]}}
    )
    finding = _finding("compare/compare.go", "ProbablyEq", (3, 5), DeadCodeKind.UNUSED_EXPORT)
    return _kept([finding], source, graph)


def test_interface_method_spec_is_not_a_use():
    sibling = b"package compare\n\ntype T interface {\n\t// For internal use.\n\tProbablyEq(other any) bool\n}\n"
    assert _member_case(sibling, "interface") == {"ProbablyEq"}


def test_struct_field_name_is_not_a_use_but_its_type_is():
    fields = b"package compare\n\ntype T struct {\n\tProbablyEq, Other int\n\tName ProbablyEq\n}\n"
    assert _member_case(fields, "struct") == set()
    only_field = b"package compare\n\ntype T struct {\n\tProbablyEq, Other int\n\tName string\n}\n"
    assert _member_case(only_field, "struct") == {"ProbablyEq"}


def test_embedded_type_is_a_use():
    sibling = b"package compare\n\ntype T struct {\n\tProbablyEq\n\tName string\n}\n"
    assert _member_case(sibling, "struct") == set()


def test_method_receiver_is_not_a_use_but_its_signature_is():
    receiver = b"package compare\n\nfunc (p *ProbablyEq) Name() string {\n\treturn \"\"\n}\n"
    assert _member_case(receiver, "function") == {"ProbablyEq"}
    returns = b"package compare\n\nfunc (p *ProbablyEq) Clone() *ProbablyEq {\n\treturn p\n}\n"
    assert _member_case(returns, "function") == set()


def test_composite_literal_key_is_not_a_use_but_its_value_and_a_case_arm_are():
    key = b"package compare\n\nvar m = M{\n\tProbablyEq: probablyEq(1),\n}\n"
    assert _member_case(key, "function") == {"ProbablyEq"}
    value = b"package compare\n\nvar m = M{Fn: ProbablyEq}\n"
    assert _member_case(value, "function") == set()
    arm = b"package compare\n\nfunc f(v any) {\n\tswitch v.(type) {\n\tcase *ProbablyEq:\n\t}\n}\n"
    assert _member_case(arm, "function") == set()


def test_versioned_import_path_is_qualified_by_the_package_clause():
    source = {"lib/v2/queue.go": _QUEUE.replace(b"package types", b"package lib"),
              "app/main.go": b"package app\n\nvar q = lib.NewEvictingQueue[int](1)\n"}
    graph = _build_graph(
        {"lib/v2/queue.go": _go("lib/v2/queue.go", ("NewEvictingQueue", 3, 5)),
         "app/main.go": {"language": "go"}},
        [("app/main.go", "lib/v2/queue.go", {"edge_type": "imports", "imported_names": ["v2"]})],
    )
    finding = _finding("lib/v2/queue.go", "NewEvictingQueue", (3, 5), DeadCodeKind.UNUSED_EXPORT)
    assert _kept([finding], source, graph) == set()


def test_real_pipeline_dot_import_and_qualified_generic_call(tmp_path):
    root = tmp_path
    # The dot import is covered before this pass ("*" counts every export as
    # imported); the generic call through ``gen.`` has no call edge at all.
    files = {
        "go.mod": "module example.com/m\n\ngo 1.22\n",
        "dot/dot.go": "package dot\n\nfunc DotUsed() int { return 1 }\n",
        "gen/gen.go": (
            "package gen\n\nfunc NewQ[T any](n int) []T { return make([]T, n) }\n\n"
            "func GenDead() int { return 2 }\n"
        ),
        "cmd/app/main.go": (
            'package main\n\nimport (\n\t. "example.com/m/dot"\n\t"example.com/m/gen"\n)\n\n'
            "func main() {\n\t_ = DotUsed()\n\t_ = gen.NewQ[string](2)\n}\n"
        ),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    traverser, parser, builder = FileTraverser(root), ASTParser(), GraphBuilder(repo_path=root)
    source_map = {}
    for fi in traverser.traverse():
        source_map[fi.path] = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, source_map[fi.path]))
    graph = builder.build()
    assert graph["cmd/app/main.go"]["dot/dot.go"]["imported_names"] == ["*"]
    report = DeadCodeAnalyzer(graph, source_map=source_map).analyze({"min_confidence": 0.0})
    exports = {f.symbol_name for f in report.findings if f.kind is DeadCodeKind.UNUSED_EXPORT}
    assert "GenDead" in exports
    assert not exports & {"DotUsed", "NewQ"}
