"""Copybooks parse as fragments inside a synthetic two-line program header.

The header overwrites two blank lines (comments are already blank) ahead of
the first code line, so every line keeps its number. A copybook without two
such lines parses unwrapped. ``FRAGMENT_PROGRAM`` names the synthetic program,
and the symbol extractor drops it.
"""

from __future__ import annotations

import re

from .statements import exec_blocks

FRAGMENT_PROGRAM = "REPOWISE-COPYBOOK"

_PROGRAM_HEADER = re.compile(r"\b(IDENTIFICATION|ID)\s+DIVISION\b|\bPROGRAM-ID\b", re.I)
_LEVEL_NUMBER = re.compile(r"\s*\d{1,2}\s")
_HEADER = "       IDENTIFICATION DIVISION. PROGRAM-ID. " + FRAGMENT_PROGRAM + "."
_DATA = "       DATA DIVISION. WORKING-STORAGE SECTION."
_PROCEDURE = "       PROCEDURE DIVISION."


def is_fragment(code: str) -> bool:
    """A member with no program header: a copybook, whatever its extension."""
    return code.strip() != "" and _PROGRAM_HEADER.search(code) is None


def is_procedure_fragment(code: str) -> bool:
    """Whether the first code line is procedure text rather than a data entry.

    EXEC blocks are skipped: a DCLGEN member opens with ``EXEC SQL DECLARE``
    ahead of its record.
    """
    for block in reversed(exec_blocks(code)):
        code = code[: block.start] + code[block.end :]
    # A code view has no sequence area to skip; a ``>>`` directive is not code.
    lines = (line.strip().strip(".").strip() for line in code.split("\n"))
    first = next((line for line in lines if line and not line.startswith(">>")), "")
    return _LEVEL_NUMBER.match(first) is None


def wrap_fragment(code: str) -> str:
    """*code* with the synthetic header written over its first two blank lines."""
    lines = code.split("\n")
    blanks = []
    for index, line in enumerate(lines):
        if line.strip():
            break
        blanks.append(index)
    if len(blanks) < 2:
        return code
    second = _PROCEDURE if is_procedure_fragment(code) else _DATA
    for index, text in zip(blanks[-2:], (_HEADER, second), strict=True):
        eol = "\r" if lines[index].endswith("\r") else ""
        lines[index] = text + eol
    return "\n".join(lines)
