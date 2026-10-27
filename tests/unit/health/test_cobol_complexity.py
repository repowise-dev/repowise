"""COBOL paragraph complexity: bodies are the siblings after a header, and so is control flow."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.complexity.languages import has_health_dialect

grammar_pack = pytest.importorskip("tree_sitter_language_pack")
try:
    grammar_pack.get_language("cobol")
except Exception as exc:  # pragma: no cover - depends on first-use grammar cache/network
    pytest.skip(f"COBOL grammar unavailable: {exc}", allow_module_level=True)


def _program(*procedure: str) -> bytes:
    lines = [
        "       IDENTIFICATION DIVISION.",
        "       PROGRAM-ID. DEMO.",
        "       DATA DIVISION.",
        "       WORKING-STORAGE SECTION.",
        "       01 WS-A PIC 9.",
        "       PROCEDURE DIVISION.",
        *procedure,
    ]
    return ("\n".join(lines) + "\n").encode()


def _rows(source: bytes, path: str = "DEMO.cbl") -> dict[str, tuple[int, ...]]:
    fcx = walk_file(path, "cobol", source)
    return {
        fc.name: (fc.start_line, fc.end_line, fc.ccn, fc.max_nesting, fc.cognitive, fc.nloc)
        for fc in fcx.functions
    }


def test_cobol_is_a_language_health_walks() -> None:
    assert has_health_dialect("cobol")


def test_statements_group_under_the_header_before_them() -> None:
    rows = _rows(
        _program(
            "           PERFORM P1.",
            "       MAIN SECTION.",
            "           DISPLAY 'X'.",
            "       P1.",
            "      * a comment line",
            "           MOVE 1 TO WS-A",
            "           MOVE 2 TO WS-A.",
            "       P2.",
            "           GOBACK.",
        )
    )
    # name: (start, end, ccn, nesting, cognitive, nloc). A section's row holds
    # only its own statements; the program's holds those before any header.
    assert rows == {
        "DEMO": (6, 7, 1, 0, 0, 1),
        "MAIN": (8, 9, 1, 0, 0, 1),
        "P1": (10, 13, 1, 0, 0, 2),
        "P2": (14, 15, 1, 0, 0, 1),
    }


def test_branches_loops_and_conditions_count_like_other_languages() -> None:
    rows = _rows(
        _program(
            "       P1.",
            "           PERFORM UNTIL WS-A > 5 OR WS-A = 0",
            "              IF WS-A = 1 AND WS-A < 3",
            "                 ADD 1 TO WS-A",
            "              ELSE",
            "                 SUBTRACT 1 FROM WS-A",
            "              END-IF",
            "           END-PERFORM",
            "           PERFORM P2 VARYING WS-A FROM 1 BY 1 UNTIL WS-A > 3",
            "           PERFORM P2 THRU P2",
            "           IF WS-A = 1 DISPLAY 'Y'.",
            "       P2.",
            "           GO TO P1.",
        )
    )
    # Loop + OR, IF + AND, the out-of-line VARYING loop, the last IF; THRU is a call.
    assert rows["P1"][2:5] == (7, 2, 7)
    # GO TO is a break in flow: cognitive only.
    assert rows["P2"][2:5] == (1, 0, 1)


def test_evaluate_arms_nest_like_switch_cases_and_a_period_closes_every_scope() -> None:
    rows = _rows(
        _program(
            "       P1.",
            "           EVALUATE WS-A",
            "              WHEN 1",
            "                 IF WS-A = 1",
            "                    DISPLAY 'A'",
            "                 END-IF",
            "              WHEN 2",
            "                 DISPLAY 'B'",
            "              WHEN OTHER",
            "                 IF WS-A = 3 DISPLAY 'C'.",
            "           IF WS-A = 4",
            "              DISPLAY 'D'",
            "           END-IF.",
        )
    )
    # Cognitive: EVALUATE (1), three arms at depth 1 (2 each), two IFs in arms (3 each),
    # then the IF after the period starts again at depth 0 (1).
    assert rows["P1"][2:5] == (7, 3, 14)


def test_a_dispatch_on_one_value_is_judged_outside_its_arms() -> None:
    fn = walk_file(
        "DEMO.cbl",
        "cobol",
        _program(
            "       P1.",
            "           EVALUATE WS-A",
            "              WHEN 1 DISPLAY 'A'",
            "              WHEN 2 DISPLAY 'B'",
            "              WHEN OTHER",
            "                 IF WS-A = 3 DISPLAY 'C' END-IF",
            "           END-EVALUATE.",
        ),
    ).functions[0]
    assert (fn.ccn, fn.dispatch_share, fn.dispatch_arm) == (5, 1.0, 1)
    # EVALUATE TRUE is an IF chain, not a dispatch on a value.
    chain = walk_file(
        "DEMO.cbl",
        "cobol",
        _program(
            "       P1.",
            "           EVALUATE TRUE",
            "              WHEN WS-A = 1 DISPLAY 'A'",
            "              WHEN WS-A = 2 DISPLAY 'B'",
            "              WHEN OTHER DISPLAY 'C'",
            "           END-EVALUATE.",
        ),
    ).functions[0]
    assert chain.dispatch_share == 0.0


def test_handlers_search_arms_and_alter_count() -> None:
    rows = _rows(
        _program(
            "       P1.",
            "           READ F1 AT END MOVE 1 TO WS-A",
            "              NOT AT END MOVE 2 TO WS-A",
            "           END-READ",
            "           SEARCH WS-T",
            "              AT END DISPLAY 'NONE'",
            "              WHEN WS-A = 1 DISPLAY 'ONE'",
            "           END-SEARCH",
            "           ALTER P2 TO PROCEED TO P1.",
        )
    )
    # AT END twice (NOT AT END is its other side), SEARCH, one WHEN arm.
    assert rows["P1"][2:5] == (5, 2, 6)


def test_data_only_copybook_has_no_procedures() -> None:
    copybook = b"\n\n       01 CUSTOMER-RECORD.\n          05 CUST-ID PIC 9(9).\n"
    assert walk_file("CUSTREC.cpy", "cobol", copybook).functions == []


def test_procedure_copybook_paragraphs_are_measured() -> None:
    copybook = (
        b"\n\n       EDIT-YEAR.\n           IF WS-A = 0\n              MOVE 1 TO WS-A\n"
        b"           END-IF.\n       EDIT-YEAR-EXIT.\n           EXIT.\n"
    )
    rows = _rows(copybook, "CSUTLDPY.cpy")
    assert rows == {"EDIT-YEAR": (3, 6, 2, 1, 1, 3), "EDIT-YEAR-EXIT": (7, 8, 1, 0, 0, 1)}


def test_stacked_when_selections_each_count() -> None:
    rows = _rows(
        _program(
            "       P1.",
            "           EVALUATE WS-A",
            "              WHEN 1 THRU 3",
            "              WHEN 'WHEN'",
            "                 DISPLAY 'A'",
            "              WHEN OTHER",
            "                 DISPLAY 'B'",
            "           END-EVALUATE.",
        )
    )
    # Two stacked selections (the literal 'WHEN' is not one) and WHEN OTHER.
    assert rows["P1"][2] == 4
