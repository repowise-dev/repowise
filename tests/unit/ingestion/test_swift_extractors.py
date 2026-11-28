"""Unit tests for Swift heritage extraction.

Covers Phase 1 fix B2 — adding ``extension_declaration`` to Swift's
``heritage_node_types``. The ``_extract_swift_heritage`` extractor
already handled the node, but the upstream filter discarded it.
Class and protocol cases are exercised as regression coverage.
"""

from __future__ import annotations

import time
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

    def test_qualified_extension_records_only_the_type(self, parser: ASTParser) -> None:
        # `extension Ns.Type: P {}` parses as ONE flat user_type with two
        # type_identifier children. An unanchored capture matches both, so the
        # qualifier was recorded as conforming to P on its own. Equality is the
        # assertion that matters here: containment would pass against the bug.
        src = b"""\
enum Ns { struct Type {} }
protocol P {}
extension Ns.Type: P {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert rels == {("Type", "P")}

    def test_deeply_qualified_extension_takes_the_trailing_identifier(
        self, parser: ASTParser
    ) -> None:
        src = b"""\
enum A { enum B { struct C {} } }
protocol P {}
extension A.B.C: P {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert rels == {("C", "P")}

    def test_plain_extension_is_unaffected_by_the_anchor(self, parser: ASTParser) -> None:
        src = b"""\
protocol P {}
extension Foo: P {}
"""
        result = parser.parse_file(_file(), src)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert rels == {("Foo", "P")}


class TestSwiftExportedImport:
    def test_exported_import_is_reexport(self, parser: ASTParser) -> None:
        src = b"@_exported import FooKit\nimport Foundation\n"
        result = parser.parse_file(_file(), src)
        by_module = {i.module_path: i for i in result.imports}
        assert by_module["FooKit"].is_reexport is True
        assert by_module["Foundation"].is_reexport is False


def _swift_struct(members: int) -> bytes:
    """One struct with *members* fields, each with a ``///`` doc comment."""
    lines = ["struct Big {"]
    for i in range(members):
        lines.append(f"    /// Doc {i}.")
        lines.append(f"    var m{i}: Int = {i}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode()


def _best_parse_time(parser: ASTParser, src: bytes, runs: int = 3) -> float:
    """The fastest of *runs* parses of *src*, in seconds."""
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        parser.parse_file(_file("Big.swift"), src)
        best = min(best, time.perf_counter() - start)
    return best


class TestSwiftDocstringScale:
    def test_many_member_struct_keeps_docstrings(self, parser: ASTParser) -> None:
        members = 5000
        result = parser.parse_file(_file("Big.swift"), _swift_struct(members))

        docs = {s.name: s.docstring for s in result.symbols}
        assert docs["m0"] == "Doc 0."
        assert docs[f"m{members - 1}"] == f"Doc {members - 1}."
        assert sum(1 for name in docs if name.startswith("m")) == members
        assert all(docs[f"m{i}"] for i in range(members))

    def test_docstring_lookup_scales_linearly(self, parser: ASTParser) -> None:
        # #5277: each docstring lookup copied the parent's children, so N members
        # cost N^2. 4x the members should take about 4x as long, not 16x.
        _best_parse_time(parser, _swift_struct(10), runs=1)  # warm-up

        small = _best_parse_time(parser, _swift_struct(1000))
        large = _best_parse_time(parser, _swift_struct(4000))

        ratio = large / small
        assert ratio < 8, (
            f"4000 members took {ratio:.1f}x as long as 1000 "
            f"({large:.2f}s vs {small:.2f}s); linear is about 4x"
        )
