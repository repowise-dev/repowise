"""Unit tests for Go heritage (struct embedding) and binding extraction."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "foo.go") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="go",
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


class TestGoSymbols:
    def test_function_and_struct(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype User struct { Name string }\n\nfunc Hello() string { return \"\" }\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "User" in names
        assert "Hello" in names


class TestGoHeritage:
    def test_struct_embedding(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype Base struct{}\n\ntype Foo struct {\n  Base\n  Name string\n}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Base" in parents


class TestGoBindings:
    def test_imports(self, parser: ASTParser) -> None:
        src = b"package x\n\nimport (\n  \"fmt\"\n  \"net/http\"\n)\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "fmt" in modules
        assert "net/http" in modules


class TestGoMethodReceiver:
    def test_method_parent_extracted_from_receiver(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype User struct{}\n\nfunc (u *User) Greet() string { return \"\" }\n"
        result = parser.parse_file(_file(), src)
        greet = [s for s in result.symbols if s.name == "Greet"]
        assert greet
        assert greet[0].parent_name == "User"

    def test_unexported_receiver_type_still_parents_the_method(
        self, parser: ASTParser
    ) -> None:
        """Export status says nothing about whether a name is a type."""
        src = b"package x\n\ntype startEnd struct{}\n\nfunc (s *startEnd) add() {}\n"
        result = parser.parse_file(_file(), src)
        add = [s for s in result.symbols if s.name == "add"]
        assert add
        assert add[0].parent_name == "startEnd"

    def test_unnamed_receiver(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype cache struct{}\n\nfunc (*cache) reset() {}\n"
        result = parser.parse_file(_file(), src)
        reset = [s for s in result.symbols if s.name == "reset"]
        assert reset
        assert reset[0].parent_name == "cache"
