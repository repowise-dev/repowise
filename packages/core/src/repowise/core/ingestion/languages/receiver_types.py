"""What type is the receiver a call is made on.

``user.save()`` names no type, so member resolution has nothing to look up.
The declaration that gives ``user`` its type is either inside the same function
— a parameter, a local, a catch or loop binding — or, when the receiver is a
field, at the enclosing class's own scope. Both are written ``T name``, so one
scan finds both and only the span they are read back over differs.

The scan is allowed to be wrong. Nothing it returns becomes an edge until the
resolver has checked that the type actually declares the method, so a
mis-inference costs a missing edge and never a wrong one. That check is what
licenses matching declarations with a regex instead of a type checker.

Split in two on purpose. ``scan_declarations`` reads a whole file once and is
the expensive half; ``types_in_span`` and ``types_by_class`` narrow the result
and are nearly free. Scanning per body instead would re-read every line that
two spans share, and a class body contains all of its methods'.

Adding a language means adding its shapes to ``_LANGUAGE_PATTERNS`` and
nothing else; a language absent from it is excluded by construction.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
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
# The closer is captured because it is the only thing separating a field from
# a parameter at class scope — `T name;` against `T name,`. Some constructors
# and static methods are extracted as no symbol at all, so their parameter
# lists sit at class scope with nothing else to tell them apart.
_TYPED_DECLARATION = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE})\s+(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,):]))"
)

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
    """One name given one type, at one line.

    ``closer`` is the punctuation that ended the declaration, or empty where
    the shape has none. Only class scope reads it.
    """

    line: int
    name: str
    type_name: str
    closer: str = ""


# What can end a field. `var` has no place here at all: it is a local-only
# shape in both languages, so it carries no closer and class scope drops it.
_FIELD_CLOSERS = frozenset({";", "="})


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
                Declaration(
                    bisect_right(starts, match.start()),
                    match.group("name"),
                    type_name,
                    match.groupdict().get("closer") or "",
                )
            )

    found.sort()
    return tuple(found)


def _record(types: dict[str, str | None], declaration: Declaration) -> None:
    """Add one declaration to a scope, or mark the name unanswerable.

    A name declared twice with two types maps to ``None`` rather than being
    dropped. A caller has to tell "this scope says nothing about the name"
    from "this scope says something unusable about it", because only the first
    of those may fall through to a wider scope.
    """
    if declaration.name not in types:
        types[declaration.name] = declaration.type_name
    elif types[declaration.name] != declaration.type_name:
        types[declaration.name] = None


def types_in_span(
    declarations: tuple[Declaration, ...],
    start_line: int,
    end_line: int,
) -> dict[str, str | None]:
    """``{name: type}`` for the declarations inside one function body."""
    types: dict[str, str | None] = {}

    # Bisected rather than skipped over: a file's bodies each ask once, so
    # walking from the front every time is quadratic in a large file, and that
    # — not the regex — was what the scan actually cost.
    first = bisect_left(declarations, start_line, key=lambda d: d.line)
    for declaration in declarations[first:]:
        if declaration.line > end_line:
            break
        _record(types, declaration)

    return types


def _merged(spans: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """The spans as non-overlapping, ascending intervals."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def types_by_class(
    declarations: tuple[Declaration, ...],
    class_spans: Mapping[str, tuple[int, int]],
    function_spans: Iterable[tuple[int, int]],
) -> dict[str, dict[str, str | None]]:
    """``{class_id: {name: type}}`` for the fields each class declares.

    A class span contains every method body inside it, so a declaration is a
    field only if it lies inside the class and inside none of the file's
    functions. Nested classes go to the innermost class containing them, so an
    inner class's fields never answer for the outer one.
    """
    if not class_spans:
        return {}

    bodies = _merged(function_spans)
    body_starts = [start for start, _ in bodies]
    # Innermost first, so the first containing span is the owner.
    ordered = sorted(class_spans.items(), key=lambda item: item[1][1] - item[1][0])

    by_class: dict[str, dict[str, str | None]] = {}
    for declaration in declarations:
        if declaration.closer not in _FIELD_CLOSERS:
            continue
        index = bisect_right(body_starts, declaration.line) - 1
        if index >= 0 and declaration.line <= bodies[index][1]:
            continue
        for class_id, (start, end) in ordered:
            if start <= declaration.line <= end:
                _record(by_class.setdefault(class_id, {}), declaration)
                break

    return by_class
