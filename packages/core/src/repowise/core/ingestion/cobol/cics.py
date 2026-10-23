"""CICS ``LINK`` / ``XCTL`` program transfers, with in-program constant tracking.

``PROGRAM('X')`` names its target. ``PROGRAM(VAR)`` names every value the
program moves into ``VAR`` that is a literal or a constant (a data item with a
``VALUE`` literal that nothing reassigns). Any other assignment makes the
transfer dynamic, reported as such and never guessed.

Ceiling: only the program's own text is read, so a constant defined in a
copybook, or a value arriving through a group move (a COMMAREA), is dynamic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .statements import exec_blocks, mask_statements

# A transfer whose program is chosen at runtime imports this, so the graph
# shows it as an unresolved reference rather than a guessed program.
DYNAMIC_PREFIX = "dynamic:"

_NAME = r"[A-Z0-9#@$][A-Z0-9#@$-]*"
_LITERAL = r"'[^']*'|\"[^\"]*\""
_TRANSFER = re.compile(r"^\s*(LINK|XCTL)\b", re.IGNORECASE)
_PROGRAM = re.compile(rf"\bPROGRAM\s*\(\s*(?:(?P<lit>{_LITERAL})|(?P<var>{_NAME}))\s*\)", re.I)
_MOVE = re.compile(
    # The receivers are a lookahead, so a MOVE inside them is matched on its own.
    rf"(?<![\w-])MOVE\s+(?P<src>{_LITERAL}|{_NAME}(?:\s*\([^)]*\))?)\s+TO\s+(?=(?P<dst>[^.]*))",
    re.IGNORECASE,
)
_OTHER_WRITE = re.compile(rf"(?<![\w-])(?:INTO|ACCEPT|INITIALIZE|COMPUTE|SET)\s+({_NAME})", re.I)
_VALUE = re.compile(
    rf"^\s*\d\d\s+(?P<name>{_NAME})\b[^.]*?\bVALUE\s+(?:IS\s+)?(?P<lit>{_LITERAL})",
    re.IGNORECASE | re.MULTILINE,
)
_WORD = re.compile(rf"{_NAME}|\([^)]*\)|,|\S+", re.IGNORECASE)
# A MOVE's receiving list ends at the next statement's verb or a scope end.
_VERBS = frozenset(
    [
        "ACCEPT",
        "ADD",
        "CALL",
        "CLOSE",
        "COMPUTE",
        "CONTINUE",
        "DELETE",
        "DISPLAY",
        "DIVIDE",
        "ELSE",
        "EVALUATE",
        "EXEC",
        "EXIT",
        "GO",
        "GOBACK",
        "IF",
        "INITIALIZE",
        "INSPECT",
        "MOVE",
        "MULTIPLY",
        "NOT",
        "OPEN",
        "PERFORM",
        "READ",
        "RETURN",
        "REWRITE",
        "SEARCH",
        "SET",
        "START",
        "STOP",
        "STRING",
        "SUBTRACT",
        "UNSTRING",
        "WHEN",
        "WRITE",
    ]
)


@dataclass(frozen=True)
class ProgramTransfer:
    """One ``EXEC CICS LINK|XCTL``: its static targets, and the variable when dynamic."""

    verb: str
    line: int
    targets: tuple[str, ...]
    dynamic: str | None = None


def program_transfers(code: str) -> list[ProgramTransfer]:
    """Every LINK/XCTL in *code* (a code view), its targets resolved where static."""
    statements = mask_statements(code)
    moves = _moves(statements)
    rewritten = {name for _, dsts in moves for name in dsts}
    rewritten |= {m.group(1).upper() for m in _OTHER_WRITE.finditer(statements)}
    constants = {
        m.group("name").upper(): _unquote(m.group("lit"))
        for m in _VALUE.finditer(statements)
        if m.group("name").upper() not in rewritten
    }
    out: list[ProgramTransfer] = []
    for block in exec_blocks(code):
        verb = _TRANSFER.match(block.body) if block.kind == "CICS" else None
        program = _PROGRAM.search(block.body) if verb else None
        if program is None:
            continue
        if program.group("lit"):
            out.append(
                ProgramTransfer(
                    verb.group(1).upper(), block.line, (_unquote(program.group("lit")),)
                )
            )
            continue
        var = program.group("var").upper()
        targets, dynamic = _values_of(var, moves, constants)
        out.append(
            ProgramTransfer(verb.group(1).upper(), block.line, targets, var if dynamic else None)
        )
    return out


def _values_of(
    var: str, moves: list[tuple[str, set[str]]], constants: dict[str, str]
) -> tuple[tuple[str, ...], bool]:
    """``(static values, any non-static assignment)`` for *var*."""
    values: list[str] = []
    dynamic = False
    sources = [src for src, dsts in moves if var in dsts]
    if not sources:
        if var in constants:
            return (constants[var],), False
        return (), True
    for src in sources:
        if src[0] in "'\"":
            value = _unquote(src)
        elif src in constants:
            value = constants[src]
        else:
            dynamic = True
            continue
        if value and value not in values:
            values.append(value)
    return tuple(values), dynamic


def _moves(code: str) -> list[tuple[str, set[str]]]:
    """``(source, {receivers})`` for each MOVE; a literal source keeps its quotes."""
    out = []
    for m in _MOVE.finditer(code):
        src = m.group("src")
        src = src if src[0] in "'\"" else re.sub(r"\s*\(.*", "", src).upper()
        receivers: set[str] = set()
        qualifier = False
        for token in _WORD.findall(m.group("dst")):
            word = token.upper()
            if word in _VERBS or word.startswith("END-"):
                break
            if word in {"OF", "IN"}:
                qualifier = True
            elif qualifier:
                qualifier = False
            elif re.fullmatch(_NAME, word, re.IGNORECASE):
                receivers.add(word)
        out.append((src, receivers))
    return out


def _unquote(literal: str) -> str:
    return literal[1:-1].strip().upper()
