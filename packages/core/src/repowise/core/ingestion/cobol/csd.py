"""CICS CSD resource definitions (DFHCSDUP input): the programs and transactions a region runs.

Read from a ``.csd`` file or from a JCL step's in-stream data. A command starts
on a line whose first word stands alone (not ``WORD(``) and runs until the
next such line; a ``*`` in column 1 is a comment. Commands, resource types and
the ``PROGRAM`` attribute may be abbreviated as DFHCSDUP allows (``DEF
TRANS(T) PROG(P)``). ``DEFINE TRANSACTION(T) ... PROGRAM(P)`` names the
program transaction T runs; an ``ALTER`` naming another program adds it, so a
transaction two programs can run links to neither. ``DELETE`` is ignored, which
only keeps a deleted program a root.

Ceiling: a line opening with a bare word ends the command, so an attribute
value wrapped onto a new line drops the rest of that command (a missed link,
never a wrong one).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from ..models import FileInfo, ParsedFile, Symbol

# A line opening with a bare word starts a command; ``DELETE(YES)`` is an attribute.
_COMMAND = re.compile(r"^\s*([A-Z]+)(?=\s|$)", re.IGNORECASE)
_RESOURCE = re.compile(
    r"^\s*(?P<command>[A-Z]+)\s+(?P<resource>[A-Z]+)\s*\(\s*(?P<name>[^()\s]+)\s*\)(?P<rest>.*)",
    re.IGNORECASE | re.DOTALL,
)
_PROGRAM_ATTR = re.compile(r"(?<![\w-])PROG[A-Z]*\s*\(\s*(?P<name>[^()\s]+)\s*\)", re.IGNORECASE)


def _abbreviates(word: str, full: str, shortest: int) -> bool:
    """Whether *word* is *full* or a DFHCSDUP abbreviation of it."""
    word = word.upper()
    return len(word) >= shortest and full.startswith(word)


def _command(word: str) -> str | None:
    if _abbreviates(word, "DEFINE", 3) or _abbreviates(word, "USERDEFINE", 5):
        return "DEFINE"
    return "ALTER" if _abbreviates(word, "ALTER", 3) else None


def _resource(word: str) -> str:
    if _abbreviates(word, "TRANSACTION", 5):
        return "TRANSACTION"
    if _abbreviates(word, "PROGRAM", 4):
        return "PROGRAM"
    return word.upper()


@dataclass(frozen=True)
class CsdDefinition:
    """One ``DEFINE`` or ``ALTER``: its resource type and name, and a transaction's program."""

    resource: str
    name: str
    line: int
    program: str | None = None
    command: str = "DEFINE"


def read_csd(lines: Iterable[tuple[int, str]]) -> list[CsdDefinition]:
    """Every ``DEFINE`` and ``ALTER`` in *lines* (``(line number, text)``), continuations joined."""
    commands: list[tuple[int, str]] = []
    for number, line in lines:
        if line.startswith("*") or not line.strip():
            continue
        if _COMMAND.match(line) or not commands:
            commands.append((number, line))
        else:
            commands[-1] = (commands[-1][0], f"{commands[-1][1]} {line}")
    out: list[CsdDefinition] = []
    for number, text in commands:
        m = _RESOURCE.match(text)
        command = _command(m.group("command")) if m else None
        if m is None or command is None:
            continue
        resource = _resource(m.group("resource"))
        program = _PROGRAM_ATTR.search(m.group("rest")) if resource == "TRANSACTION" else None
        out.append(
            CsdDefinition(
                resource,
                m.group("name").upper(),
                number,
                program.group("name").upper() if program else None,
                command,
            )
        )
    return out


def defined_programs(definitions: list[CsdDefinition]) -> list[str]:
    """The programs *definitions* make runnable: each PROGRAM, and each transaction's program."""
    names = {d.name for d in definitions if d.resource == "PROGRAM"}
    names |= {d.program for d in definitions if d.program}
    return sorted(names)


def parse_csd_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """Each defined resource as a symbol: ``TRANSACTION CAUP PROGRAM(COACTUPC)``."""
    lines = source.decode("utf-8", errors="replace").splitlines()
    definitions = [d for d in read_csd(enumerate(lines, 1)) if d.command == "DEFINE"]
    symbols = [
        Symbol(
            id=f"{file_info.path}::{d.resource}:{d.name}",
            name=d.name,
            qualified_name=f"{d.resource}:{d.name}",
            kind="variable",
            signature=f"{d.resource} {d.name}" + (f" PROGRAM({d.program})" if d.program else ""),
            start_line=d.line,
            end_line=next_line - 1,
            docstring=None,
            language="csd",
        )
        for d, next_line in zip(
            definitions,
            [d.line for d in definitions[1:]] + [len(lines) + 1],
            strict=True,
        )
    ]
    return ParsedFile(
        file_info=file_info,
        symbols=symbols,
        imports=[],
        exports=[s.name for s in symbols],
        docstring=None,
        parse_errors=[],
    )
