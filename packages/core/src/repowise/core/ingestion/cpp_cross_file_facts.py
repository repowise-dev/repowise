"""What resolving another C/C++ file reads from this file's text, beyond its parse.

The call resolver reads a few facts straight from a C/C++ file's source while
it resolves *other* files: whether a member declaration is pure virtual
(``= 0``), the type parameters of a class template, the ``using``/``typedef``
aliases an includer follows, and how a member function's first line is written
(``friend``, a ``#define``, ``Owner::name``). The parse records none of them,
so an edit that changes only these still changes how other files resolve.
:func:`cpp_cross_file_facts` lists them as ``(name, fact)`` pairs, *name*
being the identifier another file spells to reach the fact, so an update can
compare them before and after an edit.
"""

from __future__ import annotations

import re
from typing import Any

from .call_receiver_typing._shared import _FUNCTION_KINDS, _TYPE_KINDS

_C_LANGUAGES = frozenset({"c", "cpp"})
_ALIAS_START = re.compile(r"\b(?:using\s+\w+\s*=|typedef\b)")
_USING_ALIAS = re.compile(r"\busing\s+(\w+)\s*=")
_WORD = re.compile(r"[A-Za-z_]\w*")
# Words of a typedef that name no alias.
_TYPEDEF_KEYWORDS = frozenset(
    {"typedef", "struct", "class", "union", "enum", "const", "volatile", "unsigned", "signed"}
)
# An alias statement spanning more lines than this is cut there.
_MAX_ALIAS_LINES = 8

Facts = frozenset[tuple[str, str]]


def cpp_cross_file_facts(parsed: Any, text: str) -> Facts | None:
    """The ``(name, fact)`` pairs other files' resolution reads from *text*.

    ``None`` for a file that is not C or C++.
    """
    if parsed.file_info.language not in _C_LANGUAGES:
        return None
    from .call_cpp_class_scope import template_parameters
    from .call_language_strategies import member_head_flags
    from .call_receiver_typing.cpp import _is_pure

    lines = text.splitlines()
    facts: set[tuple[str, str]] = set()
    for sym in parsed.symbols:
        if sym.kind in _TYPE_KINDS:
            params = sorted(template_parameters(lines, parsed.symbols, sym.id, sym.name))
            facts.add((sym.name, f"template {sym.id} {params}"))
        elif sym.parent_name and sym.kind in _FUNCTION_KINDS:
            if sym.is_declaration:
                facts.add((sym.name, f"pure {sym.id} {_is_pure(lines, sym)}"))
            facts.add((sym.name, f"head {sym.id} {member_head_flags(lines, sym)}"))
    facts.update(_alias_facts(lines, parsed.symbols))
    return frozenset(facts)


def _alias_facts(lines: list[str], symbols: Any) -> set[tuple[str, str]]:
    """One fact per alias statement outside a function body, under each name it may bind."""
    bodies = [
        (sym.start_line, sym.end_line)
        for sym in symbols
        if sym.kind in _FUNCTION_KINDS and not sym.is_declaration
    ]
    facts: set[tuple[str, str]] = set()
    for index, line in enumerate(lines):
        if not _ALIAS_START.search(line) or any(s < index + 1 <= e for s, e in bodies):
            continue
        statement = " ".join(lines[index : index + _MAX_ALIAS_LINES]).split(";", 1)[0]
        statement = " ".join(statement.split())
        using = _USING_ALIAS.search(statement)
        if using is not None:
            names = {using.group(1)}
        else:
            names = set(_WORD.findall(statement)) - _TYPEDEF_KEYWORDS
        facts.update((name, f"alias {statement}") for name in names)
    return facts


def changed_fact_names(before: Facts | None, after: Facts | None) -> set[str] | None:
    """The names whose facts differ, or ``None`` when either side is unknown."""
    if before is None or after is None:
        return None
    return {name for name, _fact in before ^ after}
