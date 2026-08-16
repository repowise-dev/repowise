"""What type is the local a call is made on.

``user.save()`` names no type, so member resolution has nothing to look up.
The declaration that gives ``user`` its type is almost always inside the same
function — a parameter, a local, a catch or loop binding — and the languages
here write it as ``T name``.

The scan is allowed to be wrong. Nothing it returns becomes an edge until the
resolver has checked that the type actually declares the method, so a
mis-inference costs a missing edge and never a wrong one. That check is what
licenses matching declarations with a regex instead of a type checker.

Split in two on purpose. ``scan_declarations`` reads a whole file once and is
the expensive half; ``types_in_span`` narrows the result to one function body
and is nearly free. Scanning per body instead would re-read every line that
two spans share, and a class body contains all of its methods'.

Adding a language means adding its shapes to ``_LANGUAGE_PATTERNS`` and
nothing else; a language absent from it is excluded by construction.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from typing import NamedTuple

from ..language_data import get_builtin_types
from ..type_names import bare_type_name, is_resolvable_type_name, strip_type_arguments

# A type as written before a declared name: optionally qualified, optionally
# generic to two levels of nesting, optionally an array. A third level yields
# no match, which is a deliberate ceiling — a deeper group needs a real bracket
# matcher, and failing to type a name costs an edge, never a wrong one.
_TYPE = r"[A-Z]\w*(?:\.\w+)*(?:<(?:[^<>]|<[^<>]*>)*>)?(?:\[\])*"

# ``T name`` closed by the punctuation that can end a declaration: an
# initialiser, a statement end, the next parameter, the end of a parameter
# list, or an enhanced-for colon. Requiring one of those is what keeps the
# pattern off ``(Foo) bar`` and ``foo(Bar.BAZ, qux)``, which have no space in
# the same place.
_TYPED_DECLARATION = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE})\s+(?P<name>[a-z_]\w*)\s*(?=[=;,):])"
)

# ``var name = new T(...)``, where the type is on the right of the binding.
_INFERRED_FROM_NEW = re.compile(
    r"(?<![\w.])var\s+(?P<name>[a-z_]\w*)\s*=\s*new\s+(?P<type>[A-Z]\w*(?:\.\w+)*)"
)

# Truncating at ``//`` also truncates a URL inside a string literal. That can
# only ever lose a declaration, never invent one, which is the safe direction.
_LINE_COMMENT = re.compile(r"//.*")

_NEWLINE = re.compile(r"\n")

_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "csharp": _C_FAMILY,
    "java": _C_FAMILY,
}

RECEIVER_TYPE_LANGUAGES = frozenset(_LANGUAGE_PATTERNS)


class Declaration(NamedTuple):
    """One name given one type, at one line."""

    line: int
    name: str
    type_name: str


def _nests_in_a_builtin(raw: str, language: str) -> bool:
    """True for ``Map.Entry`` and its kind — a member type of a builtin.

    Taking the bare name of one of these answers ``Entry``, which the repo may
    well declare somewhere and which the declaration never meant. Discarding a
    qualifier is right when the qualifier is a package; it is wrong when the
    qualifier is a type, and a builtin head is the case we can tell apart.
    """
    if "." not in raw:
        return False
    head, separator, _ = strip_type_arguments(raw).partition(".")
    return bool(separator) and head in get_builtin_types(language)


def _usable_type_name(raw: str, language: str) -> str | None:
    """The bare name *raw* denotes, or None if it can name no repo symbol."""
    if _nests_in_a_builtin(raw, language):
        return None
    name = raw if raw.isidentifier() else bare_type_name(raw)
    return name if is_resolvable_type_name(name, language) else None


def scan_declarations(text: str, language: str) -> tuple[Declaration, ...]:
    """Every declaration *text* makes, in line order."""
    patterns = _LANGUAGE_PATTERNS.get(language)
    if not patterns:
        return ()

    cleaned = _LINE_COMMENT.sub("", text)
    # Scanned by the regex engine rather than a Python loop over characters:
    # the loop costs more than the declaration scan it exists to serve.
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]

    # One file writes the same type name hundreds of times, and normalising it
    # walks the string character by character. Resolve each spelling once.
    resolved: dict[str, str | None] = {}
    found: list[Declaration] = []
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            raw = match.group("type")
            if raw not in resolved:
                resolved[raw] = _usable_type_name(raw, language)
            type_name = resolved[raw]
            if type_name is None:
                continue
            found.append(
                Declaration(bisect_right(starts, match.start()), match.group("name"), type_name)
            )

    found.sort()
    return tuple(found)


def types_in_span(
    declarations: tuple[Declaration, ...],
    start_line: int,
    end_line: int,
) -> dict[str, str]:
    """``{name: type}`` for the declarations inside one function body."""
    types: dict[str, str] = {}
    ambiguous: set[str] = set()

    # Bisected rather than skipped over: a file's bodies each ask once, so
    # walking from the front every time is quadratic in a large file, and that
    # — not the regex — was what the scan actually cost.
    first = bisect_left(declarations, start_line, key=lambda d: d.line)
    for declaration in declarations[first:]:
        if declaration.line > end_line:
            break
        if declaration.name in ambiguous:
            continue
        previous = types.get(declaration.name)
        if previous is None:
            types[declaration.name] = declaration.type_name
        elif previous != declaration.type_name:
            # One name declared twice with two types — a shadowing inner
            # scope, or a line the scan misread. Neither has an answer.
            del types[declaration.name]
            ambiguous.add(declaration.name)

    return types
