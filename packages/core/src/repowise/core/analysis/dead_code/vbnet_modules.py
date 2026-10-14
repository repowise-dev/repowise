"""VB.NET ``Module``s: containers a use of their members never names.

A Module's members are in scope unqualified wherever its namespace is visible
(an ``Imports`` line, the project's root namespace, a project-level import),
so call sites write ``ShowProgramInfo()`` and never ``MainMod.ShowProgramInfo()``.
No importer names the Module, so the absence of one says nothing about it.

The graph emits a Module as ``kind="class"`` (``module_block`` in
``ingestion/language_configs.py``), so the declaration header is read from the
source already in memory to tell it from a Class.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Lines read from a symbol's start to find its header: its span starts at a
#: leading attribute list, which may wrap over a few lines.
_HEADER_LOOKAHEAD = 6

#: A comment line that mentions ``Module X`` is not a header.
_COMMENT_RE = re.compile(rb"\s*(?:'|REM\b)", re.IGNORECASE)

_KEYWORD_RE = re.compile(rb"\b(Module|Class|Structure|Interface|Enum)\s+(\w+)", re.IGNORECASE)


def _is_module(lines: list[bytes] | None, sym: dict) -> bool:
    """Whether *sym*'s header declares a ``Module``; True when it cannot be read.

    Unreadable counts as a Module on purpose: the caller only lowers a
    confidence for it, so the cost is a Class kept off the deletion-ready tier.
    """
    start = sym.get("start_line")
    if not lines or not start:
        return True
    name = sym.get("name", "").lower().encode("utf-8", "ignore")
    for line in lines[start - 1 : start - 1 + _HEADER_LOOKAHEAD]:
        if _COMMENT_RE.match(line):
            continue
        for match in _KEYWORD_RE.finditer(line):
            if match.group(2).lower() == name:
                return match.group(1).lower() == b"module"
    return True


def vbnet_module_names(symbols: Iterable[dict], source: bytes | None) -> frozenset[str]:
    """Names of the VB.NET Modules among *symbols* of one file.

    A Module may sit inside a ``Namespace`` block, which is its parent here.
    """
    candidates = [
        sym for sym in symbols if sym.get("language") == "vbnet" and sym.get("kind") == "class"
    ]
    if not candidates:
        return frozenset()
    lines = source.split(b"\n") if source else None
    return frozenset(sym.get("name", "") for sym in candidates if _is_module(lines, sym))
