"""What type is the receiver a call is made on.

``user.save()`` names no type, so member resolution has nothing to look up.
The declaration that gives ``user`` its type is either inside the same function
— a parameter, a local, a catch or loop binding — or, when the receiver is a
field, at the enclosing class's own scope. In the C family both are written
``T name``, so one scan finds both and only the span they are read back over
differs. A language may order them the other way round: Go writes ``name T``
and declares a method's receiver in the signature, which the body span already
covers.

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
_HASH_COMMENT = re.compile(r"#.*")

# Python writes its documentation as a string literal in the body, which a
# comment strip does not reach. Prose is the one false-positive source the
# C-family shape never had — ``context: The caller`` reads as an annotation —
# so triple-quoted runs are blanked, keeping their newlines so line numbers
# survive.
_DOCSTRING = re.compile(r"(\"\"\"|''')(?:.|\n)*?\1")

_NEWLINE = re.compile(r"\n")

# Python annotates after the name, not before it: ``x: T``, ``def f(x: T)``.
# The closer is what keeps the pattern off prose, and it is the reason a
# generic annotation is refused rather than mis-read — ``x: Optional[T]`` is
# followed by ``[``, so it matches nothing, which is right, since the value is
# an Optional and not a T.
_PY_TYPE = r"[A-Z]\w*(?:\.\w+)*"
_PY_ANNOTATED = re.compile(
    rf"(?<![\w.])(?P<name>[a-z_]\w*)\s*:\s*(?P<type>{_PY_TYPE})\s*(?=(?P<closer>[=,)\]\n]))"
)

# ``x = T(...)``, the only shape unannotated Python offers. Bare-named on
# purpose: ``x = Foo.bar(...)`` is a call on a class rather than a
# construction, and typing ``x`` as ``Foo`` from it would simply be wrong.
# Anchored to the start of a statement because ``dispatch(logger=Emitter())``
# is otherwise read as declaring ``logger``, which then answers for a
# ``logger`` that came from somewhere else entirely. The C family is safe from
# that shape only because its equivalent needs the ``var`` keyword.
_PY_CONSTRUCTED = re.compile(
    r"(?m)^[ \t]*(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*\("
)

# Go writes the name before the type, so none of the C-family shapes above
# match a line of it. Two further differences decide these patterns.
#
# A type name may be lowercase, because that is how Go spells an unexported
# one, and a private method hangs off exactly those. Admitting lowercase is
# only safe because the language spec carries every predeclared identifier in
# ``builtin_types``, so ``string`` and ``error`` are refused downstream rather
# than looked up.
#
# The receiver a method is declared on — ``func (s *Server) handle()`` — is
# written in the signature rather than the body, and it is the largest shape
# by some way: 50.2% of the reachable population over five Go repos, against
# 24.1% for parameters and 21.5% for composite literals. It needs no scope of
# its own, because a function symbol's span starts at its ``func`` line, so
# the body scan already reads it.
_GO_NAME = r"[a-z_]\w*"
_GO_TYPE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?"

# A parameter, a named return, or a method's own receiver: ``name Type``
# juxtaposed before a comma or the end of the list. Juxtaposition is a
# declaration nearly everywhere it is legal in Go, which is why this needs
# less guarding than the C family's did.
#
# ``func (s *Server) handle()`` needs no pattern of its own — the receiver
# group presents as ``s *Server)`` and is matched here. A separate anchored
# pattern for it was measured and removed: it added a whole-file scan and
# changed no edge on any of the five Go repos.
#
# ``a * b`` is not matched because gofmt spaces a binary operator on both
# sides while a pointer type binds tight, and Go source is gofmt'd.
_GO_PARAM = re.compile(
    rf"(?<![\w.])(?P<name>{_GO_NAME})\s+\*?(?P<type>{_GO_TYPE})\s*(?=[,)])"
)

# ``x := Foo{}``, ``x := &Foo{}``, ``x := y.(Foo)`` and ``x, ok := y.(Foo)``.
# One scan rather than two: both shapes are a short declaration whose type is
# written outright, and they differ only in what brackets it. The closing
# ``{`` or ``)`` is what tells them from ``x := f()``, whose type is a return
# value this phase deliberately does not chase.
#
# ``x := []Foo{}`` and ``x := map[k]Foo{}`` match nothing on purpose: the
# value is a slice or a map, and typing ``x`` as ``Foo`` would be wrong rather
# than merely unhelpful.
_GO_SHORT_DECL = re.compile(
    rf"(?<![\w.])(?P<name>{_GO_NAME})\s*(?:,\s*{_GO_NAME}\s*)?:="
    rf"\s*(?:&|[\w.]+\.\(\*?)?(?P<type>{_GO_TYPE})\s*(?:\{{|\))"
)

# ``var x Foo`` / ``var x *Foo``. The smallest shape in every Go repo
# measured — 2.5% of the reachable population — and kept only because it is
# the one shape neither pattern above reaches.
_GO_VAR_DECL = re.compile(rf"(?<![\w.])var\s+(?P<name>{_GO_NAME})\s+\*?(?P<type>{_GO_TYPE})")

_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)
# No Go shape captures a closer, so every Go declaration carries ``""`` and
# class scope would drop all of them. That is the intended reading: Go is not
# in IMPLICIT_FIELD_LANGUAGES and must not be.
_GO_FAMILY = (_GO_PARAM, _GO_SHORT_DECL, _GO_VAR_DECL)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "csharp": _C_FAMILY,
    "go": _GO_FAMILY,
    "java": _C_FAMILY,
    "python": (_PY_ANNOTATED, _PY_CONSTRUCTED),
}

RECEIVER_TYPE_LANGUAGES = frozenset(_LANGUAGE_PATTERNS)

# Languages where a field can be named with no qualifier, which is the only
# thing that lets a class-scope declaration answer for a bare receiver. Python
# writes ``self.foo.bar()``, whose receiver is dotted and which our grammar
# queries mint no call site for at all — so class scope has nothing there to
# answer, and consulting it could only bind a bare local name to a field.
IMPLICIT_FIELD_LANGUAGES = frozenset({"csharp", "java"})

_LANGUAGE_COMMENTS: dict[str, re.Pattern[str]] = {
    "csharp": _LINE_COMMENT,
    "go": _LINE_COMMENT,
    "java": _LINE_COMMENT,
    "python": _HASH_COMMENT,
}


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

    cleaned = text
    if language == "python":
        cleaned = _DOCSTRING.sub(lambda m: "\n" * m.group(0).count("\n"), cleaned)
    comment = _LANGUAGE_COMMENTS.get(language)
    if comment is not None:
        cleaned = comment.sub("", cleaned)
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
