"""COBOL source preparation: what the grammar rejects is masked, and every line keeps its number."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.cobol import code_view, copy_statements, exec_blocks
from repowise.core.ingestion.cobol.fragments import is_procedure_fragment
from repowise.core.ingestion.parser import ASTParser

from ._helpers import _make_file_info

grammar_pack = pytest.importorskip("tree_sitter_language_pack")
try:
    grammar_pack.get_language("cobol")
except Exception as exc:  # pragma: no cover - depends on first-use grammar cache/network
    pytest.skip(f"COBOL grammar unavailable: {exc}", allow_module_level=True)


def _program(data: str = "", procedure: str = "", header: str = "") -> bytes:
    lines = [
        "       IDENTIFICATION DIVISION.",
        "       PROGRAM-ID. DEMO.",
        *([header] if header else []),
        "       DATA DIVISION.",
        "       WORKING-STORAGE SECTION.",
        "       01 WS-A PIC X(10).",
        "       01 WS-B PIC X(10).",
        *([data] if data else []),
        "       PROCEDURE DIVISION.",
        "       MAIN-PARA.",
        procedure or "           CONTINUE.",
        "           PERFORM LAST-PARA.",
        "       LAST-PARA.",
        "           GOBACK.",
    ]
    return ("\n".join(lines) + "\n").encode()


def _parse(source: bytes, path: str = "DEMO.cbl"):
    return ASTParser().parse_file(_make_file_info(path, "cobol"), source)


def _names(parsed) -> dict[str, int]:
    return {s.name: s.start_line for s in parsed.symbols}


@pytest.mark.parametrize(
    "case",
    [
        _program(procedure="           EXEC CICS RETURN TRANSID('X') END-EXEC."),
        _program(
            procedure="           EXEC SQL\n             SELECT 1 INTO :WS-A FROM T\n           END-EXEC."
        ),
        _program(data="       COPY CVACT01Y."),
        _program(data="       01 GROUP-A.\n       COPY CVACT01Y."),
        _program(procedure="           IF WS-A NOT= WS-B CONTINUE END-IF."),
        _program(procedure="           INSPECT WS-A REPLACING ALL x'00' BY x'40'."),
        _program(procedure="           MOVE FUNCTION CURRENT-DATE(1:4) TO WS-A."),
        _program(procedure="           ENTRY 'DLITCBL' USING WS-A."),
        _program(header="       AUTHOR. ME.\n       DATE-COMPILED."),
        _program(data="       LINKAGE SECTION."),
        _program(
            data="       01 WS-L PIC X(70) VALUE 'ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTU\n"
            "      -    'VWXYZ'."
        ),
    ],
    ids=[
        "exec-cics",
        "exec-sql",
        "copy",
        "copy-under-group",
        "not-equal",
        "hex-lowercase",
        "function-refmod",
        "entry",
        "comment-entries",
        "empty-linkage",
        "continuation",
    ],
)
def test_constructs_the_grammar_rejects_parse_with_every_paragraph(case: bytes) -> None:
    parsed = _parse(case)

    assert parsed.parse_errors == []
    names = _names(parsed)
    lines = case.decode().split("\n")
    for paragraph in ("MAIN-PARA", "LAST-PARA"):
        assert lines[names[paragraph] - 1].strip() == f"{paragraph}."


def test_sequence_numbers_and_comment_lines_are_ignored() -> None:
    source = (
        b"000100 IDENTIFICATION DIVISION.                                         00010000\n"
        b"000200 PROGRAM-ID. SEQ.                                                 00020000\n"
        b"000300* A COMMENT WITH PERFORM NOWHERE.                                  00030000\n"
        b"000400 PROCEDURE DIVISION.                                              00040000\n"
        b"000500 FIRST-PARA.                                                      00050000\n"
        b"000600     GOBACK.                                                      00060000\n"
    )

    parsed = _parse(source)

    assert parsed.parse_errors == []
    assert _names(parsed) == {"SEQ": 1, "FIRST-PARA": 5}


def test_a_data_copybook_parses_as_a_fragment_at_its_own_lines() -> None:
    source = (
        b"      *****************************************************\n"
        b"      *    ACCOUNT RECORD\n"
        b"      *****************************************************\n"
        b"       01  ACCOUNT-RECORD.\n"
        b"           05  ACCT-ID                 PIC 9(11).\n"
        b"           05  FILLER                  PIC X(10).\n"
    )

    parsed = _parse(source, "CVACT01Y.cpy")

    assert parsed.parse_errors == []
    # No synthetic program and no FILLER: only the copybook's named items.
    assert _names(parsed) == {"ACCOUNT-RECORD": 4, "ACCT-ID": 5}


def test_a_procedure_copybook_yields_its_paragraphs() -> None:
    source = (
        b"      * COMMON CODE\n"
        b"      *\n"
        b"       YYYY-STORE-PFKEY.\n"
        b"           EXEC CICS RETURN END-EXEC.\n"
        b"       YYYY-STORE-PFKEY-EXIT.\n"
        b"           EXIT.\n"
    )

    parsed = _parse(source, "CSSTRPFY.cpy")

    assert parsed.parse_errors == []
    assert _names(parsed) == {"YYYY-STORE-PFKEY": 3, "YYYY-STORE-PFKEY-EXIT": 5}


def test_a_dclgen_member_yields_its_record_after_the_table_declaration() -> None:
    source = (
        b"      * DCLGEN TABLE(CARDDEMO.TRANSACTION_TYPE)\n"
        b"      *\n"
        b"           EXEC SQL DECLARE CARDDEMO.TRANSACTION_TYPE TABLE\n"
        b"           ( TR_TYPE CHAR(2) NOT NULL\n"
        b"           ) END-EXEC.\n"
        b"       01  DCLTRTYP.\n"
        b"           10 TR-TYPE              PIC X(2).\n"
    )

    parsed = _parse(source, "DCLTRTYP.dcl")

    assert parsed.parse_errors == []
    assert _names(parsed) == {"DCLTRTYP": 6, "TR-TYPE": 7}


def _working_storage(*entries: bytes) -> bytes:
    return (
        b"       IDENTIFICATION DIVISION.\n"
        b"       PROGRAM-ID. DEMO.\n"
        b"       DATA DIVISION.\n"
        b"       WORKING-STORAGE SECTION.\n"
        + b"".join(entries)
        + b"       PROCEDURE DIVISION.\n"
        b"           STOP RUN.\n"
    )


def test_an_exec_block_inside_an_open_entry_keeps_its_terminating_period() -> None:
    # The period after END-EXEC ends ``01 A``; blanking it would read B into A.
    source = _working_storage(
        b"       01 A PIC X\n",
        b"           EXEC SQL INCLUDE SQLCA END-EXEC.\n",
        b"       01 B PIC X.\n",
    )

    parsed = _parse(source)

    assert parsed.parse_errors == []
    assert _names(parsed) == {"DEMO": 1, "A": 5, "B": 7}


def test_an_exec_block_between_entries_parses() -> None:
    source = _working_storage(
        b"       01 A PIC X.\n",
        b"           EXEC SQL INCLUDE SQLCA END-EXEC.\n",
        b"       01 B PIC X.\n",
    )

    parsed = _parse(source)

    assert parsed.parse_errors == []
    assert _names(parsed) == {"DEMO": 1, "A": 5, "B": 7}


def test_a_data_copybook_opening_with_a_sql_include_is_data() -> None:
    source = b"           EXEC SQL INCLUDE SQLCA END-EXEC.\n       01 A PIC X.\n"

    assert not is_procedure_fragment(code_view(source))


def test_a_procedure_copybook_opening_with_exec_cics_stays_procedure() -> None:
    source = b"           EXEC CICS RETURN END-EXEC.\n       PARA-A.\n           EXIT.\n"

    assert is_procedure_fragment(code_view(source))


def test_a_free_format_data_copybook_is_not_read_as_procedure_text() -> None:
    source = b"\n\n>>SOURCE FORMAT FREE\n01 ACCOUNT-RECORD.\n   05 ACCT-ID PIC 9(11).\n"

    assert not is_procedure_fragment(code_view(source))


def test_floating_comments_hide_statements_and_keep_literals() -> None:
    source = (
        b">>SOURCE FORMAT FREE\n"
        b"IDENTIFICATION DIVISION.\n"
        b"PROGRAM-ID. DEMO.\n"
        b"PROCEDURE DIVISION.\n"
        b"MAIN-PARA. *> EXEC CICS is issued in LAST-PARA; COPY OLDMEM.\n"
        b"    DISPLAY '*> kept'.\n"
        b"    PERFORM LAST-PARA.\n"
        b"LAST-PARA.\n"
        b"    EXEC CICS RETURN END-EXEC.\n"
    )

    code = code_view(source)

    assert copy_statements(code) == []
    assert [block.line for block in exec_blocks(code)] == [9]
    assert "'*> kept'" in code
    assert len(code.splitlines()) == len(source.splitlines())


def test_copy_statements_are_found_in_code_and_not_in_comments() -> None:
    source = (
        b"       IDENTIFICATION DIVISION.\n"
        b"       PROGRAM-ID. DEMO.\n"
        b"       DATA DIVISION.\n"
        b"       WORKING-STORAGE SECTION.\n"
        b"      *COPY COMMENTED.\n"
        b"       COPY COCOM01Y.\n"
        b"       COPY 'lgpolicy.cpy'.\n"
        b"       COPY CSSETATY REPLACING\n"
        b"             ==(TESTVAR1)== BY ==ACCT.==.\n"
        b"       COPY MEMBER1 OF MYLIB.\n"
        b"           EXEC SQL INCLUDE SQLCA END-EXEC.\n"
        b"           EXEC SQL INCLUDE DCLTRTYP END-EXEC.\n"
    )

    found = [
        (c.member, c.library, c.line, c.replacing, c.include)
        for c in copy_statements(code_view(source))
    ]

    assert found == [
        ("COCOM01Y", None, 6, False, False),
        ("LGPOLICY", None, 7, False, False),
        ("CSSETATY", None, 8, True, False),
        ("MEMBER1", "MYLIB", 10, False, False),
        ("DCLTRTYP", None, 12, False, True),
    ]


def _centry_program(after_linkage: str) -> bytes:
    lines = [
        "       IDENTIFICATION DIVISION.",
        "       PROGRAM-ID. DEMO.",
        "       DATA DIVISION.",
        "       WORKING-STORAGE SECTION.",
        "       01 WS-AREA.",
        "       COPY CAREA.",
        "       01 WS-A PIC X(10).",
        "       LINKAGE SECTION.",
        "       01 DFHCOMMAREA PIC X(10).",
        after_linkage,
    ]
    return ("\n".join(lines) + "\n").encode()


def test_a_copybook_supplying_the_procedure_header_opens_the_procedure() -> None:
    # The CICS idiom: ``CENTRY`` holds only ``PROCEDURE DIVISION.``.
    source = _centry_program(
        "       COPY CENTRY.\n"
        "           MOVE DFHCOMMAREA TO WS-A.\n"
        "           CALL 'SSECURE' USING WS-A.\n"
        "           PERFORM WORK-PARA.\n"
        "       COPY CRETURN.\n"
        "       WORK-PARA.\n"
        "           MOVE SPACES TO WS-A.\n"
    )

    parsed = _parse(source)

    assert parsed.parse_errors == []
    assert _names(parsed)["WORK-PARA"] == 15
    assert {"SSECURE", "WORK-PARA"} <= {call.target_name for call in parsed.calls}
    assert [i.module_path for i in parsed.imports] == ["CAREA", "CENTRY", "CRETURN"]


def test_a_header_copybook_followed_by_a_paragraph_opens_the_procedure() -> None:
    parsed = _parse(_centry_program("       COPY CENTRY.\n       MAIN-PARA.\n           GOBACK.\n"))

    assert parsed.parse_errors == []
    assert _names(parsed)["MAIN-PARA"] == 11


@pytest.mark.parametrize(
    "after_linkage",
    [
        # A LINKAGE copybook, then more data.
        "       COPY CLINK.\n       01 LK-B PIC X.\n",
        # A copybook that ends the data division with nothing after it.
        "       COPY CLINK.\n",
        # A copybook followed by another data section.
        "       COPY CLINK.\n       LOCAL-STORAGE SECTION.\n       01 LS-A PIC X.\n",
        # Copybooks in a row: the next one is not statement text.
        "       COPY CLINK.\n       COPY CLINK2.\n",
        # Listing directives with a period are no paragraph headers.
        "       COPY CLINK.\n       EJECT.\n",
        "       COPY CLINK.\n       SKIP1.\n",
    ],
    ids=["more-data", "nothing-after", "data-section", "copy-after-copy", "eject", "skip1"],
)
def test_a_data_division_copybook_never_opens_the_procedure(after_linkage: str) -> None:
    from repowise.core.ingestion.cobol import prepare_cobol_source

    prepared = prepare_cobol_source(_centry_program(after_linkage)).decode()

    assert "PROCEDURE" not in prepared
    assert prepared.count("01 FILLER PIC X.") == after_linkage.count("COPY") + 1


@pytest.mark.parametrize(
    "environment",
    [
        "       INPUT-OUTPUT SECTION.\n       COPY CFC.\n       FILE-CONTROL.\n",
        "       CONFIGURATION SECTION.\n       COPY CSRC.\n       SPECIAL-NAMES.\n",
        "       CONFIGURATION SECTION.\n       COPY CSRC.\n       OBJECT-COMPUTER.\n",
        "       INPUT-OUTPUT SECTION.\n       COPY CSEL.\n       I-O-CONTROL.\n",
    ],
    ids=["file-control", "special-names", "object-computer", "i-o-control"],
)
@pytest.mark.parametrize("data", [True, False], ids=["with-data", "no-data"])
def test_an_environment_division_copybook_never_opens_the_procedure(
    environment: str, data: bool
) -> None:
    from repowise.core.ingestion.cobol import prepare_cobol_source

    data_division = "       DATA DIVISION.\n       WORKING-STORAGE SECTION.\n       01 A PIC X.\n"
    source = (
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. DEMO.\n"
        "       ENVIRONMENT DIVISION.\n" + environment + (data_division if data else "")
    )

    assert "PROCEDURE" not in prepare_cobol_source(source.encode()).decode()


def test_a_program_with_its_procedure_header_prepares_as_before() -> None:
    from repowise.core.ingestion.cobol import prepare_cobol_source

    source = (
        b"       IDENTIFICATION DIVISION.\n"
        b"       PROGRAM-ID. DEMO.\n"
        b"       DATA DIVISION.\n"
        b"       WORKING-STORAGE SECTION.\n"
        b"       COPY CAREA.\n"
        b"       PROCEDURE DIVISION.\n"
        b"       COPY CENTRY.\n"
        b"           MOVE 1 TO A.\n"
        b"           EXEC CICS RETURN END-EXEC.\n"
    )

    # The data COPY becomes a placeholder entry; the procedure COPY is blanked.
    assert prepare_cobol_source(source) == (
        b"       IDENTIFICATION DIVISION.\n"
        b"       PROGRAM-ID. DEMO.\n"
        b"       DATA DIVISION.\n"
        b"       WORKING-STORAGE SECTION.\n"
        b"       01 FILLER PIC X.\n"
        b"       PROCEDURE DIVISION.\n"
        b"       \n"
        b"           MOVE 1 TO A.\n"
        b"           CONTINUE                 .\n"
    )
