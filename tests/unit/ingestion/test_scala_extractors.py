"""Unit tests for Scala heritage, traits, val/var, and Scala 3 captures."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.scala") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="scala",
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


class TestScalaHeritage:
    def test_extends_with_trait(self, parser: ASTParser) -> None:
        src = b"class Foo extends Base with Comparable[Foo]\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Base" in parents


class TestScalaCaptures:
    def test_val_var_definitions(self, parser: ASTParser) -> None:
        src = b"val x = 1\nvar y: Int = 2\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "x" in names
        assert "y" in names

    def test_scala3_enum(self, parser: ASTParser) -> None:
        src = b"enum Color { case Red, Green, Blue }\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Color" in names

    def test_scala3_given(self, parser: ASTParser) -> None:
        src = b"given foo: Ord[Int] = ???\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "foo" in names


class TestScalaImports:
    def test_import_path(self, parser: ASTParser) -> None:
        src = b"import scala.collection.mutable\nclass Foo\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert any("scala" in m for m in modules)
