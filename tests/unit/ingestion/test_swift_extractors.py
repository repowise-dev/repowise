"""Unit tests for Swift heritage extraction.

Covers Phase 1 fix B2 — adding ``extension_declaration`` to Swift's
``heritage_node_types``. The ``_extract_swift_heritage`` extractor
already handled the node, but the upstream filter discarded it.
Class and protocol cases are exercised as regression coverage.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.swift") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="swift",
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


class TestSwiftHeritage:
    def test_extension_protocol_conformance(self, parser: ASTParser) -> None:
        # B2: extension Foo: SomeProtocol {} previously produced no
        # heritage edge because extension_declaration was filtered out
        # before _extract_swift_heritage ran.
        src = b"""\
class Foo {}
protocol Greeter {}
extension Foo: Greeter {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Foo", "Greeter") in rels

    def test_class_inheritance_still_works(self, parser: ASTParser) -> None:
        src = b"""\
class Base {}
class Derived: Base {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Derived", "Base") in rels

    def test_protocol_inheritance_still_works(self, parser: ASTParser) -> None:
        src = b"""\
protocol Animal {}
protocol Pet: Animal {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Pet", "Animal") in rels

    def test_extension_multiple_conformances(self, parser: ASTParser) -> None:
        src = b"""\
class Foo {}
protocol A {}
protocol B {}
extension Foo: A, B {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Foo", "A") in rels
        assert ("Foo", "B") in rels
