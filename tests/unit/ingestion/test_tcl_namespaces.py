"""Tcl procs are owned by their namespace, and calls resolve as Tcl looks them up.

A proc's namespace comes from the ``namespace eval`` blocks around it and from
its own qualified name (``proc ::x::go``, ``proc a::go``). An unqualified call
looks in the caller's namespace, then the global one; a relative ``a::f`` in
namespace ``N`` tries ``N::a::f`` then ``::a::f``; an absolute ``::a::f`` names
one proc.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


_LANGUAGES = {".tcl": "tcl", ".py": "python"}


def _build(tmp_path: Path, sources: dict[str, str], *, snapshot: bool = False):
    builder = GraphBuilder(tmp_path)
    for rel, text in sources.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_path),
            language=_LANGUAGES[Path(rel).suffix],
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(_PARSER.parse_file(info, text.encode("utf-8")))
    if snapshot:
        builder.set_source_map({path: text.encode("utf-8") for path, text in sources.items()})
        for rel in sources:
            (tmp_path / rel).write_text("# different working tree\n", encoding="utf-8")
    return builder.build()


def _calls_from(graph, caller: str) -> dict[str, str]:
    return {
        v: d.get("resolution_origin")
        for u, v, d in graph.edges(data=True)
        if u == caller and d.get("edge_type") == "calls"
    }


def _symbols(tmp_path: Path, source: str) -> list[tuple[str, str | None]]:
    info = FileInfo(
        path="lib/x.tcl",
        abs_path=str(tmp_path / "x.tcl"),
        language="tcl",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return [(s.id, s.parent_name) for s in _PARSER.parse_file(info, source.encode()).symbols]


def test_procs_carry_their_namespace(tmp_path: Path) -> None:
    source = (
        "namespace eval a {\n"
        "  namespace eval b { proc f {} { return 1 } }\n"
        "  proc ::x::go {} { return 2 }\n"
        "  proc c::h {} { return 3 }\n"
        "}\n"
        "proc top {} { return 4 }\n"
    )
    assert _symbols(tmp_path, source) == [
        ("lib/x.tcl::a::b::f", "a::b"),
        ("lib/x.tcl::x::go", "x"),
        ("lib/x.tcl::a::c::h", "a::c"),
        ("lib/x.tcl::top", None),
    ]


def test_an_unqualified_call_looks_in_its_namespace_then_global(tmp_path: Path) -> None:
    sources = {
        "lib/u.tcl": (
            "proc helper {} { return 0 }\n"
            "proc only_global {} { return 0 }\n"
            "namespace eval ns {\n"
            "  proc helper {} { return 1 }\n"
            "  proc run {} {\n    helper\n    only_global\n  }\n"
            "}\n"
            "proc top {} { helper }\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "lib/u.tcl::ns::run") == {
        "lib/u.tcl::ns::helper": "enclosing_class",
        "lib/u.tcl::only_global": "same_file",
    }
    assert _calls_from(graph, "lib/u.tcl::top") == {"lib/u.tcl::helper": "same_file"}


def test_same_named_procs_in_two_namespaces_stay_apart(tmp_path: Path) -> None:
    sources = {
        "lib/a.tcl": (
            "namespace eval a {\n  proc helper {} { return 1 }\n  proc run {} { helper }\n}\n"
            "namespace eval b {\n  proc helper {} { return 2 }\n  proc run {} { helper }\n}\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "lib/a.tcl::a::run") == {"lib/a.tcl::a::helper": "enclosing_class"}
    assert _calls_from(graph, "lib/a.tcl::b::run") == {"lib/a.tcl::b::helper": "enclosing_class"}


def test_qualified_calls_name_their_namespace(tmp_path: Path) -> None:
    sources = {
        "lib/x.tcl": "proc ::x::go {} { return 1 }\nproc ::y::go {} { return 2 }\n",
        "bin/main.tcl": (
            "source lib/x.tcl\n"
            "proc main {} {\n  ::y::go\n  x::go\n}\n"
            "namespace eval x {\n  proc local {} { y::go }\n}\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "bin/main.tcl::main") == {
        "lib/x.tcl::y::go": "scoped_name",
        "lib/x.tcl::x::go": "scoped_name",
    }
    # Inside x, ``y::go`` is tried as x::y::go first, then ::y::go.
    assert _calls_from(graph, "bin/main.tcl::x::local") == {"lib/x.tcl::y::go": "scoped_name"}


def test_an_ambiguous_namespaced_proc_does_not_fall_back_to_global(tmp_path: Path) -> None:
    sources = {
        "lib/a.tcl": "namespace eval N { proc helper {} { return 1 } }\n",
        "lib/b.tcl": "namespace eval N { proc helper {} { return 2 } }\n",
        "lib/g.tcl": "proc helper {} { return 0 }\n",
        "bin/run.tcl": (
            "source ../lib/a.tcl\nsource ../lib/b.tcl\nsource ../lib/g.tcl\n"
            "namespace eval N { proc run {} { helper } }\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/run.tcl::N::run") == {}


def test_a_namespaced_call_binds_only_tcl_procs(tmp_path: Path) -> None:
    sources = {
        "lib/ns.py": "class ns:\n    def helper(self):\n        return 1\n",
        "bin/run.tcl": "proc run {} { ::ns::helper }\n",
    }
    assert _calls_from(_build(tmp_path, sources), "bin/run.tcl::run") == {}


def test_a_transitively_sourced_proc_is_preferred(tmp_path: Path) -> None:
    sources = {
        "bin/helpers.tcl": "proc helper {} { return 1 }\n",
        "bin/loader.tcl": "source helpers.tcl\n",
        "other/helpers.tcl": "proc helper {} { return 2 }\n",
        "bin/main.tcl": "source loader.tcl\nproc run {} { helper }\n",
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::run") == {
        "bin/helpers.tcl::helper": "import_merged"
    }


def test_sourced_procs_are_defined_in_the_sourcing_namespace(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc helper {} { return 1 }\n",
        "bin/main.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval N {\n  source lib.tcl\n  proc run {} { helper }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::run") == {
        "bin/lib.tcl::helper": "enclosing_class"
    }


def test_transitively_sourced_procs_follow_nested_namespace_context(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "namespace eval Inner { proc helper {} { return 1 } }\n",
        "bin/loader.tcl": "namespace eval Middle { source lib.tcl }\n",
        "bin/main.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval N {\n"
            "  source loader.tcl\n"
            "  namespace eval Middle { namespace eval Inner { proc run {} { helper } } }\n"
            "}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::Middle::Inner::run") == {
        "bin/lib.tcl::Inner::helper": "enclosing_class"
    }


def test_sourcing_an_absolute_proc_does_not_move_it(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc ::helper {} { return 1 }\n",
        "bin/main.tcl": (
            "namespace eval N {\n"
            "  proc helper {} { return 0 }\n"
            "  source lib.tcl\n"
            "  proc run {} { helper }\n"
            "  proc absolute {} { ::helper }\n"
            "}\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "bin/main.tcl::N::run") == {
        "bin/main.tcl::N::helper": "enclosing_class"
    }
    assert _calls_from(graph, "bin/main.tcl::N::absolute") == {
        "bin/lib.tcl::helper": "import_merged"
    }


def test_same_file_sourced_into_two_namespaces(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc helper {} { return 1 }\n",
        "bin/main.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval A {\n  source lib.tcl\n  proc run {} { helper }\n}\n"
            "namespace eval B {\n  source lib.tcl\n  proc run {} { helper }\n}\n"
        ),
    }
    graph = _build(tmp_path, sources)
    for namespace in ("A", "B"):
        assert _calls_from(graph, f"bin/main.tcl::{namespace}::run") == {
            "bin/lib.tcl::helper": "enclosing_class"
        }


def test_sourced_relative_and_absolute_namespace_definitions(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": (
            "namespace eval Inner { proc relative {} {} }\n"
            "namespace eval ::Other { proc absolute {} {} }\n"
        ),
        "bin/main.tcl": (
            "namespace eval N {\n  source lib.tcl\n"
            "  proc run {} {\n    Inner::relative\n    ::Other::absolute\n  }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::run") == {
        "bin/lib.tcl::Inner::relative": "scoped_name",
        "bin/lib.tcl::Other::absolute": "scoped_name",
    }


def test_sourced_namespace_ambiguity_does_not_bind_global(tmp_path: Path) -> None:
    sources = {
        "bin/a.tcl": "proc helper {} {}\n",
        "bin/b.tcl": "proc helper {} {}\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  source a.tcl\n  source b.tcl\n"
            "  proc run {} { helper }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::run") == {}


def test_source_cycles_terminate_and_keep_the_sourcing_namespace(tmp_path: Path) -> None:
    sources = {
        "bin/a.tcl": "source b.tcl\nproc helper {} {}\n",
        "bin/b.tcl": "source a.tcl\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  source a.tcl\n  proc run {} { helper }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::run") == {
        "bin/a.tcl::helper": "enclosing_class"
    }


def test_source_namespace_uses_analysed_snapshot(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc helper {} {}\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  source lib.tcl\n  proc run {} { helper }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources, snapshot=True), "bin/main.tcl::N::run") == {
        "bin/lib.tcl::helper": "enclosing_class"
    }


def test_call_inside_sourced_proc_uses_its_execution_namespace(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc run {} { helper }\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  proc helper {} {}\n  source lib.tcl\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/lib.tcl::run") == {
        "bin/main.tcl::N::helper": "enclosing_class"
    }


def test_module_call_in_sourced_file_uses_its_execution_namespace(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "helper\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  proc helper {} {}\n  source lib.tcl\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/lib.tcl::__module__") == {
        "bin/main.tcl::N::helper": "enclosing_class"
    }


def test_sourced_proc_with_different_context_targets_is_unresolved(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc run {} { helper }\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval A {\n  proc helper {} {}\n  source lib.tcl\n}\n"
            "namespace eval B {\n  proc helper {} {}\n  source lib.tcl\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/lib.tcl::run") == {}


def test_sourced_proc_in_multiple_contexts_can_resolve_the_same_definition(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc helper {} {}\nproc run {} { helper }\n",
        "bin/main.tcl": (
            "namespace eval A {\n  source lib.tcl\n}\n"
            "namespace eval B {\n  source lib.tcl\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/lib.tcl::run") == {
        "bin/lib.tcl::helper": "enclosing_class"
    }


def test_source_inside_proc_uses_proc_namespace(tmp_path: Path) -> None:
    sources = {
        "bin/lib.tcl": "proc helper {} {}\n",
        "bin/main.tcl": (
            "proc helper {} {}\n"
            "namespace eval N {\n  proc load {} {\n    source lib.tcl\n  }\n"
            "  proc run {} { helper }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/main.tcl::N::run") == {
        "bin/lib.tcl::helper": "enclosing_class"
    }


def test_repo_wide_tcl_guess_has_shared_global_unique_confidence(tmp_path: Path) -> None:
    graph = _build(tmp_path, {"a.tcl": "proc helper {} {}\n", "b.tcl": "proc run {} { helper }\n"})
    edge = graph.edges["b.tcl::run", "a.tcl::helper"]
    assert edge["resolution_origin"] == "global_unique"
    assert edge["confidence"] == 0.50


def test_a_namespace_eval_inside_a_proc_moves_its_calls(tmp_path: Path) -> None:
    sources = {
        "lib/u.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval N { proc helper {} { return 1 } }\n"
            "proc run {} {\n  namespace eval N {\n    helper\n  }\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "lib/u.tcl::run") == {
        "lib/u.tcl::N::helper": "enclosing_class"
    }


def test_a_quoted_namespace_name_is_the_same_namespace(tmp_path: Path) -> None:
    source = 'namespace eval "N" { proc f {} {} }\nnamespace eval N { proc g {} {} }\n'
    assert _symbols(tmp_path, source) == [("lib/x.tcl::N::f", "N"), ("lib/x.tcl::N::g", "N")]


def test_a_module_level_call_runs_in_its_namespace_eval(tmp_path: Path) -> None:
    sources = {
        "lib/u.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval N {\n  proc helper {} { return 1 }\n  helper\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "lib/u.tcl::__module__") == {
        "lib/u.tcl::N::helper": "enclosing_class"
    }


def test_an_absolute_global_namespace_eval_leaves_the_procs_namespace(tmp_path: Path) -> None:
    sources = {
        "lib/u.tcl": (
            "proc helper {} { return 0 }\n"
            "namespace eval N {\n"
            "  proc helper {} { return 1 }\n"
            "  proc run {} {\n    namespace eval :: { helper }\n  }\n"
            "}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "lib/u.tcl::N::run") == {
        "lib/u.tcl::helper": "same_file"
    }


def test_a_proc_defined_by_a_proc_lands_in_its_namespace(tmp_path: Path) -> None:
    source = (
        "namespace eval A {}\n"
        "proc ::A::setup {} {\n"
        "  proc helper {} { return 1 }\n"
        "  namespace eval inner { proc deep {} { return 2 } }\n"
        "}\n"
    )
    assert _symbols(tmp_path, source) == [
        ("lib/x.tcl::A::setup", "A"),
        ("lib/x.tcl::A::helper", "A"),
        ("lib/x.tcl::A::inner::deep", "A::inner"),
    ]


def test_a_recursive_proc_calls_itself(tmp_path: Path) -> None:
    sources = {
        "lib/m.tcl": "proc fact {n} {\n  if {$n < 2} { return 1 }\n  return [expr {$n * [fact [expr {$n - 1}]]}]\n}\n",
    }
    assert _calls_from(_build(tmp_path, sources), "lib/m.tcl::fact") == {
        "lib/m.tcl::fact": "same_file"
    }
