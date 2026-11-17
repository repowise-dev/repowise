"""CICS ``LINK`` / ``XCTL`` programs, ``SEND`` / ``RECEIVE MAP`` mapsets and
``RETURN`` / ``START`` transactions, with constant tracking.

``PROGRAM('X')`` names its target. ``PROGRAM(VAR)`` names every value the
program moves into ``VAR`` that is a literal or a constant (a data item with a
``VALUE`` literal that nothing reassigns). Any other assignment makes the
target dynamic, reported as such and never guessed. ``MAPSET`` and ``TRANSID``
read the same way.

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
_OPERAND = rf"\s*\(\s*(?:(?P<lit>{_LITERAL})|(?P<var>{_NAME}))\s*\)"
_PROGRAM = re.compile(r"\bPROGRAM" + _OPERAND, re.IGNORECASE)
_MAP_COMMAND = re.compile(r"^\s*(SEND|RECEIVE)\s+MAP\b", re.IGNORECASE)
_MAPSET = re.compile(r"\bMAPSET" + _OPERAND, re.IGNORECASE)
_MAP = re.compile(r"\bMAP" + _OPERAND, re.IGNORECASE)
# ``STARTBR`` is a browse, not a START.
_TRANSID_COMMAND = re.compile(r"^\s*(RETURN|START)\b", re.IGNORECASE)
_TRANSID = re.compile(r"\bTRANSID" + _OPERAND, re.IGNORECASE)
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
# A paragraph or section header in area A, alone on its line.
_PARAGRAPH = re.compile(rf"^ {{7,10}}{_NAME}(?:\s+SECTION)?\s*\.\s*$", re.IGNORECASE | re.MULTILINE)
_ARM = re.compile(r"(?<![\w-])(?:WHEN|ELSE)(?![\w-])", re.IGNORECASE)
_SCOPE_END = re.compile(r"(?<![\w-])END-(?:IF|EVALUATE)(?![\w-])", re.IGNORECASE)
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
class CicsTarget:
    """One CICS command's named program or mapset: static values, and the variable when dynamic."""

    verb: str
    line: int
    targets: tuple[str, ...]
    dynamic: str | None = None


def program_transfers(code: str) -> list[CicsTarget]:
    """Every LINK/XCTL in *code* (a code view), its program resolved where static."""
    return _cics_targets(code, _TRANSFER, _PROGRAM)


def mapset_uses(code: str) -> list[CicsTarget]:
    """Every SEND/RECEIVE MAP in *code*, by its MAPSET (CICS defaults it to the MAP)."""
    return _cics_targets(code, _MAP_COMMAND, _MAPSET, fallback=_MAP)


def transid_uses(code: str) -> list[CicsTarget]:
    """Every RETURN/START naming a TRANSID in *code*: the transaction the region runs next."""
    return _cics_targets(code, _TRANSID_COMMAND, _TRANSID)


def _cics_targets(
    code: str,
    command: re.Pattern[str],
    option: re.Pattern[str],
    fallback: re.Pattern[str] | None = None,
) -> list[CicsTarget]:
    statements = mask_statements(code)
    moves = _moves(statements)
    other_writes = {m.group(1).upper() for m in _OTHER_WRITE.finditer(statements)}
    rewritten = {name for _, _, dsts in moves for name in dsts} | other_writes
    constants = {
        m.group("name").upper(): _unquote(m.group("lit"))
        for m in _VALUE.finditer(statements)
        if m.group("name").upper() not in rewritten
    }
    paragraphs = [m.start() for m in _PARAGRAPH.finditer(statements)]
    arms = sorted(paragraphs + [m.start() for m in _ARM.finditer(statements)])
    out: list[CicsTarget] = []
    for block in exec_blocks(code):
        verb = command.match(block.body) if block.kind == "CICS" else None
        operand = option.search(block.body) if verb else None
        if verb and operand is None and fallback is not None:
            operand = fallback.search(block.body)
        if operand is None:
            continue
        name = verb.group(1).upper()
        if operand.group("lit"):
            out.append(CicsTarget(name, block.line, (_unquote(operand.group("lit")),)))
            continue
        var = operand.group("var").upper()
        # The nearest scope (WHEN/ELSE arm, then paragraph) whose last assignment
        # is unconditional decides; otherwise every assignment in the program does.
        scoped = moves
        for bounds in (arms, paragraphs):
            start = max((p for p in bounds if p < block.start), default=0)
            local = [m for m in moves if start < m[0] < block.start and var in m[2]]
            if local and not _SCOPE_END.search(statements, local[-1][0], block.start):
                scoped = local
                break
        targets, dynamic = _values_of(var, scoped, constants)
        dynamic = dynamic or var in other_writes
        out.append(CicsTarget(name, block.line, targets, var if dynamic else None))
    return out


def _values_of(
    var: str, moves: list[tuple[int, str, set[str]]], constants: dict[str, str]
) -> tuple[tuple[str, ...], bool]:
    """``(static values, any non-static assignment)`` for *var* across *moves*."""
    values: list[str] = []
    dynamic = False
    sources = [src for _, src, dsts in moves if var in dsts]
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


def _moves(code: str) -> list[tuple[int, str, set[str]]]:
    """``(offset, source, {receivers})`` per MOVE; a literal source keeps its quotes."""
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
        out.append((m.start(), src, receivers))
    return out


def _unquote(literal: str) -> str:
    return literal[1:-1].strip().upper()
