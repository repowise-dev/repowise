"""COBOL ``COPY`` members resolve to copybooks by stem, nearest first, never by guess."""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.cobol.facts import cobol_imports
from repowise.core.ingestion.graph import build_stem_map
from repowise.core.ingestion.resolvers.cobol import resolve_cobol_import
from repowise.core.ingestion.resolvers.context import ResolverContext


def _ctx(*paths: str) -> ResolverContext:
    path_set = set(paths)
    return ResolverContext(path_set=path_set, stem_map=build_stem_map(path_set), graph=nx.DiGraph())


def test_a_member_resolves_case_insensitively_to_its_copybook() -> None:
    ctx = _ctx("app/cbl/COSGN00C.cbl", "app/cpy/cocom01y.cpy")

    assert resolve_cobol_import("COCOM01Y", "app/cbl/COSGN00C.cbl", ctx) == "app/cpy/cocom01y.cpy"


def test_a_copybook_beats_a_bms_map_or_program_with_the_same_stem() -> None:
    ctx = _ctx("app/bms/COSGN00.bms", "app/cpy-bms/COSGN00.CPY", "app/cbl/COSGN00.cbl", "a/X.cbl")

    assert resolve_cobol_import("COSGN00", "a/X.cbl", ctx) == "app/cpy-bms/COSGN00.CPY"


def test_the_copybook_nearest_the_copying_program_wins() -> None:
    ctx = _ctx("app/cpy/CVACT01Y.cpy", "app/sub/cpy/CVACT01Y.cpy", "app/sub/cbl/P.cbl")

    assert resolve_cobol_import("CVACT01Y", "app/sub/cbl/P.cbl", ctx) == "app/sub/cpy/CVACT01Y.cpy"


def test_a_named_library_directory_is_preferred() -> None:
    ctx = _ctx("lib1/MEMBER.cpy", "LIB2/MEMBER.cpy", "src/P.cbl")

    assert resolve_cobol_import("LIB2/MEMBER", "src/P.cbl", ctx) == "LIB2/MEMBER.cpy"


def test_a_system_copybook_is_external_not_guessed() -> None:
    ctx = _ctx("app/cbl/P.cbl", "app/docs/DFHAID.md")

    assert resolve_cobol_import("DFHAID", "app/cbl/P.cbl", ctx) == "external:copybook:DFHAID"
    assert "external:copybook:DFHAID" in ctx.graph


def test_copy_and_sql_include_become_one_import_per_member() -> None:
    source = (
        b"       IDENTIFICATION DIVISION.\n"
        b"       PROGRAM-ID. P.\n"
        b"       DATA DIVISION.\n"
        b"       WORKING-STORAGE SECTION.\n"
        b"       COPY COCOM01Y.\n"
        b"       COPY COCOM01Y.\n"
        b"       COPY MEMBER1 OF MYLIB.\n"
        b"       COPY MEMBER1 OF OTHERLIB.\n"
        b"           EXEC SQL INCLUDE DCLTRTYP END-EXEC.\n"
    )

    found = [(i.module_path, i.raw_statement, i.imported_names) for i in cobol_imports(source)]

    assert found == [
        ("COCOM01Y", "COPY COCOM01Y", ["*"]),
        ("MYLIB/MEMBER1", "COPY MEMBER1 OF MYLIB", ["*"]),
        ("OTHERLIB/MEMBER1", "COPY MEMBER1 OF OTHERLIB", ["*"]),
        ("DCLTRTYP", "EXEC SQL INCLUDE DCLTRTYP", ["*"]),
    ]


def test_a_sql_include_resolves_to_a_dclgen_member() -> None:
    ctx = _ctx("app/cbl/COTRTUPC.cbl", "app/dcl/DCLTRTYP.dcl")

    assert resolve_cobol_import("DCLTRTYP", "app/cbl/COTRTUPC.cbl", ctx) == "app/dcl/DCLTRTYP.dcl"
