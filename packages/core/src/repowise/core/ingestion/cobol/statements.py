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
_CLOSING_PERIOD = re.compile(r"[ \t]*\.")
_FILLER = "CONTINUE"
_DATA_PLACEHOLDER = "01 FILLER PIC X."


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


def mask_statements(code: str, procedure_from: int | None = None) -> str:
    """Blank COPY statements and EXEC blocks, keeping every line and offset.

    An EXEC block inside the procedure division becomes ``CONTINUE`` so the
    statement it stood for still parses; elsewhere it is blanked. A procedure
    copybook passes ``procedure_from=0``.
    """
    if procedure_from is None:
        division = _PROCEDURE_DIVISION.search(code)
        procedure_from = division.end() if division else len(code)
    chars = list(code)
    for block in exec_blocks(code):
        end = block.end
        if block.start < procedure_from and (period := _CLOSING_PERIOD.match(code, end)):
            # Outside the procedure division the period ends no entry, and one
            # straight after a section header (a DCLGEN member) does not parse.
            end = period.end()
        _blank(chars, block.start, end)
        if block.start >= procedure_from and not _SQL_INCLUDE.match(block.body):
            chars[block.start : block.start + len(_FILLER)] = _FILLER
    masked = "".join(chars)
    # Right to left, so a replacement of another length leaves earlier offsets alone.
    for m in reversed(list(_COPY.finditer(masked))):
        newlines = "".join(c for c in m.group(0) if c in "\r\n")
        # In data, an FD or group header needs the record the copybook supplied.
        text = _DATA_PLACEHOLDER if m.start() < procedure_from else ""
        masked = masked[: m.start()] + text + newlines + masked[m.end() :]
    return masked


def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        if chars[i] not in "\r\n":
            chars[i] = " "


def _member_name(raw: str) -> str:
    """``'lgpolicy.cpy'`` and ``LGPOLICY`` both name the member ``LGPOLICY``."""
    name = raw.strip().replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.upper()
