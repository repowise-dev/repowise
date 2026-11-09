"""JCL and BMS: steps run programs and PROCs, programs send mapsets."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.ingestion.cobol.csd import defined_programs, read_csd
from repowise.core.ingestion.cobol.jcl import read_jcl
from repowise.core.ingestion.framework_edges.mainframe import _add_mainframe_edges
from repowise.core.ingestion.parser import ASTParser

from .parser._helpers import _make_file_info

grammar_pack = pytest.importorskip("tree_sitter_language_pack")
try:
    grammar_pack.get_language("cobol")
except Exception as exc:  # pragma: no cover - depends on first-use grammar cache/network
    pytest.skip(f"COBOL grammar unavailable: {exc}", allow_module_level=True)

JOB = b"""//POSTTRAN JOB 'POSTTRAN',CLASS=A,
// NOTIFY=&SYSUID
//* EXEC PGM=COMMENTED
//STEP05 EXEC PROC=REPROC
//STEP10 EXEC PGM=CBTRN02C
//SYSIN    DD *
  EXEC PGM=INSTREAM
/*
//STEP20 EXEC PGM=IDCAMS
"""
PROC = b"""//REPROC PROC
//PRC001 EXEC PGM=IDCAMS
"""
PROGRAM = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. CBTRN02C.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC CICS SEND MAP('COSGN0A') MAPSET('COSGN00') END-EXEC.
           GOBACK.
"""
MAPSET = b"""* LOGIN SCREEN
COSGN00 DFHMSD TYPE=&&SYSPARM,LANG=COBOL
COSGN0A DFHMDI SIZE=(24,80)
        DFHMDF POS=(1,1),LENGTH=10
        DFHMSD TYPE=FINAL
"""


def test_steps_name_their_program_or_procedure() -> None:
    member = read_jcl(JOB.decode())

    assert member.jobs == ["POSTTRAN"]
    assert [(s.name, s.program, s.procedure) for s in member.steps] == [
        ("STEP05", None, "REPROC"),
        ("STEP10", "CBTRN02C", None),
        ("STEP20", "IDCAMS", None),
    ]


def test_jobs_link_programs_and_procs_and_programs_link_mapsets() -> None:
    files = {
        "jcl/POSTTRAN.jcl": ("jcl", JOB),
        "proc/REPROC.prc": ("jcl", PROC),
        "cbl/CBTRN02C.cbl": ("cobol", PROGRAM),
        "bms/COSGN00.bms": ("bms", MAPSET),
    }
    parser = ASTParser()
    parsed = {
        p: parser.parse_file(_make_file_info(p, lang), src) for p, (lang, src) in files.items()
    }
    graph = nx.DiGraph()
    graph.add_nodes_from(files)
    ctx = SimpleNamespace(source_map={p: src for p, (_, src) in files.items()})

    _add_mainframe_edges(graph, parsed, ctx)

    assert set(graph.edges) == {
        ("jcl/POSTTRAN.jcl", "proc/REPROC.prc"),
        ("jcl/POSTTRAN.jcl", "cbl/CBTRN02C.cbl"),
        ("cbl/CBTRN02C.cbl", "bms/COSGN00.bms"),
    }
    assert graph.nodes["jcl/POSTTRAN.jcl"].get("is_entry_point") is True
    assert graph.nodes["cbl/CBTRN02C.cbl"].get("is_reachability_root") is True
    assert not graph.nodes["proc/REPROC.prc"].get("is_entry_point")
    assert {(s.name, s.kind) for s in parsed["bms/COSGN00.bms"].symbols} == {
        ("COSGN00", "module"),
        ("COSGN0A", "class"),
    }


def test_a_step_never_runs_a_job_as_its_procedure() -> None:
    files = {
        "jcl/RUNNER.jcl": ("jcl", b"//RUNNER JOB CLASS=A\n//S1 EXEC BATCH1\n//S2 EXEC NIGHTLY\n"),
        "jcl/BATCH1.jcl": ("jcl", b"//BATCH1 JOB CLASS=A\n//S1 EXEC PGM=IEFBR14\n"),
        "jcl/LIB.jcl": ("jcl", b"//NIGHTLY PROC\n//S1 EXEC PGM=IEFBR14\n"),
    }
    parser = ASTParser()
    parsed = {
        p: parser.parse_file(_make_file_info(p, lang), src) for p, (lang, src) in files.items()
    }
    graph = nx.DiGraph()
    graph.add_nodes_from(files)
    ctx = SimpleNamespace(source_map={p: src for p, (_, src) in files.items()})

    _add_mainframe_edges(graph, parsed, ctx)

    assert set(graph.edges) == {("jcl/RUNNER.jcl", "jcl/LIB.jcl")}


CSD = b""" DEFINE PROGRAM(COACTUPC) GROUP(CARDDEMO)
 DESCRIPTION(CREDIT CARD DEMO ACCOUNT UPDATE)
        RELOAD(NO) DELETE(YES)
 DEFINE TRANSACTION(CAUP) GROUP(CARDDEMO)
 DESCRIPTION(ACCOUNT UPDATE)
        PROGRAM(COMENU1C) TWASIZE(0) PROFILE(DFHCICST)
* DEFINE PROGRAM(COMMENTED) GROUP(CARDDEMO)
 DEFINE MAPSET(COSGN00) GROUP(CARDDEMO)
"""
CSD_JOB = b"""//GENACSD JOB 241901,'CSD GENERATE',CLASS=A
//CSDDEFS  EXEC PGM=DFHCSDUP,REGION=1M
//SYSIN    DD *
*Delete Group(GENA)     All
Add    Group(GENASAT)     List(GENALIST)
Define Transaction(SSC1) Group(GENASAT)
       Program(LGTESTC1) TaskDataLoc(Any)
Define Program(LGACUS01) Group(GENASAP)
/*
//NEXT EXEC PGM=IEFBR14
"""


def _program(name: bytes, procedure: bytes = b"           GOBACK.\n") -> bytes:
    return (
        b"       IDENTIFICATION DIVISION.\n       PROGRAM-ID. " + name + b".\n"
        b"       DATA DIVISION.\n       WORKING-STORAGE SECTION.\n"
        b"       01 WS-TRANID PIC X(4) VALUE 'CAUP'.\n"
        b"       PROCEDURE DIVISION.\n       MAIN-PARA.\n" + procedure
    )


def _edges(files: dict[str, tuple[str, bytes]]) -> nx.DiGraph:
    parser = ASTParser()
    parsed = {
        p: parser.parse_file(_make_file_info(p, lang), src) for p, (lang, src) in files.items()
    }
    graph = nx.DiGraph()
    graph.add_nodes_from(files)
    _add_mainframe_edges(graph, parsed, SimpleNamespace(source_map={p: s for p, (_, s) in files.items()}))
    return graph


def test_csd_definitions_read_continuations_and_skip_comments() -> None:
    defs = read_csd(enumerate(CSD.decode().splitlines(), 1))
    assert [(d.resource, d.name, d.program) for d in defs] == [
        ("PROGRAM", "COACTUPC", None),
        ("TRANSACTION", "CAUP", "COMENU1C"),
        ("MAPSET", "COSGN00", None),
    ]
    assert defined_programs(defs) == ["COACTUPC", "COMENU1C"]


def test_csd_definitions_in_jcl_in_stream_data() -> None:
    member = read_jcl(CSD_JOB.decode())
    defs = read_csd(member.in_stream)
    assert [(d.resource, d.name, d.program) for d in defs] == [
        ("TRANSACTION", "SSC1", "LGTESTC1"),
        ("PROGRAM", "LGACUS01", None),
    ]
    assert [s.program for s in member.steps] == ["DFHCSDUP", "IEFBR14"]


@pytest.mark.parametrize("operation", ["SEND", "RECEIVE"])
@pytest.mark.parametrize("directory", ["cobol", "COBOL"])
def test_cobol_mapset_wins_a_nearest_tie(operation: str, directory: str) -> None:
    program = "sources/app/SBANK10P.cbl"
    preferred = f"sources/bms/{directory}/COSGN00.bms"
    graph = _edges({
        program: ("cobol", _program(b"SBANK10P", (
            f"           EXEC CICS {operation} MAP('COSGN0A') "
            "MAPSET('COSGN00') END-EXEC.\n"
        ).encode())),
        preferred: ("bms", MAPSET),
        "sources/bms/pli/COSGN00.bms": ("bms", MAPSET),
    })
    assert set(graph.edges) == {(program, preferred)}


@pytest.mark.parametrize(("candidates", "expected"), [
    (["sources/bms/cobol/a/COSGN00.bms", "sources/bms/cobol/b/COSGN00.bms"], None),
    (["sources/bms/mycobol/COSGN00.bms", "sources/bms/pli/COSGN00.bms"], None),
    (["sources/bms/pli/COSGN00.bms"], "sources/bms/pli/COSGN00.bms"),
    (["sources/app/maps/pli/COSGN00.bms", "lib/cobol/COSGN00.bms"],
     "sources/app/maps/pli/COSGN00.bms"),
    (["sources/bms/a/COSGN00.bms", "sources/bms/b/COSGN00.bms",
      "lib/cobol/COSGN00.bms"], None),
    (["sources/bms/cobol/COSGN00.bms", "sources/bms/pli/COSGN00.bms",
      "lib/cobol/COSGN00.bms"], "sources/bms/cobol/COSGN00.bms"),
])
def test_mapset_preference_preserves_nearness_and_ambiguity(
    candidates: list[str], expected: str | None,
) -> None:
    program = "sources/app/SBANK10P.cbl"
    graph = _edges({
        program: ("cobol", PROGRAM),
        **{path: ("bms", MAPSET) for path in candidates},
    })
    assert set(graph.edges) == ({(program, expected)} if expected else set())


def test_defined_programs_are_roots_linked_from_their_definition() -> None:
    graph = _edges(
        {
            "csd/CARDDEMO.CSD": ("csd", CSD),
            "cntl/cdef121.jcl": ("jcl", CSD_JOB),
            "cbl/COACTUPC.cbl": ("cobol", _program(b"COACTUPC")),
            "cbl/COMENU1C.cbl": ("cobol", _program(b"COMENU1C")),
            "cbl/LGTESTC1.cbl": ("cobol", _program(b"LGTESTC1")),
            "cbl/LGACUS01.cbl": ("cobol", _program(b"LGACUS01")),
            "cbl/UNDEFINED.cbl": ("cobol", _program(b"UNDEFINED")),
        }
    )
    assert set(graph.edges) == {
        ("csd/CARDDEMO.CSD", "cbl/COACTUPC.cbl"),
        ("csd/CARDDEMO.CSD", "cbl/COMENU1C.cbl"),
        ("cntl/cdef121.jcl", "cbl/LGTESTC1.cbl"),
        ("cntl/cdef121.jcl", "cbl/LGACUS01.cbl"),
    }
    roots = {p for p, d in graph.nodes(data=True) if d.get("is_reachability_root")}
    assert roots == {
        "cbl/COACTUPC.cbl",
        "cbl/COMENU1C.cbl",
        "cbl/LGTESTC1.cbl",
        "cbl/LGACUS01.cbl",
    }


def test_return_and_start_transid_link_to_the_transaction_program() -> None:
    returns = _program(
        b"COSGN00C",
        b"           EXEC CICS STARTBR FILE('X') RIDFLD(K) END-EXEC\n"
        b"           EXEC CICS START TRANSID('SSC1') END-EXEC\n"
        b"           EXEC CICS RETURN TRANSID(WS-TRANID) END-EXEC.\n",
    )
    graph = _edges(
        {
            "csd/CARDDEMO.CSD": ("csd", CSD),
            "cntl/cdef121.jcl": ("jcl", CSD_JOB),
            "cbl/COSGN00C.cbl": ("cobol", returns),
            "cbl/COMENU1C.cbl": ("cobol", _program(b"COMENU1C")),
            "cbl/LGTESTC1.cbl": ("cobol", _program(b"LGTESTC1")),
        }
    )
    assert set(graph.successors("cbl/COSGN00C.cbl")) == {"cbl/COMENU1C.cbl", "cbl/LGTESTC1.cbl"}


def test_a_transid_is_not_linked_to_its_own_program_or_an_ambiguous_one() -> None:
    other_region = CSD.replace(b"PROGRAM(COMENU1C)", b"PROGRAM(COOTHER)")
    graph = _edges(
        {
            "csd/A.csd": ("csd", CSD),
            "csd/B.csd": ("csd", other_region),
            "cbl/COMENU1C.cbl": (
                "cobol",
                _program(b"COMENU1C", b"           EXEC CICS RETURN TRANSID('CAUP') END-EXEC.\n"),
            ),
            "cbl/CALLER.cbl": (
                "cobol",
                _program(b"CALLER", b"           EXEC CICS RETURN TRANSID('CAUP') END-EXEC.\n"),
            ),
            "cbl/COOTHER.cbl": ("cobol", _program(b"COOTHER")),
        }
    )
    assert set(graph.successors("cbl/COMENU1C.cbl")) == set()
    assert set(graph.successors("cbl/CALLER.cbl")) == set()


def test_csd_abbreviations_and_unknown_commands_end_a_definition() -> None:
    text = (
        "DEFINE TRANSACTION(T2) GROUP(G)\n"
        "DEF TRANS(T3) GROUP(G)\n"
        "    PROG(P3)\n"
        "FOO BAR(X)\n"
        "    PROGRAM(P9)\n"
        "DEFINE PROGRAM(P4) GROUP(G)\n"
        "ALT TRANSACTION(T3) PROGRAM(OTHER)\n"
        "DELETE PROGRAM(P4) GROUP(G)\n"
    )
    defs = read_csd(enumerate(text.splitlines(), 1))
    assert [(d.command, d.resource, d.name, d.program) for d in defs] == [
        ("DEFINE", "TRANSACTION", "T2", None),
        ("DEFINE", "TRANSACTION", "T3", "P3"),
        ("DEFINE", "PROGRAM", "P4", None),
        ("ALTER", "TRANSACTION", "T3", "OTHER"),
    ]


def test_an_altered_transaction_and_a_runtime_transid_link_nowhere() -> None:
    altered = CSD + b" ALTER TRANSACTION(CAUP) GROUP(CARDDEMO) PROGRAM(COOTHER)\n"
    dynamic = _program(
        b"CALLER",
        b"           MOVE WS-INPUT TO WS-TRANID\n"
        b"           EXEC CICS RETURN TRANSID(WS-TRANID) END-EXEC.\n",
    )
    literal = _program(b"CALLER2", b"           EXEC CICS RETURN TRANSID('CAUP') END-EXEC.\n")
    graph = _edges(
        {
            "csd/CARDDEMO.CSD": ("csd", CSD),
            "cbl/CALLER.cbl": ("cobol", dynamic),
            "cbl/COMENU1C.cbl": ("cobol", _program(b"COMENU1C")),
        }
    )
    assert set(graph.successors("cbl/CALLER.cbl")) == set()
    graph = _edges(
        {
            "csd/CARDDEMO.CSD": ("csd", altered),
            "cbl/CALLER2.cbl": ("cobol", literal),
            "cbl/COMENU1C.cbl": ("cobol", _program(b"COMENU1C")),
            "cbl/COOTHER.cbl": ("cobol", _program(b"COOTHER")),
        }
    )
    assert set(graph.successors("cbl/CALLER2.cbl")) == set()
    assert graph.nodes["cbl/COOTHER.cbl"].get("is_reachability_root") is True


def test_in_stream_data_ends_at_its_dlm_delimiter() -> None:
    member = read_jcl(
        "//J JOB CLASS=A\n//S1 EXEC PGM=DFHCSDUP\n//SYSIN DD DATA,DLM=@@\n"
        "//NOTJCL looks like a statement\nDEFINE PROGRAM(P1) GROUP(G)\n@@\n"
        "//S2 EXEC PGM=IEFBR14\n"
    )
    assert [line for _, line in member.in_stream] == [
        "//NOTJCL looks like a statement",
        "DEFINE PROGRAM(P1) GROUP(G)",
    ]
    assert [s.program for s in member.steps] == ["DFHCSDUP", "IEFBR14"]
