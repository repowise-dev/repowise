"""A C++ member defined out of line belongs to its class, not to ``Class<T>``.

``template <typename T> void Box<T>::f()`` read its parent off the scope's text,
so the member's parent was ``Box<T>`` and a receiver typed ``Box`` never found
it. A specialization's members keep their arguments (a class of their own), on
one line: a multi-line argument list put newlines into symbol ids.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.models import FileInfo


def _symbols(tmp_path: Path, source: str):
    path = tmp_path / "box.h"
    path.write_text(source)
    info = FileInfo(
        path="box.h", abs_path=str(path), language="cpp", size_bytes=len(source),
        git_hash="", last_modified=0.0, is_test=False, is_config=False, is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode()).symbols


def _parents(tmp_path: Path, source: str) -> dict[str, str | None]:
    return {s.id.split("::", 1)[1]: s.parent_name for s in _symbols(tmp_path, source) if s.kind == "method"}


class TestOutOfLineTemplateMember:
    def test_a_primary_template_member_names_the_bare_class(self, tmp_path: Path) -> None:
        parents = _parents(
            tmp_path,
            "template <typename T> struct Box { void f(); };\n"
            "template <typename T> void Box<T>::f() {}\n",
        )
        assert parents == {"Box::f": "Box"}

    def test_parameter_packs_and_non_type_parameters_still_spell_the_primary(
        self, tmp_path: Path
    ) -> None:
        parents = _parents(
            tmp_path,
            "template <typename T, int N, typename... Ts> struct Box { void f(); };\n"
            "template <typename T, int N, typename... Ts> void Box<T, N, Ts...>::f() {}\n",
        )
        assert parents == {"Box::f": "Box"}

    def test_defaults_and_template_template_parameters_still_spell_the_primary(
        self, tmp_path: Path
    ) -> None:
        parents = _parents(
            tmp_path,
            "template <template <typename> class C, typename T = int> struct Box { Box(); };\n"
            "template <template <typename> class C, typename T> Box<C, T>::Box() {}\n",
        )
        assert parents == {"Box::Box": "Box"}

    def test_a_namespace_qualified_scope(self, tmp_path: Path) -> None:
        parents = _parents(tmp_path, "template <typename T> void ns::Box<T>::f() {}\n")
        assert parents == {"Box::f": "Box"}

    def test_a_member_template_of_a_class_template(self, tmp_path: Path) -> None:
        parents = _parents(
            tmp_path,
            "template <typename T> struct Box { template <typename U> void f(U u); };\n"
            "template <typename T> template <typename U> void Box<T>::f(U u) {}\n",
        )
        assert parents == {"Box::f": "Box"}

    def test_an_explicit_specialization_member_keeps_its_arguments(self, tmp_path: Path) -> None:
        """``Box<int>::f`` is another function; merging it into ``Box::f`` would be a wrong edge."""
        parents = _parents(tmp_path, "template <> void Box<ns::Item>::f() {}\n")
        assert parents == {"Box<ns::Item>::f": "Box<ns::Item>"}

    def test_a_partial_specialization_keeps_its_arguments_on_one_line(self, tmp_path: Path) -> None:
        ids = [
            s.id
            for s in _symbols(
                tmp_path,
                "template <typename T>\nstruct Box<\n    T*,\n    int> {\n  void f() {}\n};\n"
                "template <typename T> void Box<T*, long>::g() {}\n",
            )
        ]
        assert ids == ["box.h::Box<T*, int>::f", "box.h::Box<T*, long>::g"]


def test_a_receiver_typed_by_the_class_reaches_the_out_of_line_member(tmp_path: Path) -> None:
    (tmp_path / "box.h").write_text(
        "#pragma once\n"
        "template <typename T> class Box {\n public:\n  void f(int n);\n};\n"
        "template <typename T> void Box<T>::f(int n) {}\n"
        "template <> void Box<char>::f(int n) {}\n"
    )
    (tmp_path / "use.cc").write_text('#include "box.h"\nvoid g(Box<int>& b) { b.f(1); }\n')
    builder = GraphBuilder(repo_path=tmp_path)
    parser = ASTParser()
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    graph = builder.build()
    targets = {
        t for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and str(s).startswith("use.cc::")
    }
    # The declaration and the out-of-line definition are one node.
    assert targets == {"box.h::Box::f"}
    assert not any("Box<T>" in str(n) for n in graph.nodes)
