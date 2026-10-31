"""``EXEC ... END-EXEC`` blocks and ``COPY`` statements, found in a code view.

Both are read from the code view (comments already blank) and masked before
the grammar sees them: the COBOL-85 grammar has no embedded SQL/CICS/DLI and
accepts ``COPY`` in only some positions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME = r"[A-Z0-9#@$][A-Z0-9#@$_-]*"

_EXEC = re.compile(
    r"(?<![\w-])EXEC\s+(?P<kind>CICS|SQL|SQLIMS|DLI)\b(?P<body>.*?)\bEND-EXEC\b",
    re.IGNORECASE | re.DOTALL,
)
_SQL_INCLUDE = re.compile(rf"^\s*INCLUDE\s+(?P<member>{_NAME})\s*$", re.IGNORECASE)
# ``COPY member [OF|IN library] [SUPPRESS] [REPLACING ...].``; pseudo-text
# (``==...==``) may hold periods, so it is matched as a unit.
_COPY = re.compile(
    rf"(?<![\w-])COPY\s+(?:'(?P<q1>[^']+)'|\"(?P<q2>[^\"]+)\"|(?P<word>{_NAME}))"
    rf"(?:\s+(?:OF|IN)\s+(?:'(?P<lq>[^']+)'|(?P<lib>{_NAME})))?"
    r"(?:\s+SUPPRESS)?"
    r"(?P<replacing>\s+REPLACING(?:\s+(?:==.*?==|[^\s.=]+))+)?"
    r"\s*\.",
    re.IGNORECASE | re.DOTALL,
)
_PROCEDURE_DIVISION = re.compile(r"\bPROCEDURE\s+DIVISION\b", re.IGNORECASE)
_DATA_DIVISION = re.compile(r"(?<![\w-])DATA\s+DIVISION\b", re.IGNORECASE)
# A division or section header of the environment or data division: none
# follows the procedure division's header.
_NOT_PROCEDURE_HEADER = re.compile(
    r"(?<![\w-])(?:(?:ENVIRONMENT|DATA)\s+DIVISION|(?:CONFIGURATION|INPUT-OUTPUT|FILE"
    r"|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE|COMMUNICATION|REPORT|SCREEN)\s+SECTION)\b",
    re.IGNORECASE,
)
# Words alone on a line with a period that open no paragraph: environment
# paragraphs, listing directives, ``DECLARATIVES``.
_NOT_A_PARAGRAPH = (
    r"(?:FILE-CONTROL|I-O-CONTROL|SPECIAL-NAMES|SOURCE-COMPUTER|OBJECT-COMPUTER|REPOSITORY"
    r"|EJECT|SKIP[123]|TITLE|DECLARATIVES)[ \t]*\."
)
# What can follow a COPY only in the procedure division: a verb, ``EXEC CICS``,
# or a paragraph or section header alone on its line. A data COPY is followed
# by an entry, a section header, another COPY or nothing.
_STATEMENT_TEXT = re.compile(
    r"\s*(?:(?:ACCEPT|ADD|CALL|COMPUTE|CONTINUE|DISPLAY|EVALUATE|GO|GOBACK|IF|INITIALIZE"
    r"|INSPECT|MOVE|OPEN|PERFORM|READ|SET|STRING|WRITE)(?![\w-])|EXEC\s+CICS\b"
    rf"|(?!{_NOT_A_PARAGRAPH}){_NAME}(?:\s+SECTION)?[ \t]*\.[ \t]*\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_CLOSING_PERIOD = re.compile(r"[ \t]*\.")
_FILLER = "CONTINUE"
_DATA_PLACEHOLDER = "01 FILLER PIC X."

_XML_TOKEN = re.compile(
    r"('[^'\n]*'|\"[^\"\n]*\")"
    r"|(?P<start>(?<![\w-])XML\s+(?:GENERATE|PARSE)\b)"
    r"|(?P<end>(?<![\w-])END-XML(?![\w-]))"
    r"|(?P<period>\.(?:\s|$))",
    re.IGNORECASE,
)


def _close_xml_block(
    tok: re.Match[str], depth: int, curr_start: int, blocks: list[tuple[int, int]]
) -> int:
    if tok.group("end"):
        depth -= 1
        if depth == 0:
            blocks.append((curr_start, tok.end()))
        return depth
    if tok.group("period"):
        blocks.append((curr_start, tok.start()))
        return 0
    return depth


def _xml_statement_blocks(code: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each ``XML GENERATE`` and ``XML PARSE`` statement.

    Terminates at ``END-XML`` (inclusive) or at a statement-ending period (exclusive),
    skipping any statements or periods inside string literals.
    """
    blocks: list[tuple[int, int]] = []
    depth = 0
    curr_start = 0
    for tok in _XML_TOKEN.finditer(code):
        if tok.group("start"):
            if depth == 0:
                curr_start = tok.start()
            depth += 1
        elif depth > 0:
            depth = _close_xml_block(tok, depth, curr_start, blocks)

    if depth > 0:
        blocks.append((curr_start, len(code)))
    return blocks


@dataclass(frozen=True)
class ExecBlock:
    """One ``EXEC kind ... END-EXEC`` block; ``body`` is the text between them."""

    kind: str
    body: str
    line: int
    end_line: int
    start: int
    end: int


@dataclass(frozen=True)
class CopyStatement:
    """A ``COPY`` (or ``EXEC SQL INCLUDE``) of a member, by its upper-cased name."""

    member: str
    library: str | None
    line: int
    replacing: bool = False
    include: bool = False


def exec_blocks(code: str) -> list[ExecBlock]:
    out = []
    for m in _EXEC.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        out.append(
            ExecBlock(
                kind=m.group("kind").upper(),
                body=m.group("body"),
                line=line,
                end_line=line + m.group(0).count("\n"),
                start=m.start(),
                end=m.end(),
            )
        )
    return out


def copy_statements(code: str) -> list[CopyStatement]:
    """Every ``COPY`` and ``EXEC SQL INCLUDE``, in source order."""
    found: list[tuple[int, CopyStatement]] = []
    for m in _COPY.finditer(code):
        member = m.group("q1") or m.group("q2") or m.group("word")
        library = m.group("lq") or m.group("lib")
        found.append(
            (
                m.start(),
                CopyStatement(
                    member=_member_name(member),
                    library=library.upper() if library else None,
                    line=code.count("\n", 0, m.start()) + 1,
                    replacing=bool(m.group("replacing")),
                ),
            )
        )
    for block in exec_blocks(code):
        include = _SQL_INCLUDE.match(block.body) if block.kind == "SQL" else None
        if include:
            member = include.group("member").upper()
            # SQLCA and SQLDA are the precompiler's own areas, never members.
            if member not in {"SQLCA", "SQLDA"}:
                found.append((block.start, CopyStatement(member, None, block.line, include=True)))
    return [statement for _, statement in sorted(found, key=lambda item: item[0])]


def procedure_division(code: str) -> tuple[int, str | None]:
    """Where *code*'s procedure division opens (``len(code)`` without one), and
    the member of the COPY that supplies its header, if a copybook does.

    A CICS program can copy its header (``COPY CENTRY.``, a member holding only
    ``PROCEDURE DIVISION.``). With no header text, the first COPY after the
    ``DATA DIVISION`` header that is followed by statement text, and by no
    environment or data division or section header, is taken as the header.
    Ceiling: the member's own text is not read, since a parse sees one file;
    reading it would thread the resolved copybooks into every parse. A header
    COPY followed by another COPY, an ``EXEC SQL`` or a verb not listed, or in
    a program with no ``DATA DIVISION`` or with a nested program, is missed.
    """
    division = _PROCEDURE_DIVISION.search(code)
    if division:
        return division.end(), None
    data = _DATA_DIVISION.search(code)
    for m in _COPY.finditer(code, data.end() if data else len(code)):
        if _STATEMENT_TEXT.match(code, m.end()) and not _NOT_PROCEDURE_HEADER.search(
            code, m.end()
        ):
            return m.end(), _member_name(m.group("q1") or m.group("q2") or m.group("word"))
    return len(code), None


def _mask_xml_statements(chars: list[str], code: str, procedure_from: int) -> None:
    # XML GENERATE and XML PARSE statements (IBM Enterprise COBOL) are masked up to
    # END-XML or the terminating period so they do not produce an ERROR node that
    # swallows the rest of the procedure division. Any nested statement in ON EXCEPTION
    # or call edge to a PROCESSING PROCEDURE is dropped with it.
    for start, end in _xml_statement_blocks(code):
        _blank(chars, start, end)
        if start >= procedure_from:
            chars[start : start + len(_FILLER)] = _FILLER


def mask_statements(code: str, procedure_from: int | None = None) -> str:
    """Blank COPY statements and EXEC blocks, keeping every line and offset.

    An EXEC block inside the procedure division becomes ``CONTINUE`` so the
    statement it stood for still parses; elsewhere it is blanked. A procedure
    copybook passes ``procedure_from=0``. A COPY supplying the division header
    becomes that header.
    """
    header = None
    if procedure_from is None:
        procedure_from, header = procedure_division(code)
    chars = list(code)
    for block in exec_blocks(code):
        end = block.end
        period = _CLOSING_PERIOD.match(code, end)
        if period and block.start < procedure_from and _follows_period(chars, block.start):
            # Between entries the period ends nothing, and one straight after a
            # section header (a DCLGEN member) does not parse. Inside an open
            # entry it is that entry's terminator and stays.
            end = period.end()
        _blank(chars, block.start, end)
        if block.start >= procedure_from and not _SQL_INCLUDE.match(block.body):
            chars[block.start : block.start + len(_FILLER)] = _FILLER
    _mask_xml_statements(chars, code, procedure_from)
    masked = "".join(chars)
    # Right to left, so a replacement of another length leaves earlier offsets alone.
    for m in reversed(list(_COPY.finditer(masked))):
        newlines = "".join(c for c in m.group(0) if c in "\r\n")
        # In data, an FD or group header needs the record the copybook supplied.
        if header and m.end() == procedure_from:
            text = "PROCEDURE DIVISION."
        else:
            text = _DATA_PLACEHOLDER if m.start() < procedure_from else ""
        masked = masked[: m.start()] + text + newlines + masked[m.end() :]
    return masked


def _follows_period(chars: list[str], start: int) -> bool:
    """Whether the code before *start* is empty or ends with a period."""
    i = start - 1
    while i >= 0 and chars[i].isspace():
        i -= 1
    return i < 0 or chars[i] == "."


def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        if chars[i] not in "\r\n":
            chars[i] = " "


def _member_name(raw: str) -> str:
    """``'lgpolicy.cpy'`` and ``LGPOLICY`` both name the member ``LGPOLICY``."""
    name = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.upper()
