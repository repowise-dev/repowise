"""``cobol_unstructured_flow``: ALTER, GO TO ... DEPENDING ON and GO TO out of a
PERFORM range fire; GO TO to the range's own exit stays silent; advisory."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.scoring import biomarker_dimension, dimensions_for, score_file

grammar_pack = pytest.importorskip("tree_sitter_language_pack")
try:
    grammar_pack.get_language("cobol")
except (grammar_pack.Error, OSError) as exc:  # pragma: no cover - first-use download
    pytest.skip(f"COBOL grammar unavailable: {exc}", allow_module_level=True)

_KIND = "cobol_unstructured_flow"


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


def _hits(source: bytes) -> list[tuple[str | None, str]]:
    fcx = walk_file("DEMO.cbl", "cobol", source)
    return [(h.function, h.detail) for h in fcx.perf_hits if h.kind == _KIND]


def _findings(source: bytes):
    fcx = walk_file("DEMO.cbl", "cobol", source)
    ctx = FileContext(
        file_path="DEMO.cbl", language="cobol", nloc=20, has_test_file=False,
        module=None, perf_hits=fcx.perf_hits,
    )
    return [r for r in detect_all(ctx) if r.biomarker_type == _KIND]


def test_go_to_own_range_exit_is_silent() -> None:
    assert _hits(
        _program(
            "           PERFORM A THRU A-EXIT.",
            "           GOBACK.",
            "       A.",
            "           IF WS-A = 1",
            "              GO TO A-EXIT",
            "           END-IF",
            "           GO TO A-COMMON-EXIT.",
            "       A-COMMON-EXIT.",
            "           MOVE 0 TO WS-A.",
            "       A-EXIT.",
            "           EXIT.",
        )
    ) == []


def test_go_to_elsewhere_fires() -> None:
    assert _hits(
        _program(
            "           PERFORM A THRU A-EXIT.",
            "           GOBACK.",
            "       A.",
            "           GO TO COMMON-RETURN.",
            "       A-EXIT.",
            "           EXIT.",
            "       COMMON-RETURN.",
            "           GOBACK.",
        )
    ) == [("A", "GO TO COMMON-RETURN")]


def test_go_to_an_enclosing_exit_from_a_later_range_fires() -> None:
    # B is performed on its own range and sits after A-EXIT: jumping back to
    # A-EXIT abandons B's PERFORM.
    assert _hits(
        _program(
            "           PERFORM A THRU A-EXIT.",
            "           GOBACK.",
            "       A.",
            "           PERFORM B THRU B-EXIT.",
            "       A-EXIT.",
            "           EXIT.",
            "       B.",
            "           GO TO A-EXIT.",
            "       B-EXIT.",
            "           EXIT.",
        )
    ) == [("B", "GO TO A-EXIT")]


def test_loop_back_to_own_header_fires() -> None:
    assert _hits(
        _program(
            "       A-GAIN.",
            "           ADD 1 TO WS-A",
            "           IF WS-A < 5 GO TO A-GAIN END-IF.",
        )
    ) == [("A-GAIN", "GO TO A-GAIN")]


def test_alter_and_depending_on_fire() -> None:
    assert _hits(
        _program(
            "       A.",
            "           ALTER B TO PROCEED TO C",
            "           GO TO C D DEPENDING ON WS-A.",
            "       B.",
            "           GO TO.",
            "       C.",
            "           EXIT.",
            "       D.",
            "           EXIT.",
        )
    ) == [
        ("A", "ALTER B TO PROCEED TO C"),
        ("A", "GO TO C D DEPENDING ON WS-A"),
        ("B", "GO TO"),
    ]


def test_unperformed_paragraph_trusts_the_exit_name() -> None:
    # A copybook's paragraphs are performed from the program that copies it.
    assert _hits(
        _program(
            "       EDIT-DAY.",
            "           GO TO EDIT-DAY-EXIT.",
            "       EDIT-DAY-EXIT.",
            "           EXIT.",
        )
    ) == []


def test_qualified_go_to_and_thru_resolve_in_their_section() -> None:
    assert _hits(
        _program(
            "           PERFORM A OF S1 THRU A-EXIT IN S1.",
            "           GOBACK.",
            "       S1 SECTION.",
            "       A.",
            "           GO TO A-EXIT OF S1.",
            "       A-EXIT.",
            "           EXIT.",
        )
    ) == []


def test_thru_spanning_sections() -> None:
    assert _hits(
        _program(
            "           PERFORM S1 THRU S2.",
            "           GOBACK.",
            "       S1 SECTION.",
            "       A.",
            "           GO TO S2-EXIT.",
            "       S2 SECTION.",
            "       B.",
            "           MOVE 1 TO WS-A.",
            "       S2-EXIT.",
            "           EXIT.",
        )
    ) == []


def test_thru_on_a_continuation_line_and_lowercase_names() -> None:
    assert _hits(
        _program(
            "           perform a",
            "              thru a-exit.",
            "           goback.",
            "       a.",
            "           go to a-exit.",
            "       a-exit.",
            "           exit.",
        )
    ) == []


def test_duplicate_names_resolve_in_the_current_section() -> None:
    # Each section has its own P-EXIT; Q's jump means S2's, which a
    # first-header lookup would place behind it.
    assert _hits(
        _program(
            "           PERFORM P THRU P-EXIT OF S1.",
            "           PERFORM Q THRU P-EXIT OF S2.",
            "           GOBACK.",
            "       S1 SECTION.",
            "       P.",
            "           GO TO P-EXIT.",
            "       P-EXIT.",
            "           EXIT.",
            "       S2 SECTION.",
            "       Q.",
            "           GO TO P-EXIT.",
            "       P-EXIT.",
            "           EXIT.",
        )
    ) == []


def test_go_to_from_the_range_end_to_a_later_exit_fires() -> None:
    assert _hits(
        _program(
            "           PERFORM A THRU A-EXIT.",
            "           GOBACK.",
            "       A.",
            "           MOVE 1 TO WS-A.",
            "       A-EXIT.",
            "           GO TO Z-EXIT.",
            "       Z-EXIT.",
            "           EXIT.",
        )
    ) == [("A-EXIT", "GO TO Z-EXIT")]


def test_depending_on_reason_makes_no_claim_about_targets() -> None:
    (finding,) = _findings(
        _program(
            "       A.",
            "           GO TO B C DEPENDING ON WS-A.",
            "       B.",
            "           EXIT.",
            "       C.",
            "           EXIT.",
        )
    )
    assert finding.reason == (
        "GO TO B C DEPENDING ON WS-A picks A's next paragraph by value; "
        "a reader must trace each target by hand"
    )


@pytest.mark.parametrize("target", ["CHECKEXIT", "NOEXIT"])
def test_exit_needs_a_hyphen_suffix(target: str) -> None:
    assert _hits(
        _program(
            "           PERFORM A THRU " + target + ".",
            "           GOBACK.",
            "       A.",
            "           GO TO " + target + ".",
            "       " + target + ".",
            "           EXIT.",
        )
    ) == [("A", "GO TO " + target)]


def test_one_finding_per_procedure_and_advisory() -> None:
    results = _findings(
        _program(
            "       A.",
            "           IF WS-A = 1 GO TO Z END-IF",
            "           GO TO Z.",
            "       Z.",
            "           GOBACK.",
        )
    )
    assert len(results) == 1
    (finding,) = results
    assert finding.function_name == "A"
    assert finding.details["count"] == 2
    assert finding.details["first_line"] == 8
    assert finding.reason.startswith("GO TO Z leaves A outside its PERFORM range")
    assert dimensions_for(_KIND) == {"advisory"}
    assert biomarker_dimension(_KIND) == "advisory"
    scores, deductions = score_file(results)
    assert set(deductions) == {0.0}
    assert scores["defect"] == scores["maintainability"] == scores["performance"] == 10.0


def test_jumps_keep_the_cognitive_point() -> None:
    fcx = walk_file("DEMO.cbl", "cobol", _program("       A.", "           GO TO Z.", "       Z.", "           GOBACK."))
    assert {f.name: f.cognitive for f in fcx.functions}["A"] == 1
