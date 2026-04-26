"""Unit tests for Ruby heritage, mixins, bindings, and constant captures."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "foo.rb") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="ruby",
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


class TestRubyHeritage:
    def test_class_extends(self, parser: ASTParser) -> None:
        src = b"class Foo < Base\nend\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Base" in parents

    def test_include_mixin(self, parser: ASTParser) -> None:
        # Avoid stdlib names (Comparable/Enumerable) — those are filtered as
        # builtin parents. Use custom modules.
        src = b"class Foo\n  include Loggable\n  extend Cacheable\nend\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Loggable" in parents
        assert "Cacheable" in parents


class TestRubyConstants:
    def test_top_level_constant_captured(self, parser: ASTParser) -> None:
        src = b"MAX_RETRIES = 3\nDEFAULT = :foo\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "MAX_RETRIES" in names
        assert "DEFAULT" in names


class TestRubyImports:
    def test_require_statements(self, parser: ASTParser) -> None:
        src = b"require 'json'\nrequire_relative './helper'\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "json" in modules
        assert "./helper" in modules
