"""COBOL programs are entered by JCL/schedulers outside the source graph."""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer


def _graph(language: str) -> nx.DiGraph:
    graph = nx.DiGraph()
    path = f"jobs/payroll.{language}"
    symbol_id = f"{path}::PAYROLL"
    graph.add_node(
        path,
        node_type="file",
        language=language,
        is_entry_point=False,
        is_test=False,
        symbol_count=1,
    )
    graph.add_node(
        symbol_id,
        node_type="symbol",
        file_path=path,
        language=language,
        name="PAYROLL",
        kind="module",
        visibility="public",
        start_line=1,
        end_line=20,
    )
    graph.add_edge(path, symbol_id, edge_type="defines")
    return graph


def test_cobol_file_and_symbols_are_not_reported_as_dead() -> None:
    report = DeadCodeAnalyzer(_graph("cobol"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert report.findings == []


def test_ordinary_code_language_remains_in_scope() -> None:
    report = DeadCodeAnalyzer(_graph("python"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert {finding.kind for finding in report.findings} == {"unreachable_file"}


def _private_method_graph(language: str) -> nx.DiGraph:
    graph = nx.DiGraph()
    path = f"src/Payroll.{language}"
    graph.add_node(path, node_type="file", language=language, is_entry_point=True,
                   is_test=False, symbol_count=1)
    graph.add_node(f"{path}::Helper", node_type="symbol", file_path=path, language=language,
                   name="Helper", kind="class", visibility="private", start_line=5, end_line=8)
    graph.add_edge(path, f"{path}::Helper", edge_type="defines")
    return graph


def test_exempt_language_private_symbol_is_not_an_unused_internal() -> None:
    # The internal pass honours the exemption like the file and export passes.
    report = DeadCodeAnalyzer(_private_method_graph("cobol"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert report.findings == []
    control = DeadCodeAnalyzer(_private_method_graph("java"), git_meta_map={}).analyze(
        {"detect_zombie_packages": False}
    )
    assert "unused_internal" in {finding.kind for finding in control.findings}


# A paragraph is reported only when nothing names it and nothing falls into it.
_HEAD = (
    "       IDENTIFICATION DIVISION.\n"
    "       PROGRAM-ID. PAYROLL.\n"
    "       PROCEDURE DIVISION.\n"
    "       MAIN-PARA.\n"
    "           PERFORM WORK-PARA THRU WORK-EXIT\n"
)


def _unreachable(body: str, copied: dict[str, str] | None = None) -> list[str]:
    from repowise.core.analysis.dead_code.cobol_paragraphs import unreachable_paragraphs

    return [name for name, _, _ in unreachable_paragraphs(_HEAD + body, copied or {})]


def test_an_unnamed_paragraph_after_an_unconditional_return_is_unreachable() -> None:
    body = (
        "           IF A = 1\n               MOVE 2 TO B\n           END-IF\n"
        "           EXEC CICS RETURN TRANSID('PAY1') END-EXEC\n           .\n"
        "       MAIN-EXIT.\n           EXIT.\n"
        "       WORK-PARA.\n           GOBACK.\n"
        "       WORK-EXIT.\n           EXIT.\n"
    )
    assert _unreachable(body) == ["MAIN-EXIT"]


def test_fall_through_and_named_paragraphs_are_not_reported() -> None:
    cases = {
        # The terminal statement is conditional.
        "conditional": "           IF A = 1\n               GOBACK.\n       NEXT-PARA.\n           EXIT.\n",
        "read_at_end": "           READ F AT END GOBACK.\n       NEXT-PARA.\n           EXIT.\n",
        # Control can fall through: no transfer, EXIT PROGRAM, or a RESP that returns.
        "falls": "           MOVE 1 TO A.\n       NEXT-PARA.\n           EXIT.\n",
        "exit_program": "           EXIT PROGRAM.\n       NEXT-PARA.\n           EXIT.\n",
        "resp": "           EXEC CICS RETURN RESP(WS-RESP) END-EXEC.\n"
        "       NEXT-PARA.\n           EXIT.\n",
        "next_sentence": "           IF A = 1 NEXT SENTENCE END-IF GOBACK.\n"
        "       NEXT-PARA.\n           EXIT.\n",
        # Named elsewhere: a GO TO, an ALTER, a CICS handler.
        "go_to": "           GOBACK.\n       NEXT-PARA.\n           GO TO NEXT-PARA.\n",
        "handler": "           EXEC CICS HANDLE AID PF3(NEXT-PARA) END-EXEC\n"
        "           GOBACK.\n       NEXT-PARA.\n           EXIT.\n",
        # The first paragraph of a section runs when the section is performed.
        "section": "           GOBACK.\n       S1 SECTION.\n       NEXT-PARA.\n           EXIT.\n",
        # A program with an ENTRY can start anywhere.
        "entry": "           GOBACK.\n       NEXT-PARA.\n           ENTRY 'ALT' USING A.\n",
    }
    for case, body in cases.items():
        assert _unreachable(body) == [], case


def test_a_procedure_copybook_naming_the_paragraph_or_unresolved_keeps_it() -> None:
    body = "           GOBACK.\n       NEXT-PARA.\n           EXIT.\n       COPY PROCS.\n"
    assert _unreachable(body) == []
    assert _unreachable(body, {"PROCS": "       P2.\n           PERFORM NEXT-PARA.\n"}) == []
    assert _unreachable(body, {"PROCS": "       P2.\n           EXIT.\n"}) == ["NEXT-PARA"]


def test_cobol_paragraph_findings_are_low_and_never_deletion_ready() -> None:
    path = "cbl/PAYROLL.cbl"
    source = (
        _HEAD + "           GOBACK.\n"
        "       WORK-PARA.\n           EXIT.\n"
        "       WORK-EXIT.\n           GOBACK.\n"
        "       DEAD-PARA.\n           DISPLAY 'X'.\n"
    )
    graph = _graph("cobol")
    graph.add_node(path, node_type="file", language="cobol", is_entry_point=False,
                   is_test=False, symbol_count=0)
    report = DeadCodeAnalyzer(
        graph, git_meta_map={}, source_map={path: source.encode()}
    ).analyze({"detect_zombie_packages": False, "min_confidence": 0.0})

    assert [(f.kind, f.symbol_name) for f in report.findings] == [("unused_internal", "DEAD-PARA")]
    finding = report.findings[0]
    assert finding.confidence < 0.4
    assert finding.safe_to_delete is False
    assert (finding.start_line, finding.end_line) == (11, 12)


def test_rewritten_or_unread_copybooks_keep_every_paragraph() -> None:
    body = (
        "           GOBACK.\n       ACCT-INIT.\n           EXIT.\n"
        "       COPY PROCS REPLACING ==:P:== BY ==ACCT==.\n"
    )
    assert _unreachable(body, {"PROCS": "       P2.\n           PERFORM :P:-INIT.\n"}) == []
    replace = "           GOBACK.\n       ACCT-INIT.\n           EXIT.\n       REPLACE ==A== BY ==B==.\n"
    assert _unreachable(replace) == []
    nested = "           GOBACK.\n       NEXT-PARA.\n           EXIT.\n       COPY OUTER.\n"
    outer = "       P2.\n           EXIT.\n       COPY INNER.\n"
    # A copybook copied by a procedure copybook is searched too, and must resolve.
    assert _unreachable(nested, {"OUTER": outer}) == []
    assert _unreachable(nested, {"OUTER": outer, "INNER": "           PERFORM NEXT-PARA.\n"}) == []
    assert _unreachable(nested, {"OUTER": outer, "INNER": "           EXIT.\n"}) == ["NEXT-PARA"]


def test_control_paths_the_text_names_or_runs_into_are_kept() -> None:
    cases = {
        # PERFORM A THRU C runs B by falling out of A.
        "thru_interior": "           PERFORM A-PARA THRU C-PARA\n           GOBACK.\n"
        "       A-PARA.\n           MOVE 1 TO X.\n       B-PARA.\n           EXIT.\n"
        "       C-PARA.\n           EXIT.\n",
        # PERFORM of a section runs its paragraphs in order.
        "section": "           PERFORM S1\n           GOBACK.\n       S1 SECTION.\n"
        "       P1.\n           MOVE 1 TO X.\n       P2.\n           EXIT.\n",
        "lowercase_mention": "           perform next-para\n           GOBACK.\n"
        "       NEXT-PARA.\n           EXIT.\n",
        "handle_abend": "           EXEC CICS HANDLE ABEND LABEL(NEXT-PARA) END-EXEC\n"
        "           GOBACK.\n       NEXT-PARA.\n           EXIT.\n",
        "handle_condition": "           EXEC CICS HANDLE CONDITION ERROR(NEXT-PARA) END-EXEC\n"
        "           GOBACK.\n       NEXT-PARA.\n           EXIT.\n",
        # Out of range, GO TO DEPENDING ON falls through to the next statement.
        "depending_on": "           GO TO A-PARA DEPENDING ON IX.\n"
        "       NEXT-PARA.\n           EXIT.\n       A-PARA.\n           EXIT.\n",
        "nohandle": "           EXEC CICS RETURN NOHANDLE END-EXEC.\n"
        "       NEXT-PARA.\n           EXIT.\n",
        "resp": "           EXEC CICS XCTL PROGRAM('X') RESP(R) END-EXEC.\n"
        "       NEXT-PARA.\n           EXIT.\n",
        # A scope terminator in area A is no paragraph header.
        "end_verb": "           GOBACK.\n       END-PERFORM.\n",
    }
    for case, body in cases.items():
        assert "NEXT-PARA" not in _unreachable(body) and "B-PARA" not in _unreachable(body), case
        assert "P2" not in _unreachable(body) and "END-PERFORM" not in _unreachable(body), case


def test_lowercase_source_is_read_case_blind() -> None:
    body = "           goback.\n       next-para.\n           exit.\n"
    assert _unreachable(body) == ["NEXT-PARA"]


def test_end_program_is_a_paragraph_name_not_a_scope_terminator() -> None:
    body = (
        "           GO TO END-PROGRAM.\n       END-PROGRAM.\n"
        "           EXEC CICS RETURN END-EXEC.\n       MAINLINE-EXIT.\n           EXIT.\n"
    )
    assert _unreachable(body) == ["MAINLINE-EXIT"]


def test_a_program_whose_procedure_header_is_copied_is_judged() -> None:
    from repowise.core.analysis.dead_code.cobol_paragraphs import unreachable_paragraphs

    code = (
        "       IDENTIFICATION DIVISION.\n"
        "       PROGRAM-ID. CICSPGM.\n"
        "       DATA DIVISION.\n"
        "       LINKAGE SECTION.\n"
        "       01 DFHCOMMAREA PIC X.\n"
        "       COPY CENTRY.\n"
        "           PERFORM WORK-PARA.\n"
        "           GOBACK.\n"
        "       WORK-PARA.\n"
        "           GOBACK.\n"
        "       LOST-PARA.\n"
        "           EXIT.\n"
    )
    header = {"CENTRY": "       PROCEDURE DIVISION.\n"}

    assert unreachable_paragraphs(code, header) == [("LOST-PARA", 11, 12)]
    # Unresolved, or a member that is not the header: the program is not judged.
    assert unreachable_paragraphs(code, {}) == []
    assert unreachable_paragraphs(code, {"CENTRY": "       MOVE 1 TO A.\n"}) == []
