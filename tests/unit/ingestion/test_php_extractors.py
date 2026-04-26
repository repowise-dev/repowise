"""Unit tests for PHP heritage, traits, bindings, and const/property captures."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.php") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="php",
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


class TestPhpHeritage:
    def test_extends_implements(self, parser: ASTParser) -> None:
        src = b"<?php\nclass Foo extends Base implements IBar {}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Base" in parents
        assert "IBar" in parents

    def test_use_trait(self, parser: ASTParser) -> None:
        src = b"<?php\nclass Foo {\n  use TimestampedTrait;\n}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "TimestampedTrait" in parents


class TestPhpCaptures:
    def test_const_declaration(self, parser: ASTParser) -> None:
        src = b"<?php\nclass Foo { const ITEMS = 10; }\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "ITEMS" in names

    def test_property_declaration(self, parser: ASTParser) -> None:
        src = b"<?php\nclass Foo { public $name = 'a'; }\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "name" in names


class TestPhpBindings:
    def test_use_declaration(self, parser: ASTParser) -> None:
        src = b"<?php\nuse App\\Service\\UserService;\nclass Foo {}\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert any("UserService" in m for m in modules)
