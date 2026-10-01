"""Unit tests for Java heritage, binding, and module-Javadoc extraction."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.java") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="java",
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


class TestJavaHeritage:
    def test_extends_and_implements(self, parser: ASTParser) -> None:
        src = b"package x;\npublic class Foo extends Base implements IBar, IBaz {}\n"
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert ("Base", "extends") in rels
        assert ("IBar", "implements") in rels
        assert ("IBaz", "implements") in rels

    def test_interface_extends(self, parser: ASTParser) -> None:
        src = b"package x;\npublic interface IFoo extends IBase {}\n"
        result = parser.parse_file(_file(), src)
        rels = {(r.parent_name, r.kind) for r in result.heritage}
        assert any(p == "IBase" for p, _ in rels)


class TestJavaRecords:
    def test_record_declaration_captured(self, parser: ASTParser) -> None:
        src = b"package x;\npublic record Point(double x, double y) {}\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Point" in names

    def test_implicit_canonical_ctor_takes_record_access(self, parser: ASTParser) -> None:
        # JLS 8.10.4: an implicit canonical constructor takes the record's
        # own access level — package-private record => package-private ctor.
        src = b"package x;\nrecord Foo(int a, int b) {}\n"
        result = parser.parse_file(_file(), src)
        ctor = next(s for s in result.symbols if s.name == "Foo" and s.kind == "function")
        assert ctor.visibility == "internal"
        assert ctor.signature == "Foo(int a, int b)"

    def test_compact_canonical_ctor_takes_record_access(self, parser: ASTParser) -> None:
        # An explicit compact constructor with no access keyword is
        # package-private too — no separate code path, one fix covers both.
        src = (
            b"package x;\nrecord Bar(int a, int b) {\n"
            b"  Bar {\n    if (a < 0) throw new IllegalArgumentException();\n  }\n}\n"
        )
        result = parser.parse_file(_file(), src)
        ctor = next(s for s in result.symbols if s.name == "Bar" and s.kind == "function")
        assert ctor.visibility == "internal"
        assert ctor.signature == "Bar(int a, int b)"

    def test_public_record_keeps_public_ctor(self, parser: ASTParser) -> None:
        src = b"package x;\npublic record Baz(int a) {}\n"
        result = parser.parse_file(_file(), src)
        ctor = next(s for s in result.symbols if s.name == "Baz" and s.kind == "function")
        assert ctor.visibility == "public"
        assert ctor.signature == "public Baz(int a)"

    def test_record_accessors_stay_public(self, parser: ASTParser) -> None:
        # Accessors (and equals/hashCode/toString) are mandated public by
        # the record contract regardless of the record's own access.
        src = b"package x;\nrecord Foo(int a, int b) {}\n"
        result = parser.parse_file(_file(), src)
        accessors = {s.name: s for s in result.symbols if s.kind == "method"}
        assert accessors["a"].visibility == "public"
        assert accessors["a"].signature == "public int a()"
        assert accessors["toString"].visibility == "public"


class TestJavaBindings:
    def test_import_produces_binding(self, parser: ASTParser) -> None:
        src = b"package x;\nimport com.example.Foo;\nimport static com.example.Bar.baz;\npublic class App {}\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "com.example.Foo" in modules


class TestJavaModuleDocstring:
    def test_module_javadoc_extracted(self, parser: ASTParser) -> None:
        src = b"/**\n * Module-level summary line.\n */\npackage x;\npublic class Foo {}\n"
        result = parser.parse_file(_file(), src)
        assert result.docstring is not None
        assert "Module-level summary" in result.docstring


class TestJavaWildcardImports:
    def test_wildcard_import_keeps_star(self, parser: ASTParser) -> None:
        # The grammar query captures only the scoped identifier — the `*`
        # is a sibling node. Extraction must restore it or the resolver's
        # package fan-out branch can never fire.
        src = b"package x;\nimport com.example.util.*;\npublic class App {}\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "com.example.util.*" in modules

    def test_static_wildcard_import_keeps_star(self, parser: ASTParser) -> None:
        src = b"package x;\nimport static com.example.Assertions.*;\npublic class App {}\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "com.example.Assertions.*" in modules

    def test_plain_import_unchanged(self, parser: ASTParser) -> None:
        src = b"package x;\nimport com.example.Foo;\npublic class App {}\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert modules == ["com.example.Foo"]
