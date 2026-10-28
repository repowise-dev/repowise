"""A typed C++ receiver reaches a method its class inherits from a base class.

``specs.set_align()`` on a ``format_specs&`` names ``basic_specs::set_align``
when ``format_specs`` derives from ``basic_specs`` and declares no
``set_align`` of its own. The pair index holds only ``(class, method)`` pairs a
class declares, so the call went unresolved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller: str) -> set[str]:
    return {
        t for s, t, d in graph.edges(data=True) if d.get("edge_type") == "calls" and s == caller
    }


_SPECS_H = """#pragma once
class basic_specs {
 public:
  void set_align(int a) { align_ = a; }
  void set_fill(char c) { fill_ = c; }
 private:
  int align_;
  char fill_;
};

struct format_specs : basic_specs {
  int width;
};

struct dynamic_specs : public format_specs {
  int ref;
};
"""


def _repo(root: Path, specs_h: str = _SPECS_H, extra: dict[str, str] | None = None) -> None:
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "specs.h").write_text(specs_h)
    # Reached through another header, so no direct include lets the bare-name
    # tiers answer for the typed one.
    (root / "lib" / "mid.h").write_text('#pragma once\n#include "specs.h"\n')
    (root / "lib" / "parse.cc").write_text(
        '#include "lib/mid.h"\n'
        "void parse_align(format_specs& specs) { specs.set_align(1); }\n"
        "void parse_fill(dynamic_specs& specs) { specs.set_fill('x'); }\n"
    )
    for name, text in (extra or {}).items():
        (root / "lib" / name).write_text(text)


class TestCppBaseClassReceiver:
    def test_typed_receiver_reaches_the_base_class_method(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" in targets

    def test_base_class_edge_is_stamped_as_a_global_typed_receiver(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        graph = _build(tmp_path)
        edge = graph.get_edge_data(
            "lib/parse.cc::parse_align", "lib/specs.h::basic_specs::set_align"
        )
        assert edge["resolution_origin"] == "receiver_typed_global"
        assert edge["confidence"] == 0.75

    def test_reaches_a_grandparent(self, tmp_path: Path) -> None:
        _repo(tmp_path)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_fill")
        assert "lib/specs.h::basic_specs::set_fill" in targets

    def test_an_out_of_line_override_hides_the_base_method(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace("  int width;\n", "  int width;\n  void set_align(int a);\n")
        _repo(
            tmp_path,
            specs_h,
            {"specs.cc": '#include "lib/specs.h"\nvoid format_specs::set_align(int a) {}\n'},
        )
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_an_out_of_line_template_override_hides_the_base_method(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H + (
            "\ntemplate <typename T> struct tspecs : basic_specs {\n  void set_align(int a);\n};\n"
            "template <typename T> void tspecs<T>::set_align(int a) {}\n"
        )
        caller = '#include "lib/mid.h"\nvoid parse_t(tspecs<char>& specs) { specs.set_align(1); }\n'
        _repo(tmp_path, specs_h, {"tparse.cc": caller})
        targets = _call_targets(_build(tmp_path), "lib/tparse.cc::parse_t")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_two_types_of_that_name_refuse(self, tmp_path: Path) -> None:
        other = "#pragma once\nstruct format_specs {\n  int width;\n};\n"
        _repo(tmp_path, extra={"other.h": other})
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert "lib/specs.h::basic_specs::set_align" not in targets

    def test_two_bases_declaring_the_method_refuse(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace(
            "struct format_specs : basic_specs {",
            "class aligner {\n public:\n  void set_align(int a) {}\n};\n\n"
            "struct format_specs : basic_specs, aligner {",
        )
        _repo(tmp_path, specs_h)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert not any(t.endswith("::set_align") for t in targets)

    def test_a_method_no_base_declares_yields_no_edge(self, tmp_path: Path) -> None:
        specs_h = _SPECS_H.replace("  void set_align(int a) { align_ = a; }\n", "")
        _repo(tmp_path, specs_h)
        targets = _call_targets(_build(tmp_path), "lib/parse.cc::parse_align")
        assert not any(t.endswith("::set_align") for t in targets)


@pytest.mark.parametrize("alias", ["using IV = V<int>;", "typedef V<int> IV;"])
@pytest.mark.parametrize("header", [False, True, "guarded", "unparsed guard"])
@pytest.mark.parametrize("declaration", ["auto v = IV();", "auto v = IV{};", "IV v;"])
def test_a_cpp_type_alias_reaches_an_inherited_method(
    tmp_path: Path, alias: str, header: bool | str, declaration: str
) -> None:
    types = (
        "struct B {\n  int size() const { return 1; }\n};\n"
        "template <class T> struct V : B {};\n" + alias + "\n"
    )
    prefix = types
    if header:
        if header == "unparsed guard":
            # A class head split across #if/#else makes tree-sitter wrap the
            # whole guard in an ERROR node, as it does for fmt/format.h.
            types += "#if X\nstruct C {\n#else\nstruct C : B {\n#endif\n  void g();\n};\n"
        if header in ("guarded", "unparsed guard"):
            types = "#ifndef TYPES_H\n#define TYPES_H\n" + types + "#endif\n"
        (tmp_path / "types.h").write_text(types)
        prefix = '#include "types.h"\n'
    (tmp_path / "a.cpp").write_text(prefix + "void f() {\n  " + declaration + "\n  v.size();\n}\n")
    owner = "types.h" if header else "a.cpp"
    assert owner + "::B::size" in _call_targets(_build(tmp_path), "a.cpp::f")


@pytest.mark.parametrize("target", ["V", "V<int>"])
def test_a_cpp_alias_chain_reaches_the_target_class(tmp_path: Path, target: str) -> None:
    (tmp_path / "a.cpp").write_text(
        "template <class T = int> struct V {\n  void run() {}\n};\n"
        f"using First = {target};\nusing Second = First;\ntypedef Second Last;\n"
        "void f() {\n  Last v;\n  v.run();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == {"a.cpp::V::run"}


@pytest.mark.parametrize(
    "aliases",
    [
        "using IV = std::string;",
        "using IV = Missing;",
        "using IV = Other;\nusing Other = IV;",
        "using IV = IV;",
    ],
)
def test_an_unresolved_cpp_alias_invents_no_method_edge(tmp_path: Path, aliases: str) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct string {\n  void size() {}\n};\n"
        "struct Other {\n  void size() {}\n};\n"
        + aliases + "\nvoid f() {\n  IV v;\n  v.size();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == set()


@pytest.mark.parametrize("target", ["std::chrono::weekday", "Missing"])
def test_a_preprocessor_alias_keeps_its_fallback_class(tmp_path: Path, target: str) -> None:
    # fmt/chrono.h: the alias and the class are the two sides of one #if.
    (tmp_path / "a.cpp").write_text(
        "#if defined(HAS_WEEKDAY)\nusing weekday = " + target + ";\n#else\n"
        "class weekday {\n public:\n  unsigned c_encoding() const { return 0; }\n};\n#endif\n"
        "unsigned f(weekday wd) {\n  return wd.c_encoding();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == {"a.cpp::weekday::c_encoding"}


def test_a_cpp_alias_chain_has_a_depth_limit(tmp_path: Path) -> None:
    aliases = "".join(f"using A{i} = A{i + 1};\n" for i in range(20))
    (tmp_path / "a.cpp").write_text(
        "struct A20 {\n  void run() {}\n};\n" + aliases
        + "void f() {\n  A0 v;\n  v.run();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == set()


def test_two_headers_declaring_the_same_cpp_alias_refuse(tmp_path: Path) -> None:
    for name in ("A", "B"):
        (tmp_path / f"{name}.h").write_text(
            f"struct {name} {{\n  void run() {{}}\n}};\nusing IV = {name};\n"
        )
    (tmp_path / "a.cpp").write_text(
        '#include "A.h"\n#include "B.h"\nvoid f() {\n  IV v;\n  v.run();\n}\n'
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == set()


def test_an_alias_cannot_borrow_a_type_from_an_unincluded_file(tmp_path: Path) -> None:
    (tmp_path / "other.h").write_text("struct Missing {\n  void run() {}\n};\n")
    (tmp_path / "a.cpp").write_text(
        "using IV = Missing;\nvoid f() {\n  IV v;\n  v.run();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == set()


@pytest.mark.parametrize(
    "declaration",
    ["auto v = IV().other();", "auto v = IV(make()).other();", "auto v = IV() + other;"],
)
def test_a_cpp_constructor_expression_does_not_type_its_result(
    tmp_path: Path, declaration: str
) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct V {\n  void run() {}\n};\nusing IV = V;\n"
        "void f() {\n  " + declaration + "\n  v.run();\n}\n"
    )
    assert "a.cpp::V::run" not in _call_targets(_build(tmp_path), "a.cpp::f")


@pytest.mark.parametrize("scope", ["namespace ns", "void other()"])
def test_a_scoped_cpp_alias_does_not_hide_an_unrelated_global_class(
    tmp_path: Path, scope: str
) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct Peer {\n  void run() {}\n};\n"
        + scope + " {\n  using Peer = External;\n}\n"
        + "void f(Peer& peer) {\n  peer.run();\n}\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::f") == {"a.cpp::Peer::run"}


@pytest.mark.parametrize("scoped", [False, True])
def test_an_in_scope_cpp_alias_cannot_borrow_a_global_class(
    tmp_path: Path, scoped: bool
) -> None:
    body = "void f() {\n  using Peer = External;\n  Peer peer;\n  peer.run();\n}\n"
    if scoped:
        body = "namespace ns {\n" + body + "}\n"
    (tmp_path / "a.cpp").write_text("struct Peer {\n  void run() {}\n};\n" + body)
    assert "a.cpp::Peer::run" not in _call_targets(_build(tmp_path), "a.cpp::f")


@pytest.mark.parametrize("before", [False, True])
@pytest.mark.parametrize("compact", [False, True])
def test_a_cpp_parameter_keeps_the_type_bound_before_a_body_alias(
    tmp_path: Path, before: bool, compact: bool
) -> None:
    body = ["using Peer = External;", "peer.run();"]
    if not before:
        body.reverse()
    function = "void f(Peer& peer) {\n" + "\n".join(body) + "\n}\n"
    if compact:
        function = function.replace("\n", " ")
    (tmp_path / "a.cpp").write_text("struct Peer {\n  void run() {}\n};\n" + function)
    assert _call_targets(_build(tmp_path), "a.cpp::f") == {"a.cpp::Peer::run"}


def test_a_cpp_field_keeps_the_type_bound_before_a_method_alias(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text(
        "struct Peer {\n  void run() {}\n};\n"
        "struct Host {\n  Peer peer;\n  void f() {\n"
        "    using Peer = External;\n    peer.run();\n  }\n};\n"
    )
    assert _call_targets(_build(tmp_path), "a.cpp::Host::f") == {"a.cpp::Peer::run"}


class TestOtherLanguagesSkipTheCppBranch:
    def test_python_typed_receiver_never_asks_the_cpp_base_class_rule(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from repowise.core.ingestion.call_receiver_typing import ReceiverTypingMixin as M

        def _boom(*_a, **_k):
            raise AssertionError("C++ base-class rule used for a non-C++ file")

        monkeypatch.setattr(M, "_cpp_base_class_target", _boom, raising=True)
        (tmp_path / "base.py").write_text("class Base:\n    def set_align(self, a):\n        pass\n")
        (tmp_path / "derived.py").write_text(
            "from base import Base\n\nclass Specs(Base):\n    width = 0\n"
        )
        (tmp_path / "use.py").write_text(
            "from derived import Specs\n\n"
            "def run(specs: Specs):\n    specs.set_align(1)\n"
        )
        _build(tmp_path)

    def test_java_typed_receiver_never_asks_the_cpp_base_class_rule(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from repowise.core.ingestion.call_receiver_typing import ReceiverTypingMixin as M

        def _boom(*_a, **_k):
            raise AssertionError("C++ base-class rule used for a non-C++ file")

        monkeypatch.setattr(M, "_cpp_base_class_target", _boom, raising=True)
        (tmp_path / "Base.java").write_text("class Base {\n  void setAlign(int a) {}\n}\n")
        (tmp_path / "Specs.java").write_text("class Specs extends Base {\n  int width;\n}\n")
        (tmp_path / "Use.java").write_text(
            "class Use {\n  void run(Specs specs) { specs.setAlign(1); }\n}\n"
        )
        _build(tmp_path)


class TestCppBaseClassTargetGuards:
    """Each refusal of ``_cpp_base_class_target`` on its own, with an ancestor always on offer."""

    _HIT = "lib/specs.h::basic_specs::set_align"

    def _resolver(self, **state):
        from types import SimpleNamespace

        base = {
            "_global_methods": {},
            "_global_symbols": {},
            "_symbols_by_id": {},
            "_declared_types_named": lambda _name: [
                ("lib/specs.h", ("lib/specs.h::format_specs", "0"), 0)
            ],
            "_ancestor_method": lambda *_a: self._HIT,
        }
        return SimpleNamespace(**{**base, **state})

    def _target(self, resolver):
        from repowise.core.ingestion.call_receiver_typing import ReceiverTypingMixin
        from repowise.core.ingestion.models import CallSite

        call = CallSite("set_align", "specs", "lib/parse.cc::parse_align", 2, 1)
        return ReceiverTypingMixin._cpp_base_class_target(
            resolver, call, "lib/parse.cc::parse_align", "format_specs"
        )

    def test_answers_with_the_ancestor_when_no_guard_fires(self) -> None:
        assert self._target(self._resolver()) == (self._HIT, "global")

    def test_a_declared_class_method_pair_refuses(self) -> None:
        pairs = {("format_specs", "set_align"): [("lib/specs.h", "x")]}
        assert self._target(self._resolver(_global_methods=pairs)) is None

    def test_a_same_named_class_declaring_the_method_refuses(self) -> None:
        from types import SimpleNamespace

        sym_id = "lib/specs.cc::format_specs<T>::set_align"
        resolver = self._resolver(
            _global_symbols={"set_align": [sym_id]},
            _symbols_by_id={sym_id: SimpleNamespace(parent_name="format_specs<T>")},
        )
        assert self._target(resolver) is None

    def test_an_ambiguous_type_name_refuses(self) -> None:
        two = [
            ("lib/specs.h", ("lib/specs.h::format_specs", "0"), 0),
            ("lib/other.h", ("lib/other.h::format_specs", "0"), 0),
        ]
        assert self._target(self._resolver(_declared_types_named=lambda _name: two)) is None
