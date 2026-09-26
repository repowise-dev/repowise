"""What type is the receiver a call is made on.

``user.save()`` names no type, so member resolution has nothing to look up.
The declaration typing ``user`` is inside the same function (parameter, local,
catch or loop binding) or, for a field, at the enclosing class's scope; one
scan finds both and only the span they are read back over differs.

The scan is allowed to be wrong. Nothing it returns becomes an edge until the
resolver has checked that the type declares the method, so a mis-inference
costs a missing edge, never a wrong one. That check is what licenses a regex
instead of a type checker.

``scan_declarations`` reads a whole file once (the expensive half);
``types_in_span`` and ``types_by_class`` narrow it nearly for free, where a
per-body scan would re-read every line two spans share.

Adding a language means adding its shapes to ``_LANGUAGE_PATTERNS``.
``_FRAMEWORK_DECORATOR_TYPES`` covers a decorator that changes what a symbol
is; ``_PY_BINDINGS`` says only that a name is taken, a refusal, not a type.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from typing import NamedTuple

from ..language_data import get_builtin_types
from ..type_names import (
    bare_type_name,
    is_resolvable_type_name,
    strip_type_arguments,
    unwrap_pointer_like,
)

# A type as written before a declared name: optionally qualified, generic to two
# levels, an array. A third level yields no match: a deliberate ceiling, since a
# deeper group needs a real bracket matcher.
_TYPE = r"[A-Z]\w*(?:\.\w+)*(?:<(?:[^<>]|<[^<>]*>)*>)?(?:\[\])*"

# ``T name`` closed by punctuation that can end a declaration, which keeps it
# off ``(Foo) bar`` and ``foo(Bar.BAZ, qux)``. The closer is captured because it
# alone separates a field (`T name;`) from a parameter (`T name,`) at class
# scope, where unextracted constructors leave their parameter lists.
_TYPED_DECLARATION = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE})\s+(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,):]))"
)

_INFERRED_FROM_NEW = re.compile(
    r"(?<![\w.])var\s+(?P<name>[a-z_]\w*)\s*=\s*new\s+(?P<type>[A-Z]\w*(?:\.\w+)*)"
)

# Truncating at ``//`` also cuts a URL in a string: it can only lose a
# declaration, never invent one.
_LINE_COMMENT = re.compile(r"//.*")
_HASH_COMMENT = re.compile(r"#.*")

# Docstring prose reads as annotations (``context: The caller``), so triple-quoted
# runs are blanked, keeping newlines so line numbers survive.
_DOCSTRING = re.compile(r"(\"\"\"|''')(?:.|\n)*?\1")

# A block comment, blanked to its newlines. Needed where doc-comment prose
# matches the language's own shape: KDoc's `@param connection: Store` is
# Kotlin's `name: Type`, while javadoc's is not Java's `Type name`.
# Run before the line strip, or `//` would eat the `*/` of `/* see http://x */`.
_BLOCK_COMMENT = re.compile(r"/\*(?:.|\n)*?\*/")

_NEWLINE = re.compile(r"\n")

# Python annotates after the name: ``x: T``. The closer keeps the pattern off
# prose and refuses a generic: ``x: Optional[T]`` is an Optional, not a T.
_PY_TYPE = r"[A-Z]\w*(?:\.\w+)*"
_PY_ANNOTATED = re.compile(
    rf"(?<![\w.])(?P<name>[a-z_]\w*)\s*:\s*(?P<type>{_PY_TYPE})\s*(?=(?P<closer>[=,)\]\n]))"
)

# ``x = T(...)``. Bare-named: ``x = Foo.bar(...)`` is a call on a class, not a
# construction. Anchored to a statement start, or ``dispatch(logger=Emitter())``
# would read as declaring ``logger``.
_PY_CONSTRUCTED = re.compile(
    r"(?m)^[ \t]*(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*\("
)

# Go writes the name before the type. A type may be lowercase (unexported);
# that is safe only because ``builtin_types`` carries every predeclared
# identifier, so ``string`` and ``error`` are refused downstream. A method's
# receiver sits in the signature, which the function span already covers.
_GO_NAME = r"[a-z_]\w*"
_GO_TYPE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?"

# A parameter, named return, or method receiver (``s *Server)``): ``name Type``
# before a comma or the list end. Juxtaposition is a declaration nearly
# everywhere Go allows it. ``a * b`` is not matched because gofmt spaces binary
# operators while a pointer type binds tight.
_GO_PARAM = re.compile(
    rf"(?<![\w.])(?P<name>{_GO_NAME})\s+\*?(?P<type>{_GO_TYPE})\s*(?=[,)])"
)

# ``x := Foo{}``, ``x := &Foo{}``, ``x := y.(Foo)``, ``x, ok := y.(Foo)``. The
# closing ``{`` or ``)`` tells them from ``x := f()``, whose return type is not
# chased. ``[]Foo{}`` and ``map[k]Foo{}`` match nothing: the value is not a Foo.
_GO_SHORT_DECL = re.compile(
    rf"(?<![\w.])(?P<name>{_GO_NAME})\s*(?:,\s*{_GO_NAME}\s*)?:="
    rf"\s*(?:&|[\w.]+\.\(\*?)?(?P<type>{_GO_TYPE})\s*(?:\{{|\))"
)

# ``var x Foo`` / ``var x *Foo``: rare, but no pattern above reaches it.
_GO_VAR_DECL = re.compile(rf"(?<![\w.])var\s+(?P<name>{_GO_NAME})\s+\*?(?P<type>{_GO_TYPE})")

# Kotlin annotates after the name, and `val x: Foo`, `var x: Foo`, parameters
# and `class A(val x: Foo)` are all `name: Type`.
#
# A generic bares to its head, which is right here (unlike Python's
# `Optional[T]`): `List<Foo>` bares to a builtin refused downstream, `Column<T>`
# to the value's real type. A trailing `?` is consumed: `Foo?` is still a `Foo`.
#
# `by` closes a delegated property (`val x: Foo by lazy {}`), but never as a
# field closer, so a delegated property at class scope stays untyped.
#
# The `val`/`var` keyword is captured for field classification only: a
# constructor parameter with a default (`class C(timeout: Duration = 5.seconds)`)
# closes on `=` at class scope like a property but is not one, and a match
# without the keyword gets its closer blanked.
#
# `(` is not a closer, which keeps the pattern off `@get:JvmName("x")`.
_KT_ANNOTATED = re.compile(
    rf"(?<![\w.])(?:(?P<keyword>va[lr])\s+)?(?P<name>[a-z_]\w*)\s*:\s*"
    rf"(?P<type>{_TYPE})\??\s*(?=(?P<closer>[=,)\n]|by\b))"
)

# `val x = Foo(...)`. Anchored to `val`/`var`, since a named argument
# `dispatch(logger = Emitter())` also uses `=`; bare-named, since `Foo.bar()` is
# a call on a class. The lookahead refuses a chain (`Builder().build()` is not
# a `Builder`); `_PY_CONSTRUCTED` does not refuse one yet. Ceiling: `[^()]*`
# cannot cross a nested call, so `Foo(bar(1)).baz()` is still typed.
_KT_CONSTRUCTED = re.compile(
    r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*\((?![^()]*\)\s*\.)"
)

# Swift annotates after the name: `let x: Foo`, `var x: Foo` and every parameter
# spelling are `name: Type`. Anchored on the colon, not the opening bracket,
# because in `f(label x: Foo)` the declared name is the identifier next to the
# colon, not the label.
#
# `some`/`any`, `?` and `!` are consumed: the value is still a `Foo`. `[Foo]`
# and `[K: V]` match nothing (an Array or Dictionary), and `]` is not a closer
# so a dictionary's inner `k: V` cannot close.
#
# The `let`/`var` keyword is captured as in Kotlin: an `init(timeout: ... = ...)`
# parameter closes on `=` at type scope but is not a property.
_SWIFT_ANNOTATED = re.compile(
    rf"(?<![\w.])(?:(?P<keyword>let|var)\s+)?(?P<name>[a-z_]\w*)\s*:\s*"
    rf"(?:(?:some|any)\s+)?(?P<type>{_TYPE})[?!]?\s*(?=(?P<closer>[=,)\n{{]))"
)

# `let x = Foo(...)`. Bare-named, and refusing a chain, for the reasons
# `_KT_CONSTRUCTED` gives: `let x = Foo.bar()` is a call on a class, and
# `let x = Builder().build()` makes `x` whatever `build()` returns.
_SWIFT_CONSTRUCTED = re.compile(
    r"(?<![\w.])(?:let|var)\s+(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*\((?![^()]*\)\s*\.)"
)

# C++ writes ``T name`` with differences ``_TYPE`` cannot read: ``::`` qualifies,
# ``*``/``&`` bind between type and name, and lowercase heads (the STL) are
# ordinary. A keyword head is refused, or ``struct foo {`` would declare ``foo``
# and ``auto`` would read as a type.
_CPP_KEYWORDS = (
    r"(?:const|constexpr|consteval|constinit|static|mutable|volatile|extern|"
    r"inline|virtual|explicit|friend|typedef|using|namespace|template|typename|"
    r"class|struct|union|enum|public|private|protected|return|delete|new|throw|"
    r"case|else|do|if|for|while|switch|goto|break|continue|auto|register|"
    r"operator|sizeof|decltype|noexcept|co_await|co_return|co_yield)"
)

# Two levels of nesting: the same ceiling as ``_TYPE``.
_CPP_TYPE = r"(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*<(?:[^<>]|<[^<>]*>)*>)?"

# ``T name``, ``T* name``, ``T& name``, ``ns::T name``, ``W<T> name``, closed by
# the same punctuation the C family requires plus ``{`` for brace init.
#
# ``(`` is NOT a closer: ``Status doIt(int x);`` is a method declaration, the
# commonest line in a header. That also drops ``Foo bar(args);``, costing an
# edge, the safe direction. ``>`` and ``:`` in the lookbehind stop a restart
# inside a type already read (``std::shared_ptr<Foo>& p``).
_CPP_DECLARATION = re.compile(
    rf"(?<![\w.>:])(?!{_CPP_KEYWORDS}\b)(?P<type>{_CPP_TYPE})"
    rf"(?:\s*[*&]{{1,2}}\s*|\s+)(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,){{]))"
)


_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)
# No Go shape captures a closer, so class scope drops every Go declaration:
# intended, since Go is not in IMPLICIT_FIELD_LANGUAGES.
_GO_FAMILY = (_GO_PARAM, _GO_SHORT_DECL, _GO_VAR_DECL)
_KT_FAMILY = (_KT_ANNOTATED, _KT_CONSTRUCTED)
_SWIFT_FAMILY = (_SWIFT_ANNOTATED, _SWIFT_CONSTRUCTED)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "cpp": (_CPP_DECLARATION,),
    "csharp": _C_FAMILY,
    "go": _GO_FAMILY,
    "java": _C_FAMILY,
    "kotlin": _KT_FAMILY,
    "python": (_PY_ANNOTATED, _PY_CONSTRUCTED),
    "swift": _SWIFT_FAMILY,
}

RECEIVER_TYPE_LANGUAGES = frozenset(_LANGUAGE_PATTERNS)

# Languages where a field can be named with no qualifier, the only case where a
# class-scope declaration may answer for a bare receiver. Python is absent: it
# writes ``self.foo.bar()``, a dotted receiver the grammar mints no call site
# for, so class scope could only bind a bare local to a field. Membership rests
# on the scan actually finding typed fields in the language's idiom.
IMPLICIT_FIELD_LANGUAGES = frozenset({"csharp", "java", "kotlin", "swift"})

# A decorator that changes what the symbol it wraps *is*: `@shared_task` leaves
# no function behind, so `add.s(...)` is a method call on `Task`. A table, since
# the decorator lives in an imported framework. Ceiling: an entry earns nothing
# unless the repo also declares the type.
_FRAMEWORK_DECORATOR_TYPES: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "python": (
        # celery: `@task`, `@shared_task`, `@app.task`, `@celery.task`, bare or
        # called with arguments. The qualifier is optional and must end in a
        # dot, which is what keeps `@mytask` and `@task_group` out.
        (re.compile(r"@(?:[\w.]+\.)?(?:shared_task|task)\b"), "Task"),
    ),
}

FRAMEWORK_DECORATOR_LANGUAGES = frozenset(_FRAMEWORK_DECORATOR_TYPES)

# Every shape that binds a name in a Python body, whatever its value, so the
# framework scope can refuse a rebound name (`fail = signature(...)`) that the
# CapWords-only scan cannot see. Over-matching only costs an edge.

# A comma-separated target list (`a, b[0], c.d = ...`, `for a, b in ...`), split
# by the caller so a subscript or attribute cannot hide the bare names beside it.
_TARGETS = r"[\w.\[\]]+(?:\s*,\s*[\w.\[\]]+)*"

_PY_TARGET_LISTS = (
    # An assignment, plain or augmented, at the start of a statement.
    re.compile(
        rf"(?m)^[ \t]*(?P<lhs>{_TARGETS})\s*(?::[^=\n]*)?"
        r"(?:[-+*/%|&^@]|//|\*\*|>>|<<)?=(?!=)"
    ),
    # A `for` target, statement or comprehension.
    re.compile(rf"\bfor\s+(?P<lhs>{_TARGETS})\s+in\b"),
)

_PY_BINDINGS = (
    # `with ... as n`, `except ... as n`, `import x as n`.
    re.compile(r"\bas\s+(?P<name>[a-z_]\w*)\b"),
    re.compile(r"\b(?P<name>[a-z_]\w*)\s*:="),
    re.compile(r"\b(?:global|nonlocal)\s+(?P<name>[a-z_]\w*)"),
    # A parameter of any `def` or `lambda` in the span, the enclosing one
    # included: its signature line is the first line of its own span.
    re.compile(r"\b(?:def\s+\w+\s*\(|lambda\s+)[^)\n:]*?\b(?P<name>[a-z_]\w*)\s*(?=[,=)\n:])"),
)

# A plain `import` in a body is deliberately absent: it names the same module
# symbol this scope resolves against (`from .tasks import ping`; `ping.delay()`).

_PY_IDENTIFIER = re.compile(r"^[a-z_]\w*$")


def scan_bindings(text: str, language: str) -> tuple[tuple[int, str], ...]:
    """Every ``(line, name)`` *text* binds, in line order."""
    if language != "python":
        return ()
    cleaned = _DOCSTRING.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    cleaned = _HASH_COMMENT.sub("", cleaned)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    found: set[tuple[int, str]] = set()

    for pattern in _PY_TARGET_LISTS:
        for match in pattern.finditer(cleaned):
            line = bisect_right(starts, match.start("lhs"))
            for target in match.group("lhs").split(","):
                # `self.x = ...` and `d[k] = ...` bind no bare name.
                name = target.strip()
                if _PY_IDENTIFIER.match(name):
                    found.add((line, name))

    for pattern in _PY_BINDINGS:
        for match in pattern.finditer(cleaned):
            found.add((bisect_right(starts, match.start("name")), match.group("name")))
    return tuple(sorted(found))


def names_in_span(
    bindings: tuple[tuple[int, str], ...],
    start_line: int,
    end_line: int,
) -> frozenset[str]:
    """Every name bound inside one function body."""
    first = bisect_left(bindings, start_line, key=lambda b: b[0])
    return frozenset(name for line, name in bindings[first:] if line <= end_line)


def framework_decorated_type(decorators: Iterable[str], language: str) -> str | None:
    """The type a framework decorator turns the symbol it wraps into."""
    entries = _FRAMEWORK_DECORATOR_TYPES.get(language)
    if not entries:
        return None
    for decorator in decorators:
        for pattern, type_name in entries:
            if pattern.match(decorator):
                return type_name
    return None

_LANGUAGE_BLOCK_COMMENTS: dict[str, re.Pattern[str]] = {
    # C++ needs the block strip for the reason Kotlin does: doxygen writes
    # `@param Type name`, which is exactly the shape the scan reads.
    "cpp": _BLOCK_COMMENT,
    "kotlin": _BLOCK_COMMENT,
}

_LANGUAGE_COMMENTS: dict[str, re.Pattern[str]] = {
    "cpp": _LINE_COMMENT,
    "csharp": _LINE_COMMENT,
    "go": _LINE_COMMENT,
    "java": _LINE_COMMENT,
    "kotlin": _LINE_COMMENT,
    "swift": _LINE_COMMENT,
    "python": _HASH_COMMENT,
}


class Declaration(NamedTuple):
    """One name given one type, at one line.

    ``closer`` is the punctuation that ended the declaration, or empty where
    the shape has none. Only class scope reads it.

    ``unwrapped`` marks a type taken from inside a pointer-like wrapper, which
    answers differently by call operator; a caller that cannot see the
    operator refuses these names.
    """

    line: int
    name: str
    type_name: str
    closer: str = ""
    unwrapped: bool = False


# What can end a field. `var` has no place here at all: it is a local-only
# shape in both languages, so it carries no closer and class scope drops it.
_FIELD_CLOSERS = frozenset({";", "="})


def _nests_in_a_builtin(raw: str, language: str) -> bool:
    """True for ``Map.Entry`` and its kind — a member type of a builtin.

    Its bare name ``Entry`` may match an unrelated repo type. Dropping a
    package qualifier is right, a type qualifier wrong, and a builtin head is
    the type case that can be told apart.
    """
    if "." not in raw:
        return False
    head, separator, _ = strip_type_arguments(raw).partition(".")
    return bool(separator) and head in get_builtin_types(language)


def _usable_type_name(raw: str, language: str) -> tuple[str | None, bool]:
    """``(bare name, unwrapped)`` for *raw*, or ``(None, False)``.

    Only C++ looks inside the spelling: ``shared_ptr<Foo>`` is a ``Foo`` behind
    the arrow. Elsewhere the generic head is the value's real type.
    """
    if _nests_in_a_builtin(raw, language):
        return None, False
    inner = unwrap_pointer_like(raw) if language == "cpp" else None
    name = inner or (raw if raw.isidentifier() else bare_type_name(raw))
    if not is_resolvable_type_name(name, language):
        return None, False
    return name, inner is not None


def scan_declarations(text: str, language: str) -> tuple[Declaration, ...]:
    """Every declaration *text* makes, in line order."""
    patterns = _LANGUAGE_PATTERNS.get(language)
    if not patterns:
        return ()

    cleaned = text
    if language == "python":
        cleaned = _DOCSTRING.sub(lambda m: "\n" * m.group(0).count("\n"), cleaned)
    block = _LANGUAGE_BLOCK_COMMENTS.get(language)
    if block is not None:
        cleaned = block.sub(lambda m: "\n" * m.group(0).count("\n"), cleaned)
    comment = _LANGUAGE_COMMENTS.get(language)
    if comment is not None:
        cleaned = comment.sub("", cleaned)
    # Scanned by the regex engine rather than a Python loop over characters:
    # the loop costs more than the declaration scan it exists to serve.
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]

    # One file writes the same type name hundreds of times, and normalising it
    # walks the string character by character. Resolve each spelling once.
    resolved: dict[str, tuple[str | None, bool]] = {}
    found: list[Declaration] = []
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            raw = match.group("type")
            if raw not in resolved:
                resolved[raw] = _usable_type_name(raw, language)
            type_name, unwrapped = resolved[raw]
            if type_name is None:
                continue
            groups = match.groupdict()
            # A keyword-capable shape without its keyword cannot own a field
            # (see `_KT_ANNOTATED`).
            closer = groups.get("closer") or ""
            if "keyword" in groups and not groups["keyword"]:
                closer = ""
            found.append(
                Declaration(
                    bisect_right(starts, match.start()),
                    match.group("name"),
                    type_name,
                    closer,
                    unwrapped,
                )
            )

    found.sort()
    return tuple(found)


def _record(types: dict[str, str | None], declaration: Declaration) -> None:
    """Add one declaration to a scope, or mark the name unanswerable.

    A name declared with two types maps to ``None`` rather than being dropped:
    only "says nothing" may fall through to a wider scope, not "says something
    unusable".
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

    # Bisected: walking from the front for every body is quadratic in a large file.
    first = bisect_left(declarations, start_line, key=lambda d: d.line)
    for declaration in declarations[first:]:
        if declaration.line > end_line:
            break
        _record(types, declaration)

    return types


def unwrapped_names_in_span(
    declarations: tuple[Declaration, ...],
    start_line: int,
    end_line: int,
) -> frozenset[str]:
    """The names in one body whose type was taken from inside a wrapper.

    Separate from ``types_in_span`` rather than folded into its return: only
    C++ can produce one of these, and only one caller asks.
    """
    first = bisect_left(declarations, start_line, key=lambda d: d.line)
    return frozenset(
        declaration.name
        for declaration in declarations[first:]
        if declaration.line <= end_line and declaration.unwrapped
    )


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
