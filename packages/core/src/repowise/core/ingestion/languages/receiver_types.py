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

Adding a language means adding its shapes to ``_LANGUAGE_PATTERNS``; a
language absent from it is excluded by construction. Two smaller tables sit
beside it: ``_FRAMEWORK_DECORATOR_TYPES``, for a decorator that changes what
the symbol it wraps is, and ``_PY_BINDINGS``, which says only that a name is
taken — a refusal rather than a type.
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

# A block comment, blanked to its newlines so line numbers survive. Kotlin
# needs this where Java and C# do not, and the asymmetry is in the shapes
# rather than in the languages: KDoc writes `@param connection: Store`, which
# is exactly Kotlin's `name: Type`, while javadoc's `@param connection the
# Store` is not the C family's `Type name`. Measured on the same text — the
# Kotlin scan fabricated `connection: Store` from prose and the Java scan
# returned nothing.
#
# Run before the line strip, not after: truncating at `//` first would eat the
# `*/` out of `/* see http://x */` and leave the opener unterminated. As with
# `//`, a `/*` inside a string literal is over-matched, which can only ever
# lose a declaration and never invent one.
_BLOCK_COMMENT = re.compile(r"/\*(?:.|\n)*?\*/")

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

# Kotlin annotates after the name, as Python does, and one shape reaches every
# declaration that matters: `val x: Foo`, `var x: Foo`, `fun f(x: Foo)` and a
# primary constructor's `class A(val x: Foo)` are all `name: Type`. Measured
# over ktor and exposed, that one shape is 6,291 typed `val`s, 1,242 `var`s and
# ~8,800 parameters, against 1,183 for the constructor shape below.
#
# The type reuses the C family's `_TYPE`, so a generic is matched and reduced to
# its bare name. That is right in Kotlin and wrong in Python for the same
# reason spelled backwards: `List<Foo>` bares to `List`, a builtin the language
# spec refuses downstream, while `Column<T>` bares to `Column`, which is the
# type the value actually has. Python's `Optional[T]` had no such reading, which
# is why `_PY_ANNOTATED` refuses a generic outright.
#
# A trailing `?` is consumed rather than refused: `Foo?` is still a `Foo` at the
# call site, and it is 15% of ktor's typed declarations.
#
# `by` closes a declaration as well as the punctuation does. `val x: Foo by
# lazy { ... }` is a delegated property, `x` is a `Foo`, and without this the
# whole idiom types nothing. It is a word rather than a symbol, so it is an
# alternation and not another character in the class — and it can never be a
# field closer, which is the conservative reading: a delegated property at
# class scope stays untyped rather than being guessed at.
#
# The `val`/`var` keyword is captured, optional, and read by nothing except
# field classification. A parameter is `name: Type` with no keyword, and a
# primary constructor's `class C(timeout: Duration = 5.seconds)` therefore
# closes on `=` at class scope exactly as a real property does — so without
# this group `timeout` is registered as a field it is not, since a parameter
# with no `val`/`var` is not a property and no method body can name it.
# Blanking the closer is what refuses it; body typing is unaffected either
# way, because only `types_by_class` reads a closer at all.
#
# `(` is deliberately not a closer. It is what keeps the pattern off an
# annotation use-site target — `@get:JvmName("x")` reads as `get: JvmName`.
#
# A function type's own parameters are reached incidentally: `statement:
# Transaction.(dest: Dest) -> Unit` presents `dest: Dest` to the same pattern.
# That is kept rather than excluded — the hand-read found 3 such rows and all
# 3 were right, and the validator makes a wrong one cost an edge, not an error.
_KT_ANNOTATED = re.compile(
    rf"(?<![\w.])(?:(?P<keyword>va[lr])\s+)?(?P<name>[a-z_]\w*)\s*:\s*"
    rf"(?P<type>{_TYPE})\??\s*(?=(?P<closer>[=,)\n]|by\b))"
)

# `val x = Foo(...)`, the shape an inferred Kotlin declaration takes. Anchored
# to `val`/`var` rather than to the start of a statement, which is what Python's
# equivalent needed: Kotlin spells a named argument with `=` too, so
# `dispatch(logger = Emitter())` is otherwise read as declaring `logger`.
# Bare-named on purpose — `val x = Foo.bar()` is a call on a class, and typing
# `x` as `Foo` from it would simply be wrong.
#
# The lookahead refuses a chain: `val x = Builder().build()` makes `x` whatever
# `build()` returns, not a `Builder`, and typing it as one is a wrong answer
# rather than a missing one. `_PY_CONSTRUCTED` has the same shape and does not
# refuse it; moving Python is its own measured change and is not made here.
# `[^()]*` cannot cross a nested call, so `Foo(bar(1)).baz()` is still typed --
# a stated ceiling, and the same direction of error as today rather than a new
# one.
_KT_CONSTRUCTED = re.compile(
    r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)\s*\((?![^()]*\)\s*\.)"
)

# Swift annotates after the name as Kotlin does, and the same one shape reaches
# more of the language than it does of Kotlin: `let x: Foo`, `var x: Foo` and
# all three parameter spellings are `name: Type`.
#
# The argument label is the reason to anchor on the colon rather than on the
# bracket that opens the list. Swift writes a parameter as `f(x: Foo)`,
# `f(_ x: Foo)` or `f(label x: Foo)`, and in the last of those the declared
# name is the *second* identifier. A pattern anchored at `(` or `,` takes the
# label instead and is wrong 736 times over swift-nio and Alamofire. The
# identifier adjacent to the colon is the declared name in all three
# spellings, with no branch on any of them.
#
# Measured over those two repos, this one shape is 88% of the declaration
# population: 4,001 single-name parameters, 2,876 stored properties, 2,104
# `var`s, 1,356 underscore-label parameters, 1,304 `let`s and 736 two-name
# parameters. The construction shape below is a further 6%.
#
# `some`/`any` is consumed rather than refused: an opaque or existential
# `some Foo` is a `Foo` at the call site, as a trailing `?` or `!` is.
#
# `[Foo]` and `[K: V]` match nothing on purpose — the value is an Array or a
# Dictionary, and typing the name as `Foo` would be wrong rather than merely
# unhelpful. `]` stays out of the closer set for the same reason: it is what a
# dictionary type's inner `k: V` would otherwise close on.
#
# The `let`/`var` keyword is captured for the reason Kotlin's is: an
# initialiser's `init(timeout: Duration = .seconds(5))` sits at type scope and
# closes on `=` exactly as a stored property does, and it is a parameter
# rather than a property. Only a keyword-bearing match may own a field.
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

# C++ writes the C family's ``T name``, with three differences ``_TYPE`` cannot
# read: ``::`` qualifies rather than ``.``, a pointer or reference star binds
# between the type and the name, and a lowercase head is ordinary rather than a
# Go-style unexported one, because every STL type is written that way.
#
# A keyword head is refused outright. Without it ``struct foo {`` and
# ``namespace foo {`` present as ``struct``/``namespace`` naming a ``foo``, and
# ``auto`` would be read as a type rather than as the absence of one.
_CPP_KEYWORDS = (
    r"(?:const|constexpr|consteval|constinit|static|mutable|volatile|extern|"
    r"inline|virtual|explicit|friend|typedef|using|namespace|template|typename|"
    r"class|struct|union|enum|public|private|protected|return|delete|new|throw|"
    r"case|else|do|if|for|while|switch|goto|break|continue|auto|register|"
    r"operator|sizeof|decltype|noexcept|co_await|co_return|co_yield)"
)

# Two levels of nesting, the same ceiling ``_TYPE`` states and for the same
# reason: a third needs a real bracket matcher, and failing to type a name
# costs an edge rather than inventing one.
_CPP_TYPE = r"(?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*(?:\s*<(?:[^<>]|<[^<>]*>)*>)?"

# ``T name``, ``T* name``, ``T& name``, ``ns::T name``, ``W<T> name``, closed by
# the same punctuation the C family requires plus ``{`` for brace init.
#
# ``(`` is deliberately NOT a closer, and it is load-bearing twice. ``Status
# doIt(int x);`` is a method declaration and not a variable of type ``Status``,
# and it is the commonest line in a C++ header. Excluding it also drops ``Foo
# bar(args);``, a real construction -- that costs an edge, which is the safe
# direction, and it additionally keeps a constructed local from being read at a
# scope where a same-named field would answer instead.
#
# ``>`` and ``:`` sit in the lookbehind so the scan cannot restart inside a
# type it has already read: without them ``std::shared_ptr<Foo>& p`` matches a
# second time at ``shared_ptr``.
_CPP_DECLARATION = re.compile(
    rf"(?<![\w.>:])(?!{_CPP_KEYWORDS}\b)(?P<type>{_CPP_TYPE})"
    rf"(?:\s*[*&]{{1,2}}\s*|\s+)(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,){{]))"
)


_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)
# No Go shape captures a closer, so every Go declaration carries ``""`` and
# class scope would drop all of them. That is the intended reading: Go is not
# in IMPLICIT_FIELD_LANGUAGES and must not be.
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

# Languages where a field can be named with no qualifier, which is the only
# thing that lets a class-scope declaration answer for a bare receiver. Python
# writes ``self.foo.bar()``, whose receiver is dotted and which our grammar
# queries mint no call site for at all — so class scope has nothing there to
# answer, and consulting it could only bind a bare local name to a field.
#
# Kotlin is here on a count rather than on its semantics, which is the lesson
# the Python attempt cost: Python has implicit field access too, and
# registering it would have promised nothing, because only 1.8% of its field
# receivers are typed where this scan looks. Kotlin declares a property at
# class scope in its own idiom, and the scan does find them — 56 of ktor's
# 1,349 gained edges and 100 of exposed's 320, hand-read 10/10 correct. Small,
# and measured.
#
# Only a `val`/`var` reaches class scope. A primary-constructor parameter with
# a default closes on `=` there too and is not a property at all, which is why
# `_KT_ANNOTATED` captures the keyword.
IMPLICIT_FIELD_LANGUAGES = frozenset({"csharp", "java", "kotlin", "swift"})

# A decorator that changes what the symbol it wraps *is*: `@shared_task` leaves
# no function behind, so `add.s(...)` is a method call and `(Task, s)` a
# checkable pair. A table, not an inference — the decorator lives in the
# framework, which an application imports rather than vendors. Ceiling: an entry
# earns nothing unless the repo also declares the type.
_FRAMEWORK_DECORATOR_TYPES: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "python": (
        # celery: `@task`, `@shared_task`, `@app.task`, `@celery.task`, bare or
        # called with arguments. The qualifier is optional and must end in a
        # dot, which is what keeps `@mytask` and `@task_group` out.
        (re.compile(r"@(?:[\w.]+\.)?(?:shared_task|task)\b"), "Task"),
    ),
}

FRAMEWORK_DECORATOR_LANGUAGES = frozenset(_FRAMEWORK_DECORATOR_TYPES)

# Every shape that binds a name in a Python body, whatever its value. The
# framework scope must refuse a name the body rebinds: `fail = signature(...)`
# leaves `fail` a Signature, and the CapWords-only scan above cannot see it.
# Over-matching is safe here — refusing only ever costs an edge.

# A comma-separated target list: every name in `a, b[0], c.d = ...` and in
# `for a, b in ...`. Read whole and split by the caller, so a subscript or an
# attribute in any position cannot hide the bare names beside it.
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

# A plain `import` inside a body is deliberately absent: it names the same
# module symbol this scope resolves against, and refusing it cost 3 correct
# edges on celery (`from .tasks import ping`, then `ping.delay()`).

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

    ``unwrapped`` marks a type taken from inside a pointer-like wrapper rather
    than written outright. The two spellings answer differently depending on
    which operator the call used, and the caller that cannot see the operator
    reads this to refuse the ambiguous names.
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

    Taking the bare name of one of these answers ``Entry``, which the repo may
    well declare somewhere and which the declaration never meant. Discarding a
    qualifier is right when the qualifier is a package; it is wrong when the
    qualifier is a type, and a builtin head is the case we can tell apart.
    """
    if "." not in raw:
        return False
    head, separator, _ = strip_type_arguments(raw).partition(".")
    return bool(separator) and head in get_builtin_types(language)


def _usable_type_name(raw: str, language: str) -> tuple[str | None, bool]:
    """``(bare name, unwrapped)`` for *raw*, or ``(None, False)``.

    C++ is the one language that looks inside the spelling: ``shared_ptr<Foo>``
    denotes a ``Foo`` at every call the arrow can reach, and taking the head
    would answer ``shared_ptr``, which names no repo symbol and resolves
    nothing. Every other language keeps the head, where a generic really is
    the type the value has.
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
            # A shape that names a declaration keyword and did not match one
            # is not a declaration that can own a field — see `_KT_ANNOTATED`.
            # Shapes with no `keyword` group are unaffected.
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
