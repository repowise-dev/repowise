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


class TestScalaImportExtraction:
    def test_full_dotted_path(self, parser: ASTParser) -> None:
        # The grammar query captures only the first identifier; extraction
        # must reconstruct the full dotted path.
        src = b"package p\nimport com.example.util.Helper\n"
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["com.example.util.Helper"]
        assert result.imports[0].imported_names == ["Helper"]

    def test_underscore_wildcard_normalises_to_star(self, parser: ASTParser) -> None:
        src = b"package p\nimport com.example.util._\n"
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["com.example.util.*"]
        assert result.imports[0].imported_names == ["*"]

    def test_scala3_star_wildcard(self, parser: ASTParser) -> None:
        src = b"package p\nimport com.example.util.*\n"
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["com.example.util.*"]

    def test_brace_imports_expand(self, parser: ASTParser) -> None:
        src = b"package p\nimport com.example.model.{User, Order}\n"
        result = parser.parse_file(_file(), src)
        assert sorted(i.module_path for i in result.imports) == [
            "com.example.model.Order",
            "com.example.model.User",
        ]

    def test_rename_keeps_source_path_and_local_name(self, parser: ASTParser) -> None:
        src = b"package p\nimport com.example.model.{Account => Acct}\n"
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["com.example.model.Account"]
        assert result.imports[0].imported_names == ["Acct"]

    def test_hidden_import_skipped_wildcard_kept(self, parser: ASTParser) -> None:
        # {Account => _, _}: Account is excluded, the rest wildcards in.
        src = b"package p\nimport com.example.model.{Account => _, _}\n"
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["com.example.model.*"]

    def test_comma_separated_clauses(self, parser: ASTParser) -> None:
        src = b"package p\nimport a.B, c.D\n"
        result = parser.parse_file(_file(), src)
        assert sorted(i.module_path for i in result.imports) == ["a.B", "c.D"]
