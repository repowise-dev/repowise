"""JCL jobs and procedures: their steps and the programs and PROCs those run.

Read line by line: ``//*`` comments are skipped and a statement continued on
the next ``//`` line is joined to it. A step runs ``PGM=X`` or a procedure
(``EXEC X`` / ``EXEC PROC=X``). In-stream data (``DD *`` / ``DD DATA``, up to
``/*``, the next ``//`` statement, or its ``DLM=`` delimiter) is kept apart, for
the CSD definitions a DFHCSDUP step reads there. Ceiling: data under ``DLM=``
still passes the statement joiner, so a ``//*`` data line is dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import FileInfo, ParsedFile, Symbol

_NAME = r"[A-Z0-9#@$][A-Z0-9#@$]{0,7}"
_STATEMENT = re.compile(rf"^//(?P<name>{_NAME})?\s+(?P<op>\S+)\s*(?P<operands>.*)$", re.I)
_PGM = re.compile(rf"^PGM=(?P<name>{_NAME})\b", re.I)
_PROC = re.compile(rf"^(?:PROC=)?(?P<name>{_NAME})\s*(?:,|$)", re.I)
_IN_STREAM = re.compile(r"\bDD\s+(\*|DATA)", re.I)
_DELIMITER = re.compile(r"\bDLM=(?:'(?P<quoted>[^']{2})'|(?P<plain>[^\s,']{2}))", re.I)


@dataclass
class JclStep:
    name: str
    line: int
    end_line: int
    program: str | None = None
    procedure: str | None = None


@dataclass
class JclMember:
    """A job or procedure member: its JOB/PROC names and its steps."""

    jobs: list[str] = field(default_factory=list)
    procedures: list[str] = field(default_factory=list)
    steps: list[JclStep] = field(default_factory=list)
    in_stream: list[tuple[int, str]] = field(default_factory=list)


def read_jcl(text: str) -> JclMember:
    member = JclMember()
    lines = text.splitlines()
    # While in-stream: the prefix that ends the data ("/" for ``/*`` and ``//``).
    end_of_data: str | None = None
    for number, statement in _statements(lines):
        if end_of_data is not None:
            if not statement.startswith(end_of_data):
                member.in_stream.append((number, statement))
                continue
            if end_of_data != "/":
                end_of_data = None
                continue
        end_of_data = None
        m = _STATEMENT.match(statement)
        if m is None:
            continue
        name, op, operands = (
            (m.group("name") or "").upper(),
            m.group("op").upper(),
            m.group("operands"),
        )
        if op == "JOB" and name:
            member.jobs.append(name)
        elif op == "PROC" and name:
            member.procedures.append(name)
        elif op == "EXEC":
            if member.steps:
                member.steps[-1].end_line = number - 1
            step = JclStep(name or f"STEP{len(member.steps) + 1}", number, len(lines))
            pgm = _PGM.match(operands)
            proc = None if pgm else _PROC.match(operands)
            step.program = pgm.group("name").upper() if pgm else None
            step.procedure = proc.group("name").upper() if proc else None
            member.steps.append(step)
        elif op == "DD" and _IN_STREAM.search(f"DD {operands}"):
            delimiter = _DELIMITER.search(operands)
            end_of_data = (delimiter.group("quoted") or delimiter.group("plain")) if delimiter else "/"
    return member


def _statements(lines: list[str]) -> list[tuple[int, str]]:
    """``(line, statement)`` with continuation lines joined; comments dropped."""
    out: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, raw in enumerate(lines, 1):
        line = raw[:72].rstrip()
        if line.startswith("//*"):
            continue
        if pending is not None and line.startswith("//") and not line[2:3].strip():
            pending = (pending[0], pending[1] + line[2:].strip())
        else:
            if pending is not None:
                out.append(pending)
            pending = (number, line)
        if not pending[1].rstrip().endswith(","):
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


def parse_jcl_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """The JOB or PROC as a module symbol, each step as a function inside it."""
    text = source.decode("utf-8", errors="replace")
    member = read_jcl(text)
    line_count = text.count("\n") + 1
    path = file_info.path
    symbols = [
        _symbol(path, name, "module", f"{kind} {name}", 1, line_count)
        for kind, names in (("JOB", member.jobs), ("PROC", member.procedures))
        for name in names
    ]
    for step in member.steps:
        runs = f"PGM={step.program}" if step.program else step.procedure or ""
        symbols.append(
            _symbol(
                path, step.name, "function", f"{step.name} EXEC {runs}", step.line, step.end_line
            )
        )
    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
    )


def _symbol(path: str, name: str, kind: str, signature: str, start: int, end: int) -> Symbol:
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,  # type: ignore[arg-type]
        signature=signature,
        start_line=start,
        end_line=max(start, end),
        docstring=None,
        language="jcl",
    )
