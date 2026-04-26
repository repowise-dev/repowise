"""Unit tests for Kotlin symbol extraction.

Phase 1 baseline coverage — covers class, object, and interface
declarations. In ``tree-sitter-kotlin`` the ``interface Foo {}`` form
parses as a ``class_declaration`` with ``interface`` as the leading
keyword child (NOT a separate ``interface_declaration`` node), so the
existing ``class_declaration`` capture in ``kotlin.scm`` already picks
interfaces up. These tests pin that behavior so any future grammar
upgrade or query change is caught immediately.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.kt") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="kotlin",
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


class TestKotlinSymbols:
    def test_interface_captured_as_class_declaration(self, parser: ASTParser) -> None:
        # tree-sitter-kotlin parses `interface Foo {}` as class_declaration
        # with `interface` as a keyword child; the existing
        # class_declaration capture covers it (kind="class").
        src = b"""\
package com.example

interface Greeter {
    fun greet(): String
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Greeter" in names

    def test_class_declaration_still_captured(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

class Foo {
    fun bar(): Int = 1
}
"""
        result = parser.parse_file(_file(), src)
        names = {(s.name, s.kind) for s in result.symbols}
        assert ("Foo", "class") in names

    def test_object_declaration_still_captured(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

object Singleton {
    fun work() {}
}
"""
        result = parser.parse_file(_file(), src)
        names = {(s.name, s.kind) for s in result.symbols}
        assert ("Singleton", "class") in names

    def test_interface_alongside_class(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

interface Repo
class UserRepo : Repo
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Repo" in names
        assert "UserRepo" in names
