"""Unit tests for C++ heritage extraction.

Exercises the parser path end-to-end so we cover the same code paths as
production. Other C++ extractors (bindings, docstrings) follow the
``public_by_default`` shape and don't need parallel coverage here.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.cpp") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="cpp",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


class TestCppHeritage:
    def test_com_interface_classified_as_implements(self, parser: ASTParser) -> None:
        """``class X : public IFoo, public CBase`` → IFoo implements, CBase extends."""
        src = b"""\
class CShellExt : public IShellExtInit, public IContextMenu, public CBase {
};
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("IShellExtInit", "implements") in rels
        assert ("IContextMenu", "implements") in rels
        assert ("CBase", "extends") in rels

    def test_qualified_base_stripped_to_bare_name(self, parser: ASTParser) -> None:
        """``public ns::IFoo`` is classified by the bare name."""
        src = b"""\
class CFoo : public ns::IFooService {
};
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("IFooService", "implements") in rels

    def test_namespace_scope_function_is_public(self, parser: ASTParser) -> None:
        src = b"""\
namespace ns {
  void Helper(int x) { }
}
"""
        result = parser.parse_file(_file(), src)
        sym = next(s for s in result.symbols if s.name == "Helper")
        assert sym.visibility == "public"
        assert sym.is_exported_symbol is False

    def test_file_scope_static_function_is_private(self, parser: ASTParser) -> None:
        src = b"""\
static int Helper(int x) { return x; }
"""
        result = parser.parse_file(_file(), src)
        sym = next(s for s in result.symbols if s.name == "Helper")
        assert sym.visibility == "private"

    def test_class_method_private_by_default(self, parser: ASTParser) -> None:
        src = b"""\
class Foo {
  void Hidden(int x) { }
public:
  void Visible(int x) { }
};
"""
        result = parser.parse_file(_file(), src)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Hidden"].visibility == "private"
        assert by_name["Visible"].visibility == "public"

    def test_struct_method_public_by_default(self, parser: ASTParser) -> None:
        src = b"""\
struct Foo {
  void Visible(int x) { }
private:
  void Hidden(int x) { }
};
"""
        result = parser.parse_file(_file(), src)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["Visible"].visibility == "public"
        assert by_name["Hidden"].visibility == "private"

    def test_qualified_definition_binds_to_class(self, parser: ASTParser) -> None:
        """``void Foo::method() { … }`` extracts as a method with parent=Foo."""
        src = b"""\
void Foo::DoOne(int x) { }
void NS::Bar::DoTwo(int y) { }
void Free() { }
"""
        result = parser.parse_file(_file(), src)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["DoOne"].kind == "method"
        assert by_name["DoOne"].parent_name == "Foo"
        assert by_name["DoTwo"].kind == "method"
        # Two-level NS::Bar::DoTwo → parent is the innermost qualifier, Bar.
        assert by_name["DoTwo"].parent_name == "Bar"
        # Free function unaffected.
        assert by_name["Free"].kind == "function"
        assert by_name["Free"].parent_name is None

    def test_dllexport_marks_exported_symbol(self, parser: ASTParser) -> None:
        src = b"""\
extern "C" __declspec(dllexport) HRESULT DllRegisterServer(void) { return 0; }
"""
        result = parser.parse_file(_file(), src)
        sym = next(s for s in result.symbols if s.name == "DllRegisterServer")
        assert sym.visibility == "public"
        assert sym.is_exported_symbol is True

    def test_two_letter_i_prefix_not_misclassified(self, parser: ASTParser) -> None:
        """``IO`` / ``ID`` etc. aren't COM interfaces — must stay extends."""
        src = b"""\
class StreamWrapper : public IO {
};
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("IO", "extends") in rels
        assert ("IO", "implements") not in rels

    def test_variadic_base_pack_emits_edge(self, parser: ASTParser) -> None:
        """``class C : public Bases...`` — trailing ``...`` stripped, edge emitted."""
        src = b"""\
template<typename... Bases>
class Combined : public Bases... {};
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Combined", "Bases") in rels


class TestCppPhase2Captures:
    """Symbol-graph hardening (Phase 2): new tree-sitter captures."""

    def test_destructor_declaration_extracted(self, parser: ASTParser) -> None:
        """``~Foo();`` in a class body lands as a symbol named ``~Foo``."""
        src = b"""\
class Foo {
public:
  ~Foo();
};
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "~Foo" in names

    def test_destructor_definition_qualified(self, parser: ASTParser) -> None:
        """``Foo::~Foo() {}`` out-of-class definition lands too."""
        src = b"""\
class Foo {};
Foo::~Foo() {}
"""
        result = parser.parse_file(_file(), src)
        dtors = [s for s in result.symbols if s.name == "~Foo"]
        assert dtors, "expected at least one ~Foo definition symbol"

    def test_alias_declaration_extracted(self, parser: ASTParser) -> None:
        """``using StringMap = std::map<std::string, int>;`` → type_alias."""
        src = b"""\
#include <map>
#include <string>
using StringMap = std::map<std::string, int>;
"""
        result = parser.parse_file(_file(), src)
        by_name = {s.name: s for s in result.symbols}
        assert "StringMap" in by_name
        assert by_name["StringMap"].kind == "type_alias"

    def test_template_argument_type_captured_as_ref(self, parser: ASTParser) -> None:
        """``std::vector<Widget>`` in a param type captures ``Widget``."""
        src = b"""\
#include <vector>
class Widget;
void Consume(const std::vector<Widget>& xs);
"""
        result = parser.parse_file(_file(), src)
        ref_names = {r.type_name for r in result.type_refs}
        assert "Widget" in ref_names
