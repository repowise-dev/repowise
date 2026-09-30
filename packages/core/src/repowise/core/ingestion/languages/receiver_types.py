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

# ``self.x: T`` and ``self.x = T(...)`` in a method: the fields a Python class
# gives its instances. The ``member`` group marks a declaration of the
# enclosing class wherever it sits, never of the body that makes it. A chain
# is refused as ``_KT_CONSTRUCTED`` refuses one.
_PY_SELF_ANNOTATED = re.compile(
    rf"(?m)^[ \t]*(?P<member>self)\.(?P<name>[a-z_]\w*)\s*:\s*(?P<type>{_PY_TYPE})\s*(?=[=\n])"
)
_PY_SELF_CONSTRUCTED = re.compile(
    r"(?m)^[ \t]*(?P<member>self)\.(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*"
    r"\((?![^()]*\)\s*\.)"
)

# TypeScript annotates after the name: parameters, fields, ``const x: T``. An
# optional or definite marker (``x?: T``, ``x!: T``) still leaves a ``T``. The
# type is bare or generic and must be followed by a closer at once, which
# refuses a union (``T | null``), an array (``T[]``), a qualified name and a
# function type. A ternary's ``a ? b : C`` is refused by the lookbehinds.
_TS_NAME = r"[a-z_$][\w$]*"
_TS_TYPE = r"[A-Z][\w$]*(?:<(?:[^<>]|<[^<>]*>)*>)?"
_TS_ANNOTATED = re.compile(
    rf"(?<![\w$.?])(?<!\?\s)(?P<name>{_TS_NAME})\s*[?!]?\s*:\s*(?P<type>{_TS_TYPE})"
    rf"\s*(?=(?P<closer>[=;,)\n]))"
)

# An accessibility modifier makes a constructor parameter a field of its class
# as well (``constructor(private readonly svc: Svc)``). ``_TS_ANNOTATED`` still
# reads the same text as the parameter.
_TS_PARAMETER_PROPERTY = re.compile(
    rf"(?<![\w$.])(?P<member>private|protected|public|readonly)\s+(?:readonly\s+)?"
    rf"(?P<name>{_TS_NAME})\s*[?!]?\s*:\s*(?P<type>{_TS_TYPE})\s*(?=[=,)])"
)

# ``x = new T(...)``, declared or assigned, a field initialiser included. The
# ``=`` is the closer, so at class scope it is a field. Refuses a chain as
# ``_KT_CONSTRUCTED`` does: ``new Builder().build()`` is not a ``Builder``.
_TS_CONSTRUCTED = re.compile(
    rf"(?<![\w$.])(?P<name>{_TS_NAME})\s*(?P<closer>=)\s*new\s+(?P<type>[A-Z][\w$]*)\s*"
    r"(?:<(?:[^<>]|<[^<>]*>)*>)?\s*\((?![^()]*\)\s*\.)"
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
_PY_FAMILY = (_PY_ANNOTATED, _PY_CONSTRUCTED, _PY_SELF_ANNOTATED, _PY_SELF_CONSTRUCTED)
_TS_FAMILY = (_TS_ANNOTATED, _TS_PARAMETER_PROPERTY, _TS_CONSTRUCTED)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "cpp": (_CPP_DECLARATION,),
    "csharp": _C_FAMILY,
    "go": _GO_FAMILY,
    "java": _C_FAMILY,
    "kotlin": _KT_FAMILY,
    "python": _PY_FAMILY,
    "swift": _SWIFT_FAMILY,
    "typescript": _TS_FAMILY,
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
)

# Every parameter of any `def` or `lambda` in the span, the enclosing one
# included: its signature starts its own span. Each lowercase identifier in the
# list counts, a default's names too, since over-matching only refuses.
# Ceiling: a `def` list ends at its first `)`, so a default that calls hides
# the parameters after it.
_PY_PARAMETER_LISTS = (
    re.compile(r"\bdef\s+\w+\s*\((?P<lhs>[^)]*)\)"),
    re.compile(r"\blambda\b(?P<lhs>[^:\n]*):"),
)
_PY_NAME = re.compile(r"(?<![\w.])[a-z_]\w*")

# A plain `import` in a body is deliberately absent: it names the same module
# symbol this scope resolves against (`from .tasks import ping`; `ping.delay()`).

_PY_IDENTIFIER = re.compile(r"^[a-z_]\w*$")

# The TypeScript shapes that bind a name, typed or not. Over-matching only
# refuses, so every identifier in a destructuring pattern or a parameter list
# counts, a type name or a renamed key included. Ceiling: a parameter list
# holding parentheses (a default that calls, a function type) is not read.
_TS_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_TS_BINDINGS = (
    re.compile(r"(?<![\w$.])(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"),
    re.compile(r"(?<![\w$.])(?:function\*?|class)\s+(?P<name>[A-Za-z_$][\w$]*)"),
    re.compile(r"(?<![\w$.])(?P<name>[A-Za-z_$][\w$]*)\s*=>"),
    # An assignment at the start of a statement, plain or compound.
    re.compile(
        r"(?m)^[ \t]*(?P<name>[A-Za-z_$][\w$]*)\s*"
        r"(?:[-+*/%|&^]|\*\*|\?\?|\|\||&&|<<|>>>?)?=(?![=>])"
    ),
)
_TS_TARGET_LISTS = (
    re.compile(r"(?<![\w$.])(?:const|let|var)\s*(?P<lhs>\{[^;=]*\}|\[[^;=]*\])\s*(?:=|of\b|in\b)"),
    # A parameter list: a function, a method, an arrow, a catch clause.
    re.compile(r"(?P<head>[\w$]*)\s*(?P<lhs>\([^()]*\))\s*(?::[^=;{}()]*?)?\s*(?:=>|\{)"),
)
# Heads whose parenthesised part is a condition, not a parameter list.
_TS_CONDITION_HEADS = frozenset({"if", "for", "while", "switch", "with", "return", "await"})


def _named_bindings(
    patterns: Iterable[re.Pattern[str]], cleaned: str, starts: list[int]
) -> set[tuple[int, str]]:
    """``(line, name)`` for each pattern's ``name`` group."""
    return {
        (bisect_right(starts, match.start("name")), match.group("name"))
        for pattern in patterns
        for match in pattern.finditer(cleaned)
    }


def _listed_bindings(
    patterns: Iterable[re.Pattern[str]],
    identifier: re.Pattern[str],
    cleaned: str,
    starts: list[int],
) -> set[tuple[int, str]]:
    """``(line, name)`` for every identifier in each pattern's ``lhs`` list."""
    found: set[tuple[int, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            # Only TypeScript's lists carry a ``head`` to refuse.
            if match.groupdict().get("head") in _TS_CONDITION_HEADS:
                continue
            offset = match.start("lhs")
            for name in identifier.finditer(match.group("lhs")):
                found.add((bisect_right(starts, offset + name.start()), name.group()))
    return found


def _python_target_bindings(cleaned: str, starts: list[int]) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for pattern in _PY_TARGET_LISTS:
        for match in pattern.finditer(cleaned):
            line = bisect_right(starts, match.start("lhs"))
            for target in match.group("lhs").split(","):
                # `self.x = ...` and `d[k] = ...` bind no bare name.
                name = target.strip()
                if _PY_IDENTIFIER.match(name):
                    found.add((line, name))
    return found


# The languages ``scan_bindings`` reads: only there can a scope be shown not
# to bind a name.
BINDING_LANGUAGES = frozenset({"python", "typescript"})


def scan_bindings(text: str, language: str) -> tuple[tuple[int, str], ...]:
    """Every ``(line, name)`` *text* binds, in line order."""
    if language not in BINDING_LANGUAGES:
        return ()
    cleaned = _without_comments(text, language)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    if language == "typescript":
        found = _named_bindings(_TS_BINDINGS, cleaned, starts)
        found |= _listed_bindings(_TS_TARGET_LISTS, _TS_IDENTIFIER, cleaned, starts)
    else:
        found = _python_target_bindings(cleaned, starts)
        found |= _named_bindings(_PY_BINDINGS, cleaned, starts)
        found |= _listed_bindings(_PY_PARAMETER_LISTS, _PY_NAME, cleaned, starts)
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
    "typescript": _BLOCK_COMMENT,
}

_LANGUAGE_COMMENTS: dict[str, re.Pattern[str]] = {
    "cpp": _LINE_COMMENT,
    "csharp": _LINE_COMMENT,
    "go": _LINE_COMMENT,
    "java": _LINE_COMMENT,
    "kotlin": _LINE_COMMENT,
    "swift": _LINE_COMMENT,
    "python": _HASH_COMMENT,
    "typescript": _LINE_COMMENT,
}


def _without_comments(text: str, language: str) -> str:
    """*text* with its comments (and Python docstrings) blanked, newlines kept."""
    cleaned = text
    if language == "python":
        cleaned = _DOCSTRING.sub(lambda m: "\n" * m.group(0).count("\n"), cleaned)
    block = _LANGUAGE_BLOCK_COMMENTS.get(language)
    if block is not None:
        cleaned = block.sub(lambda m: "\n" * m.group(0).count("\n"), cleaned)
    comment = _LANGUAGE_COMMENTS.get(language)
    if comment is not None:
        cleaned = comment.sub("", cleaned)
    return cleaned


class Declaration(NamedTuple):
    """One name given one type, at one line.

    ``closer`` is the punctuation that ended the declaration, or empty where
    the shape has none. Only class scope reads it.

    ``unwrapped`` marks a type taken from inside a pointer-like wrapper, which
    answers differently by call operator; a caller that cannot see the
    operator refuses these names.

    ``member`` marks a field of the enclosing class declared from inside a
    method (``self.x = T()``, a constructor parameter property). It belongs
    to the class wherever it sits, and never to the body.
    """

    line: int
    name: str
    type_name: str
    closer: str = ""
    unwrapped: bool = False
    member: bool = False


# What can end a field. `var` has no place here at all: it is a local-only
# shape in both languages, so it carries no closer and class scope drops it.
_FIELD_CLOSERS = frozenset({";", "="})

# Where a class-scope annotation may end its line: Python's ``x: T`` and a
# semicolon-free TypeScript ``x: T`` are fields too.
_LANGUAGE_FIELD_CLOSERS: dict[str, frozenset[str]] = {
    "python": frozenset({"=", "\n"}),
    "typescript": frozenset({";", "=", "\n"}),
}


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

    cleaned = _without_comments(text, language)
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
                    bool(groups.get("member")),
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
        if not declaration.member:
            _record(types, declaration)

    return types


def bound_types(
    declarations: Iterable[Declaration],
    bindings: Iterable[tuple[int, str]],
    language: str,
    *,
    rebinding_refuses: bool = False,
) -> dict[str, str | None]:
    """``{name: type}`` for one scope, read against every name it binds.

    In TypeScript a declaration must itself be a binding (``const``, ``let``,
    a parameter, an assignment), which keeps an object literal's
    ``key: Value`` from declaring ``key``. Under *rebinding_refuses* a name the
    scope also binds untyped on another line maps to ``None``: asked of module
    scope, where the rebinding can run anywhere before the call. A body keeps
    its first reading, as ``types_in_span`` does.
    """
    bound = set(bindings)
    types: dict[str, str | None] = {}
    typed: set[tuple[int, str]] = set()
    for declaration in declarations:
        if declaration.member:
            continue
        spot = (declaration.line, declaration.name)
        if language == "typescript" and spot not in bound:
            continue
        _record(types, declaration)
        typed.add(spot)
    for spot in bound - typed if rebinding_refuses else ():
        if spot[1] in types:
            types[spot[1]] = None
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


def merge_spans(spans: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """The spans as non-overlapping, ascending intervals."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def in_spans(merged: tuple[tuple[int, int], ...], line: int) -> bool:
    """Is *line* inside one of ``merge_spans``' intervals?"""
    index = bisect_right(merged, (line, float("inf"))) - 1
    return index >= 0 and line <= merged[index][1]


def types_by_class(
    declarations: tuple[Declaration, ...],
    class_spans: Mapping[str, tuple[int, int]],
    function_spans: Iterable[tuple[int, int]],
    language: str = "",
) -> dict[str, dict[str, str | None]]:
    """``{class_id: {name: type}}`` for the fields each class declares.

    A class span contains every method body inside it, so a declaration is a
    field only if it lies inside the class and inside none of the file's
    functions, unless it is a ``member``. Nested classes go to the innermost
    class containing them, so an inner class's fields never answer for the
    outer one.
    """
    closers = _LANGUAGE_FIELD_CLOSERS.get(language, _FIELD_CLOSERS)
    if not class_spans:
        return {}

    bodies = merge_spans(function_spans)
    body_starts = [start for start, _ in bodies]
    # Innermost first, so the first containing span is the owner.
    ordered = sorted(class_spans.items(), key=lambda item: item[1][1] - item[1][0])

    by_class: dict[str, dict[str, str | None]] = {}
    for declaration in declarations:
        if not declaration.member:
            if declaration.closer not in closers:
                continue
            index = bisect_right(body_starts, declaration.line) - 1
            if index >= 0 and declaration.line <= bodies[index][1]:
                continue
        for class_id, (start, end) in ordered:
            if start <= declaration.line <= end:
                _record(by_class.setdefault(class_id, {}), declaration)
                break

    return by_class


# ``for k, v := range expr`` declares ``k`` and ``v`` with no type spelled: each
# is whatever ranging over ``expr``'s container yields in its position. Two
# scans feed it, the clauses and the containers they may range over, and the
# resolver joins them, since only it can type ``expr`` itself.
RANGE_LANGUAGES = frozenset({"go"})

# What ranging over each container shape yields, the group name giving the
# position: ``[]T``, ``[N]T`` and a variadic ``...T`` an int index, then a
# ``T``; ``map[K]V`` a ``K``, then a ``V``; ``chan T`` a ``T`` alone. A pointer
# element is still a ``T``. Anything else, a named container type included,
# yields nothing, so its range variables stay untyped.
_GO_ELEMENT_SHAPES = (
    re.compile(rf"(?:\[\d*\]|\.\.\.)\*?(?P<value>{_GO_TYPE})"),
    re.compile(rf"map\[\*?(?P<key>{_GO_TYPE})\]\*?(?P<value>{_GO_TYPE})"),
    re.compile(rf"(?:<-\s*)?chan(?:\s*<-)?\s+\*?(?P<key>{_GO_TYPE})"),
)

# Any one of those spellings, for the declaration scans to capture whole.
_GO_CONTAINER = (
    rf"(?:\[\d*\]|\.\.\.|map\[\*?{_GO_TYPE}\]|(?:<-\s*)?chan(?:\s*<-)?\s+)\*?{_GO_TYPE}"
)

_GO_CONTAINER_DECLARATIONS = (
    # A parameter or named result, as ``_GO_PARAM``. Only the last name of
    # ``a, b []T`` is read: a missed declaration, never a wrong one.
    re.compile(rf"(?<![\w.])(?P<name>{_GO_NAME})\s+(?P<type>{_GO_CONTAINER})\s*(?=[,)])"),
    # ``var xs []T``.
    re.compile(rf"(?m)(?<![\w.])var\s+(?P<name>{_GO_NAME})\s+(?P<type>{_GO_CONTAINER})\s*(?=[=;)]|$)"),
    # ``xs := []T{}`` and ``xs := make([]T, n)``.
    re.compile(
        rf"(?<![\w.])(?P<name>{_GO_NAME})\s*:=\s*(?:make\(\s*)?(?P<type>{_GO_CONTAINER})\s*(?=[{{,)])"
    ),
    # A struct field is the whole line, tag aside. ``member`` keeps it out of
    # every body and admits it at class scope, where no closer marks it.
    re.compile(
        rf"(?m)^[ \t]*(?P<member>(?P<name>[A-Za-z_]\w*))[ \t]+(?P<type>{_GO_CONTAINER})"
        r"[ \t]*(?:`[^`\n]*`)?[ \t]*$"
    ),
)

# The range expression is a name, ``h.field`` or ``h.Method()``, closed by the
# loop's brace. Anything longer (``h.a.b``, ``f().x``, ``xs[1:]``) is refused.
_GO_RANGE = re.compile(
    rf"\bfor\s+(?P<key>{_GO_NAME})(?:\s*,\s*(?P<value>{_GO_NAME}))?\s*:=\s*range\s+"
    rf"(?P<head>{_GO_NAME})(?:\.(?P<member>[A-Za-z_]\w*)(?P<call>\(\))?)?\s*\{{"
)


class RangeClause(NamedTuple):
    """One ``for key, value := range head[.member[()]]`` at one line.

    ``value`` is empty when only one variable is declared; ``_`` names none.
    """

    line: int
    key: str
    value: str
    head: str
    member: str
    call: bool


class RangeScan(NamedTuple):
    """A file's range clauses, and its container declarations typed by spelling."""

    clauses: tuple[RangeClause, ...]
    containers: tuple[Declaration, ...]


def scan_ranges(text: str, language: str) -> RangeScan:
    """Every range clause and container declaration *text* makes, in line order."""
    if language not in RANGE_LANGUAGES:
        return RangeScan((), ())
    cleaned = _without_comments(text, language)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    clauses = tuple(
        RangeClause(
            bisect_right(starts, match.start()),
            match.group("key"),
            match.group("value") or "",
            match.group("head"),
            match.group("member") or "",
            bool(match.group("call")),
        )
        for match in _GO_RANGE.finditer(cleaned)
    )
    containers = sorted(
        Declaration(
            bisect_right(starts, match.start()),
            match.group("name"),
            match.group("type"),
            member=bool(match.groupdict().get("member")),
        )
        for pattern in _GO_CONTAINER_DECLARATIONS
        for match in pattern.finditer(cleaned)
    )
    return RangeScan(clauses, tuple(containers))


def range_element_types(
    spelling: str | None, language: str
) -> tuple[str | None, str | None] | None:
    """``(key type, value type)`` for ranging over *spelling*, or None if unknown.

    A position holding a predeclared type (an index's ``int``) is None within
    a known shape.
    """
    if not spelling or language not in RANGE_LANGUAGES:
        return None
    for shape in _GO_ELEMENT_SHAPES:
        match = shape.fullmatch(spelling)
        if match is not None:
            key, value = (match.groupdict().get(role) for role in ("key", "value"))
            return (
                _usable_type_name(key, language)[0] if key else None,
                _usable_type_name(value, language)[0] if value else None,
            )
    return None


def clauses_in_span(
    clauses: tuple[RangeClause, ...], start_line: int, end_line: int
) -> tuple[RangeClause, ...]:
    """The range clauses inside one function body, in line order."""
    first = bisect_left(clauses, start_line, key=lambda c: c.line)
    last = bisect_right(clauses, end_line, key=lambda c: c.line)
    return clauses[first:last]
