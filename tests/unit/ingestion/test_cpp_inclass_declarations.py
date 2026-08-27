"""A C++ in-class method declaration must become a symbol.

``cpp.scm`` had patterns for ``function_definition`` and for a file-scope
``declaration``, but none for ``field_declaration``, which is how every in-class
declaration parses. A CONCRETE class survived it, because its out-of-line
``Cls::m`` definition in the ``.cc`` is a ``function_definition``; an ABSTRACT
one has no definition anywhere and reached the method index empty, so
``leveldb::Iterator`` held no ``Seek`` and a same-named nested class answered
every ``Iterator*`` call unopposed.

Adding symbols feeds two mechanisms that had nothing to be wrong with before,
and the last two classes here are those two: the declaration/definition
pairing, whose name key proves nothing about the class, and the bare-name
index, which no method should be reachable through.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.models import FileInfo


def _parse(source: str, path: str = "lib/thing.h"):
    info = FileInfo(
        path=path,
        abs_path=path,
        language="cpp",
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime(2026, 1, 1),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode())


def _methods(source: str) -> dict[str, tuple[str | None, bool]]:
    return {
        s.name: (s.parent_name, s.is_declaration)
        for s in _parse(source).symbols
        if s.kind == "method"
    }


def _build(repo: Path):
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, caller_file: str) -> set[str]:
    return {
        t
        for s, t, d in graph.edges(data=True)
        if d.get("edge_type") == "calls" and str(s).startswith(caller_file + "::")
    }


class TestInClassDeclarationsBecomeSymbols:
    def test_a_plain_declaration_is_a_method_declaration(self) -> None:
        assert _methods("class B {\n public:\n  void plain(int x);\n};\n") == {
            "plain": ("B", True)
        }

    def test_it_does_not_need_an_access_specifier(self) -> None:
        assert _methods("class C {\n  void hidden(int x);\n};\n") == {"hidden": ("C", True)}

    def test_virtual_and_pure_virtual_both_land(self) -> None:
        assert _methods(
            "class D {\n public:\n  virtual void virt();\n  virtual void pure() = 0;\n};\n"
        ) == {"virt": ("D", True), "pure": ("D", True)}

    def test_a_struct_member_lands_too(self) -> None:
        assert _methods("struct F {\n  void inStruct();\n};\n") == {"inStruct": ("F", True)}

    def test_a_static_member_lands(self) -> None:
        assert _methods("class H {\n public:\n  static int make(int x);\n};\n") == {
            "make": ("H", True)
        }

    def test_a_pointer_return_lands(self) -> None:
        """``virtual Iterator* NewIterator() = 0;`` nests the declarator."""
        assert _methods("class I {\n public:\n  virtual Iterator* newIter(int n) = 0;\n};\n") == {
            "newIter": ("I", True)
        }

    def test_a_reference_return_lands(self) -> None:
        """``reference_declarator`` does not name its declarator field."""
        assert _methods("class G {\n public:\n  const Slice& value() const;\n};\n") == {
            "value": ("G", True)
        }

    def test_an_inline_definition_is_not_a_declaration(self) -> None:
        """The known positive: a body was always extracted, and still is."""
        assert _methods("class A {\n public:\n  void withBody() { }\n};\n") == {
            "withBody": ("A", False)
        }


class TestShapesThatMustNotBecomeMethods:
    """Controls the patterns can fail. Each nests a ``function_declarator``."""

    def test_a_function_pointer_data_member_is_not_a_method(self) -> None:
        assert _methods("class P {\n  void (*callback_)(int);\n  void real();\n};\n") == {
            "real": ("P", True)
        }

    def test_an_operator_declaration_is_not_captured(self) -> None:
        assert _methods(
            "class Q {\n  bool operator==(const Q& o) const;\n  void kept();\n};\n"
        ) == {"kept": ("Q", True)}

    def test_the_recovery_refusal_does_not_reach_a_class_that_parsed(self) -> None:
        """A parentless in-class declaration is dropped as grammar recovery.

        A ``field_declaration`` cannot occur outside a class body, so a missing
        class ancestor means the class did not parse; those symbols answered
        out-of-line definition headers that the same broken region captures as
        calls. The refusal is corpus-measured at 11 of 6,883 in-class
        declarations over the six cpp repos, all in two files. What must be
        tested here is that it does not reach an ordinary class -- a derived
        one, and one nested in a namespace, are the shapes most likely to lose
        their parent by accident.
        """
        assert _methods("namespace ns {\nclass Derived : public Base {\n public:\n  void m(int x);\n};\n}\n") == {
            "m": ("Derived", True)
        }

    def test_an_inner_types_methods_survive(self) -> None:
        """``@symbol.def`` sits on the declarator for exactly this reason.

        A ``field_declaration`` can carry a whole ``struct Inner { ... } m_;``,
        so anchoring there would make it a callable ancestor and silently drop
        the inner type's methods -- a removal caused by an addition.
        """
        assert _methods(
            "class Outer {\n"
            "  void plain();\n"
            "  struct Inner {\n"
            "    void innerBody() { }\n"
            "    void innerDecl();\n"
            "  } member_;\n"
            "};\n"
        ) == {
            "plain": ("Outer", True),
            "innerBody": ("Inner", False),
            "innerDecl": ("Inner", True),
        }


class TestExportMacroClassBodies:
    """``class LEVELDB_EXPORT Iterator { ... }`` is grammar-recovery shaped.

    The body becomes a ``compound_statement`` and its members statement-context
    ``declaration`` nodes, so none of the ``field_declaration`` patterns reach
    them, and ``= 0`` wraps the declarator in an ``init_declarator`` the plain
    forward-declaration pattern cannot match either.
    """

    def test_a_pure_virtual_member_lands(self) -> None:
        assert _methods(
            "class LEVELDB_EXPORT Iterator {\n"
            " public:\n"
            "  virtual void Seek(const Slice& t) = 0;\n"
            "  void RegisterCleanup(int a);\n"
            "};\n"
        ) == {"Seek": ("Iterator", True), "RegisterCleanup": ("Iterator", True)}

    def test_a_recovered_inline_definition_is_not_read_as_a_declaration(self) -> None:
        """The same recovery gives an inline body an ``initializer_list`` value.

        Without the ``number_literal`` guard that reads as a declaration, and a
        constructor with a member-init list does too.
        """
        source = (
            "class LEVELDB_EXPORT R {\n"
            " public:\n"
            "  size_t Index(size_t i) const { return i + 1; }\n"
            "  virtual void Real() = 0;\n"
            "};\n"
        )
        declared = {s.name for s in _parse(source).symbols if s.is_declaration}
        assert "Index" not in declared, declared
        # An inline body inside the recovered body degrades the parse further
        # and lifts what follows out of the class, so ``Real`` arrives
        # parentless. Extracting it is still right; calling it R's is not.
        assert "Real" in declared

    def test_a_deleted_function_is_not_a_method_declaration(self) -> None:
        got = _methods(
            "class LEVELDB_EXPORT S {\n"
            " public:\n"
            "  void gone(int n) = delete;\n"
            "  virtual void kept() = 0;\n"
            "};\n"
        )
        assert "gone" not in got
        assert got["kept"] == ("S", True)


class TestDeclarationPairingKeepsClassesApart:
    def test_a_declaration_does_not_pair_across_two_classes_of_one_name(
        self, tmp_path: Path
    ) -> None:
        """The pairing key is ``(class_name, method_name)``.

        A repo-wide unique definition proves the METHOD name is unique and says
        nothing about the class, so the bare fallback paired an abstract base's
        declarations onto an unrelated same-named class's definitions.
        """
        (tmp_path / "lib").mkdir(parents=True)
        (tmp_path / "lib" / "base.h").write_text(
            "#pragma once\nclass Iterator {\n public:\n  virtual void Seek(int t);\n};\n"
        )
        (tmp_path / "lib" / "nested.h").write_text(
            "#pragma once\nclass Iterator {\n public:\n  void Seek(int t) { }\n};\n"
        )
        (tmp_path / "lib" / "caller.cc").write_text(
            '#include "lib/base.h"\nint Run() {\n  Iterator* it;\n  it->Seek(1);\n  return 0;\n}\n'
        )
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert not any("nested.h::Iterator::Seek" in t for t in targets), targets

    def test_a_same_stem_pair_still_links(self, tmp_path: Path) -> None:
        """The known positive: ``thing.h`` / ``thing.cc`` is one unit.

        Without it the guard would be indistinguishable from switching the
        pairing off.
        """
        (tmp_path / "lib").mkdir(parents=True)
        (tmp_path / "lib" / "thing.h").write_text(
            "#pragma once\nclass Thing {\n public:\n  int work(int n);\n};\n"
        )
        (tmp_path / "lib" / "thing.cc").write_text(
            '#include "lib/thing.h"\nint Thing::work(int n) { return n; }\n'
        )
        (tmp_path / "lib" / "caller.cc").write_text(
            '#include "lib/thing.h"\nint Run() {\n  Thing t;\n  return t.work(1);\n}\n'
        )
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert any(t.endswith("lib/thing.cc::Thing::work") for t in targets), targets


class TestMethodDeclarationsStayOutOfTheBareNameIndex:
    def test_a_bare_call_does_not_reach_an_included_headers_method(
        self, tmp_path: Path
    ) -> None:
        """No bare name can legally reach a method.

        The per-file bare-name index answers unqualified lookups from importing
        files, so a header full of in-class declarations would let ``work(1)``
        bind to ``Thing::work`` purely because the header was included.
        """
        (tmp_path / "lib").mkdir(parents=True)
        (tmp_path / "lib" / "thing.h").write_text(
            "#pragma once\nclass Thing {\n public:\n  int work(int n);\n};\n"
        )
        (tmp_path / "lib" / "caller.cc").write_text(
            '#include "lib/thing.h"\nint Run() {\n  return work(1);\n}\n'
        )
        targets = _call_targets(_build(tmp_path), "lib/caller.cc")
        assert not any("Thing::work" in t for t in targets), targets
