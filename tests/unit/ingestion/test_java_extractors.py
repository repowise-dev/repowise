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
