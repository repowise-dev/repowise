"""CICS LINK/XCTL targets: literals and constants are static, anything else is dynamic."""

from __future__ import annotations

from repowise.core.ingestion.cobol import code_view
from repowise.core.ingestion.cobol.cics import CicsTarget, program_transfers
from repowise.core.ingestion.cobol.facts import cobol_imports


def _program(data: str, procedure: str) -> bytes:
    return (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. COSGN00C.\n"
        "       DATA DIVISION.\n"
        "       WORKING-STORAGE SECTION.\n"
        f"{data}\n"
        "       PROCEDURE DIVISION.\n"
        "       MAIN-PARA.\n"
        f"{procedure}\n"
    ).encode()


def _transfers(source: bytes) -> list[CicsTarget]:
    return program_transfers(code_view(source))


def test_a_literal_program_is_its_target() -> None:
    source = _program("", "           EXEC CICS XCTL PROGRAM('COMEN01C') END-EXEC.")

    assert _transfers(source) == [CicsTarget("XCTL", 8, ("COMEN01C",))]


def test_literal_moves_into_the_variable_are_its_targets() -> None:
    source = _program(
        "       01 CDEMO-TO-PROGRAM PIC X(8).",
        "           IF WS-ADMIN MOVE 'COADM01C' TO CDEMO-TO-PROGRAM\n"
        "           ELSE MOVE 'COMEN01C' TO CDEMO-TO-PROGRAM END-IF\n"
        "           EXEC CICS XCTL\n"
        "                PROGRAM(CDEMO-TO-PROGRAM)\n"
        "           END-EXEC.",
    )

    assert _transfers(source) == [CicsTarget("XCTL", 10, ("COADM01C", "COMEN01C"))]


def test_a_constant_moved_into_the_variable_is_followed() -> None:
    source = _program(
        "       01 LIT-MENUPGM PIC X(8) VALUE 'COMEN01C'.\n       01 CDEMO-TO-PROGRAM PIC X(8).",
        "           MOVE LIT-MENUPGM TO CDEMO-TO-PROGRAM\n"
        "           EXEC CICS LINK PROGRAM(CDEMO-TO-PROGRAM) END-EXEC.",
    )

    assert _transfers(source) == [CicsTarget("LINK", 10, ("COMEN01C",))]


def test_a_variable_assigned_from_another_variable_is_dynamic() -> None:
    source = _program(
        "       01 CDEMO-TO-PROGRAM PIC X(8).",
        "           MOVE 'COSGN00C' TO CDEMO-TO-PROGRAM\n"
        "           MOVE CDEMO-FROM-PROGRAM TO CDEMO-TO-PROGRAM\n"
        "           EXEC CICS XCTL PROGRAM(CDEMO-TO-PROGRAM) END-EXEC.",
    )

    assert _transfers(source) == [
        CicsTarget("XCTL", 10, ("COSGN00C",), dynamic="CDEMO-TO-PROGRAM")
    ]
    dynamic = [i.module_path for i in cobol_imports(source) if i.module_path.startswith("dyn")]
    assert dynamic == ["dynamic:CDEMO-TO-PROGRAM"]


def test_a_reassigned_value_is_not_a_constant() -> None:
    source = _program(
        "       01 WS-PGM PIC X(8) VALUE 'COMEN01C'.",
        "           MOVE INPUT-PGM TO WS-PGM\n           EXEC CICS XCTL PROGRAM(WS-PGM) END-EXEC.",
    )

    assert _transfers(source) == [CicsTarget("XCTL", 9, (), dynamic="WS-PGM")]


def test_a_literal_later_overwritten_by_another_verb_is_dynamic() -> None:
    source = _program(
        "       01 WS-PGM PIC X(8).",
        "           MOVE 'COMEN01C' TO WS-PGM\n"
        "           ACCEPT WS-PGM\n"
        "           EXEC CICS XCTL PROGRAM(WS-PGM) END-EXEC.",
    )

    assert _transfers(source) == [CicsTarget("XCTL", 10, ("COMEN01C",), dynamic="WS-PGM")]


def test_other_cics_commands_and_comments_are_not_transfers() -> None:
    source = _program(
        "",
        "      *    EXEC CICS XCTL PROGRAM('NOTME') END-EXEC.\n"
        "           EXEC CICS RETURN TRANSID('CC00') END-EXEC.\n"
        "           EXEC CICS SEND MAP('COSGN0A') MAPSET('COSGN00') END-EXEC.",
    )

    assert _transfers(source) == []


def test_each_when_arm_transfers_to_the_program_it_moves() -> None:
    source = _program(
        "       01 NEXT-PROG PIC X(8).",
        "           MOVE 'COCRDLIC' TO NEXT-PROG\n"
        "           EVALUATE TRUE\n"
        "             WHEN WS-VIEW\n"
        "               MOVE 'COCRDSLC' TO NEXT-PROG\n"
        "               EXEC CICS XCTL PROGRAM(NEXT-PROG) END-EXEC\n"
        "             WHEN WS-EDIT\n"
        "               MOVE 'COCRDUPC' TO NEXT-PROG\n"
        "               EXEC CICS XCTL PROGRAM(NEXT-PROG) END-EXEC\n"
        "           END-EVALUATE.",
    )

    assert [t.targets for t in _transfers(source)] == [("COCRDSLC",), ("COCRDUPC",)]
