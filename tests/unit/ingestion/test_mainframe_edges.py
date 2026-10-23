"""JCL and BMS: steps run programs and PROCs, programs send mapsets."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import pytest

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
