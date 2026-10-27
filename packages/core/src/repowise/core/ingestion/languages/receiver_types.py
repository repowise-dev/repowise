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
from collections.abc import Callable, Iterable, Mapping
from typing import NamedTuple

from ..framework_routes import scan_code
from ..language_data import get_builtin_types
from ..type_names import (
    bare_type_name,
    is_resolvable_type_name,
    strip_type_arguments,
    type_qualifier,
    unwrap_pointer_like,
    written_type_key,
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

# A struct field is the rest of its line: a type, its type arguments (two levels,
# the ``_TYPE`` ceiling) and a tag. A field is typed only as ``T`` or ``*T``, as
# ``_GO_SHORT_DECL`` types a value: ``[]T``, ``map[K]T`` and ``chan T`` hold a
# ``T`` but are not one. Exported fields are capitalised, so the name is too.
#
# The type is unqualified: ``fd *sftp.File`` is never the ``File`` the file
# itself declares, and a chain hop can only look a type up in its own file.
# Ceiling: a field typed from another repo package stays untyped until a hop
# can follow a Go import to a package.
_GO_FIELD_NAME = r"[A-Za-z_]\w*"
_GO_FIELD_END = r"(?:\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\])?[ \t]*(?:`[^`\n]*`?)?[ \t]*$"

# ``parent *Command``, and each name of ``a, b Foo``: a name opens the line or
# follows a comma, and the lookahead leaves the next name unconsumed. Both field
# shapes set ``member`` to their whole match, so they reach class scope whatever
# closed them and never type a local: a body line such as ``return err`` matches
# too, but lies in no struct. That holds while a Go struct span never contains a
# function body; ``test_a_field_never_types_a_local`` guards it.
_GO_FIELD = re.compile(
    rf"(?m)(?:^[ \t]*|,[ \t]*)(?P<member>(?P<name>{_GO_FIELD_NAME}))"
    rf"(?=(?:[ \t]*,[ \t]*{_GO_FIELD_NAME})*[ \t]+\*?(?P<type>{_GO_FIELD_NAME}){_GO_FIELD_END})"
)

# An embedded ``Base`` or ``*Base`` is a field named ``Base``, the name
# ``c.Base.Method()`` reads it by.
_GO_EMBEDDED_FIELD = re.compile(
    rf"(?m)^[ \t]*\*?(?P<member>(?P<type>(?P<name>{_GO_FIELD_NAME}))){_GO_FIELD_END}"
)

# ``x := f(..)``, ``x, err := pkg.New(..)``: the head of a call whose return
# type names the local. Only the head is matched here; ``scan_call_assignments``
# checks that the call is the whole right-hand side, not ``f().g()``.
_GO_CALL_DECL = re.compile(
    rf"(?<![\w.])(?P<name>{_GO_NAME})\s*(?:,\s*{_GO_NAME}\s*)*:="
    r"[ \t]*(?:(?P<receiver>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.)?(?P<target>[A-Za-z_]\w*)\s*\("
)

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
    r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*(?P<closer>=)\s*(?P<type>[A-Z]\w*)\s*"
    r"\((?![^()]*\)\s*\.)"
)
# `val x = Foo { ... }`: a constructor whose only argument is a trailing lambda.
# The match ends at the `{`; `scan_declarations` keeps it only when the lambda
# ends the statement, so `Foo { }.build()` is not a `Foo`.
_KT_CONSTRUCTED_LAMBDA = re.compile(
    r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*=\s*(?P<type>[A-Z]\w*)[ \t]*(?=\{)"
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
    r"(?<![\w.])(?:let|var)\s+(?P<name>[a-z_]\w*)\s*(?P<closer>=)\s*(?P<type>[A-Z]\w*)\s*"
    r"\((?![^()]*\)\s*\.)"
)

# Dart writes the C family's `Type name`, plus a nullable `Api? api` and a named
# parameter closing on `}`. Fields close on `;` or `=` as Java's do. A
# constructor is no symbol here, so its parameters sit at class scope; nothing
# inside parentheses owns a field (`_PARAMETERS_AT_CLASS_SCOPE`), which keeps
# out `{Dao d = const Dao()}` too. An initialising formal (`Repo(this.api)`)
# spells no type: the field it assigns already carries one.
_DART_TYPED = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE})\??\s+(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,)}}]|in\b))"
)

# `final x = Foo()`, `var`/`const`, with `new`/`const` before the type. The
# `=` closer makes it a field at class scope, as `_TS_CONSTRUCTED`'s does. A
# chain is refused as `_KT_CONSTRUCTED` refuses one, but a cascade
# (`Paint()..color = c`) is still the `Paint`. `Foo.named()` is refused too:
# a named constructor and a static call look the same.
_DART_CONSTRUCTED = re.compile(
    r"(?<![\w.])(?:final|var|const)\s+(?P<name>[a-z_]\w*)\s*(?P<closer>=)\s*"
    r"(?:(?:new|const)\s+)?(?P<type>[A-Z]\w*)\s*(?:<(?:[^<>]|<[^<>]*>)*>)?\s*"
    r"\((?![^()]*\)\s*\.(?!\.))"
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


# Rust annotates after the name: parameters, `let x: Foo`, closure parameters.
# A path is kept only when it starts inside the crate (`crate::`, `self::`,
# `super::`): `std::process::Child` bares to a `Child` the repo may also
# declare. A borrow (`&Foo`, `&'a mut Foo`) and a `Box<Foo>` still reach
# `Foo` by auto-deref, so both are read through; `dyn`/`impl` heads are
# lowercase and match nothing. A bare `Foo` path is still refused downstream
# when a `use` binds it outside the repo.
_RUST_TYPE = r"(?:(?:crate|self|super)::(?:\w+::)*)?[A-Z]\w*"
_RUST_ANNOTATED = re.compile(
    rf"(?<![\w.:'])(?P<name>[a-z_]\w*)\s*:\s*(?:&(?:'\w+\s+)?(?:mut\s+)?)?(?P<box>Box<)?"
    rf"(?P<type>{_RUST_TYPE}(?:<(?:[^<>]|<[^<>]*>)*>)?)(?(box)>)\s*(?=[=,;)|])"
)

# `let x = Foo::new(..)` and the other constructor-named associated functions,
# the Rust spelling of `_KT_CONSTRUCTED`. A chain is refused as there
# (`Foo::new().build()` is whatever `build` returns); a `?` is read through,
# since it unwraps the `Result<Foo>` a fallible constructor returns.
_RUST_CONSTRUCTED = re.compile(
    rf"(?<![\w.])let\s+(?:mut\s+)?(?P<name>[a-z_]\w*)\s*=\s*(?P<type>{_RUST_TYPE})"
    r"::(?:new|default|from|with_\w+)\s*\((?![^()]*\)\s*\??\s*\.)"
)

_C_FAMILY = (_TYPED_DECLARATION, _INFERRED_FROM_NEW)

# A Java primitive or primitive array, ``long start`` / ``int[] xs``. Kept as an
# external type: no call is made on one, but an overload is chosen by it.
_JAVA_PRIMITIVE_DECLARATION = re.compile(
    r"(?<![\w.])(?P<type>(?:boolean|byte|char|short|int|long|float|double)(?:\[\])*(?:\.\.\.)?)"
    r"\s+(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[=;,):]))"
)
# A varargs parameter, ``Foo... xs``: the array it is.
_JAVA_VARARGS_DECLARATION = re.compile(
    rf"(?<![\w.])(?P<type>{_TYPE}\.\.\.)\s+(?P<name>[a-z_]\w*)\s*(?=(?P<closer>[,)]))"
)
# A pattern variable, ``x instanceof Foo f && ...``: a local wherever it ends.
_JAVA_PATTERN_VARIABLE = re.compile(
    rf"(?<![\w.])instanceof\s+(?:final\s+)?(?P<type>{_TYPE})\s+(?P<name>[a-z_]\w*)\b(?!\s*[.(])"
)
# Only the field shapes reach class scope. Go is still not in
# IMPLICIT_FIELD_LANGUAGES: a field is read through its receiver (``c.parent``),
# so it types a chain hop and never a bare name.
_GO_FAMILY = (_GO_PARAM, _GO_SHORT_DECL, _GO_VAR_DECL, _GO_FIELD, _GO_EMBEDDED_FIELD)
_KT_FAMILY = (_KT_ANNOTATED, _KT_CONSTRUCTED, _KT_CONSTRUCTED_LAMBDA)
_SWIFT_FAMILY = (_SWIFT_ANNOTATED, _SWIFT_CONSTRUCTED)
_DART_FAMILY = (_DART_TYPED, _DART_CONSTRUCTED)
_PY_FAMILY = (_PY_ANNOTATED, _PY_CONSTRUCTED, _PY_SELF_ANNOTATED, _PY_SELF_CONSTRUCTED)
_TS_FAMILY = (_TS_ANNOTATED, _TS_PARAMETER_PROPERTY, _TS_CONSTRUCTED)

_LANGUAGE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Apex declares Java's way (``Foo foo``) and has no ``var``.
    "apex": (_TYPED_DECLARATION,),
    "cpp": (_CPP_DECLARATION,),
    "csharp": _C_FAMILY,
    "dart": _DART_FAMILY,
    "go": _GO_FAMILY,
    "java": (
        *_C_FAMILY,
        _JAVA_PRIMITIVE_DECLARATION,
        _JAVA_VARARGS_DECLARATION,
        _JAVA_PATTERN_VARIABLE,
    ),
    "kotlin": _KT_FAMILY,
    "python": _PY_FAMILY,
    "rust": (_RUST_ANNOTATED, _RUST_CONSTRUCTED),
    "swift": _SWIFT_FAMILY,
    "typescript": _TS_FAMILY,
}

RECEIVER_TYPE_LANGUAGES = frozenset(_LANGUAGE_PATTERNS)

# Languages where a field can be named with no qualifier, the only case where a
# class-scope declaration may answer for a bare receiver. Python is absent: it
# writes ``self.foo.bar()``, a dotted receiver the grammar mints no call site
# for, so class scope could only bind a bare local to a field. Membership rests
# on the scan actually finding typed fields in the language's idiom.
IMPLICIT_FIELD_LANGUAGES = frozenset({"apex", "csharp", "dart", "java", "kotlin", "swift"})

# Languages whose scan keeps a declaration of a builtin type, marked external,
# so the resolver can refuse a call on it instead of matching the method name.
# Only where nothing in the repo can add a method to a builtin type: C#
# extension methods, Kotlin and Swift extensions and Rust trait impls all can.
EXTERNAL_TYPE_LANGUAGES = frozenset({"java"})
# Those, and Kotlin, whose scan keeps the marked declaration without refusing:
# a call on it may still reach a repo extension of the builtin. Apex keeps it so
# a builtin-typed local still reads as a variable and types overload arguments.
_MARKED_BUILTIN_LANGUAGES = EXTERNAL_TYPE_LANGUAGES | {"kotlin", "apex"}
_EXTERNAL_TYPE = "external:"

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
)

# Every parameter of any `def` or `lambda` in the span, the enclosing one
# included: its signature starts its own span. Each lowercase identifier the
# list binds counts; an annotation or a default binds nothing.
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

# The TypeScript shapes that bind a name, typed or not. Every identifier in a
# destructuring pattern counts, a renamed key included; a parameter's type
# annotation and default do not. Ceiling: a parameter list holding
# parentheses (a default that calls, a function type) is not read.
_TS_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_TS_BINDINGS = (
    re.compile(r"(?<![\w$.])(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"),
    re.compile(r"(?<![\w$.])(?:function\*?|class)\s+(?P<name>[A-Za-z_$][\w$]*)"),
    # A lone arrow parameter; ``): Foo =>`` is a return type, not one.
    re.compile(r"(?<![\w$.:])(?<!:\s)(?P<name>[A-Za-z_$][\w$]*)\s*=>"),
    # An assignment at the start of a statement, plain or compound.
    re.compile(
        r"(?m)^[ \t]*(?P<name>[A-Za-z_$][\w$]*)\s*"
        r"(?:[-+*/%|&^]|\*\*|\?\?|\|\||&&|<<|>>>?)?=(?![=>])"
    ),
)
_TS_TARGET_LISTS = (
    # Lazy, so a pattern ends at its own bracket, not at one lines later.
    re.compile(
        r"(?<![\w$.])(?:const|let|var)\s*(?P<lhs>\{[^;=]*?\}|\[[^;=]*?\])\s*(?:=|of\b|in\b)"
    ),
    # A parameter list: a function, a method, an arrow, a catch clause. Not
    # one after a colon: ``cb: (x: T) => void`` is a function type.
    re.compile(
        r"(?<!:)(?<!:\s)(?P<head>[\w$]*)\s*(?P<lhs>\([^()]*\))\s*(?::[^=;{}()]*?)?\s*(?:=>|\{)"
    ),
)
# Heads whose parenthesised part is a condition, not a parameter list.
_TS_CONDITION_HEADS = frozenset({"if", "for", "while", "switch", "with", "return", "await"})


def _named_bindings(patterns: Iterable[re.Pattern[str]], cleaned: str) -> set[tuple[int, str]]:
    """``(offset, name)`` for each pattern's ``name`` group."""
    return {
        (match.start("name"), match.group("name"))
        for pattern in patterns
        for match in pattern.finditer(cleaned)
    }


def _without_annotations(names: str) -> str:
    """*names* with each top-level item's annotation and default blanked.

    ``(a: Foo, b = make)`` binds ``a`` and ``b``, never ``Foo`` or ``make``: a
    bare call to either is still the module's. Blanked, not cut, so every name
    kept stays at its offset. A bracket opens a nested pattern or a generic,
    whose commas and colons are not the list's own.
    """
    out: list[str] = []
    depth = 0
    blanking = False
    for ch in names:
        if ch in "{[<":
            depth += 1
        elif ch in "}]>":
            depth = max(depth - 1, 0)
        elif depth == 0 and ch in ":=":
            blanking = True
        elif depth == 0 and ch == ",":
            blanking = False
        out.append(" " if blanking and ch != "\n" else ch)
    return "".join(out)


def _listed_bindings(
    patterns: Iterable[re.Pattern[str]],
    identifier: re.Pattern[str],
    cleaned: str,
) -> set[tuple[int, str]]:
    """``(offset, name)`` for every identifier each pattern's ``lhs`` list binds."""
    found: set[tuple[int, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            # Only TypeScript's lists carry a ``head`` to refuse.
            if match.groupdict().get("head") in _TS_CONDITION_HEADS:
                continue
            offset = match.start("lhs")
            for name in identifier.finditer(_without_annotations(match.group("lhs"))):
                found.add((offset + name.start(), name.group()))
    return found


def _python_target_bindings(cleaned: str) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for pattern in _PY_TARGET_LISTS:
        for match in pattern.finditer(cleaned):
            for target in match.group("lhs").split(","):
                # `self.x = ...` and `d[k] = ...` bind no bare name.
                name = target.strip()
                if _PY_IDENTIFIER.match(name):
                    found.add((match.start("lhs"), name))
    return found


# The languages ``scan_bindings`` reads: only there can a scope be shown not
# to bind a name. JavaScript binds with TypeScript's shapes, less annotations.
BINDING_LANGUAGES = frozenset({"javascript", "python", "typescript"})
_BINDING_SCAN_AS = {"javascript": "typescript"}

# Names a body binds whatever their type: an untyped local, a loop variable, a
# lambda or closure parameter. The scan sees only some of them, so these never
# prove a name unbound; they only refuse a field of the same name, which class
# scope would otherwise answer for (`{ api -> api.m() }` is not the field).
# `(named, listed)` shapes; a lambda's list stays on its opening line.
_JAVA_LAMBDA = (
    re.compile(r"(?<![\w.])var\s+(?P<name>[a-z_]\w*)"),
    re.compile(r"(?<![\w.])(?P<name>[a-z_]\w*)\s*->"),
), (re.compile(r"\((?P<lhs>[\w\s,]*)\)\s*->"),)
_LOCAL_NAME_SHAPES: dict[str, tuple[tuple[re.Pattern[str], ...], ...]] = {
    "csharp": (
        (
            re.compile(r"(?<![\w.])var\s+(?P<name>[a-z_]\w*)"),
            re.compile(r"(?<![\w.])(?P<name>[a-z_]\w*)\s*=>"),
        ),
        (re.compile(r"\((?P<lhs>[\w\s,]*)\)\s*=>"),),
    ),
    "dart": (
        (
            re.compile(
                r"(?<![\w.])(?:final|var|const)\s+(?P<name>[a-z_]\w*)\b(?=\s*(?:[=;,]|in\b))"
            ),
        ),
        # A function literal's list follows punctuation, never a name or `if`.
        (
            re.compile(
                r"(?m)(?:(?<=[(,=:>])|^)\s*\((?P<lhs>[\w\s,]*)\)\s*(?:async\s*)?(?:=>|\{)"
            ),
        ),
    ),
    "java": _JAVA_LAMBDA,
    "kotlin": (
        (
            re.compile(r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)"),
            re.compile(r"\bfor\s*\(\s*(?P<name>[a-z_]\w*)\s+in\b"),
        ),
        (re.compile(r"\{[ \t]*(?P<lhs>[\w \t,:?()]*?)->"),),
    ),
    "swift": (
        # `guard let client else` and `if let client = self.client {` unwrap
        # the field itself, so the name keeps the field's type.
        (
            re.compile(
                r"(?<![\w.])(?:let|var)\s+(?P<name>[a-z_]\w*)\b"
                r"(?!\s*(?:=\s*(?:self\.)?(?P=name)\s*)?(?:[,{]|else\b))"
            ),
        ),
        (
            re.compile(r"\{[ \t]*(?:\[[^\]\n]*\][ \t]*)?(?P<lhs>[\w \t,:?()]*?)[ \t]+in\b"),
            re.compile(r"\bfor\s+(?:case\s+(?:let\s+)?)?(?P<lhs>\([^()\n]*\)|[a-z_]\w*)\s+in\b"),
        ),
    ),
}


def scan_bindings(text: str, language: str) -> tuple[tuple[int, str], ...]:
    """Every ``(line, name)`` *text* binds, in line order."""
    return tuple(sorted({(line, name) for line, _, name in scan_binding_positions(text, language)}))


def scan_binding_positions(text: str, language: str) -> tuple[tuple[int, int, str], ...]:
    """Every ``(line, column, name)`` *text* binds, in order.

    Ceiling: a block comment earlier on the same line shifts the column, since
    comments are blanked without keeping their width.
    """
    local = _LOCAL_NAME_SHAPES.get(language)
    if language not in BINDING_LANGUAGES and local is None:
        return ()
    language = _BINDING_SCAN_AS.get(language, language)
    cleaned = _without_comments(text, language)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    if local is not None:
        found = _named_bindings(local[0], cleaned) | _listed_bindings(local[1], _PY_NAME, cleaned)
    elif language == "typescript":
        found = _named_bindings(_TS_BINDINGS, cleaned)
        found |= _listed_bindings(_TS_TARGET_LISTS, _TS_IDENTIFIER, cleaned)
    else:
        found = _python_target_bindings(cleaned)
        found |= _named_bindings(_PY_BINDINGS, cleaned)
        found |= _listed_bindings(_PY_PARAMETER_LISTS, _PY_NAME, cleaned)
    positions = set()
    for offset, name in found:
        line = bisect_right(starts, offset)
        positions.add((line, offset - starts[line - 1], name))
    return tuple(sorted(positions))


# Languages where a parameter or local hides a bare call of its name from every
# repo symbol of that name. Kotlin, Swift and Dart read their own shapes below.
CALL_SHADOW_LANGUAGES = BINDING_LANGUAGES | {"dart", "kotlin", "swift"}

# Kotlin resolves `f()` past a binding that cannot be invoked, so only a
# function-typed one hides `f`: `onClick: () -> Unit`, `r: Call.() -> T`,
# `val f = { .. }`. Ceiling: a class type with `operator fun invoke` is missed.
_KT_INVOKABLE = (
    re.compile(
        r"(?<![\w.@])(?P<name>[a-z_]\w*)\s*:\s*(?:@\w+(?:\([^()\n]*\))?\s+)*"
        r"(?:suspend\s+)?(?:[\w.]+(?:<[^\n]*?>)?\??\.)?\("
    ),
    re.compile(r"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*=\s*\{"),
)
_SWIFT_INVOKABLE = re.compile(
    r"(?<![\w.])(?:let|var)\s+(?P<name>[a-z_]\w*)\s*(?::\s*(?:@\w+\s+)*\(|=\s*\{)"
)


def holds_function(declaration: str, name: str, language: str) -> bool:
    """Does Kotlin or Swift *declaration* give property *name* a function value?

    Read with comments and string literals blanked, so text inside them never
    looks like a function type.
    """
    code = [" "] * len(declaration)
    quotes = "\"'" if language == "kotlin" else '"'
    for index, char, _ in scan_code(declaration, quotes=quotes, text_blocks=True):
        code[index] = char
    masked = "".join(code)
    shapes = _KT_INVOKABLE if language == "kotlin" else (_SWIFT_INVOKABLE,)
    return any(match.group("name") == name for shape in shapes for match in shape.finditer(masked))


# Where a declared parameter list opens. Dart's header is told from a call by
# the body that follows its list (`_DART_BODY`).
_GENERIC = r"(?:<[^<>\n]*(?:<[^<>\n]*>[^<>\n]*)*>)?"
_SWIFT_HEADER = re.compile(rf"(?<![\w.])(?:func\s+[^\s(<]+|init[?!]?|subscript)\s*{_GENERIC}\s*\(")
_DART_HEADER = re.compile(rf"(?<![\w$.])(?P<fn>[A-Za-z_$][\w$]*)\s*{_GENERIC}\s*\(")
_DART_BODY = re.compile(r"\s*(?:async\*?|sync\*)?\s*(?:\{|=>)")
_DART_NOT_HEADERS = frozenset({"if", "for", "while", "switch", "catch", "super", "this", "assert"})
_GROUP_BRACKETS = str.maketrans("{}[]", "    ")
_TRAILING_NAME = re.compile(r"(?P<name>[A-Za-z_$][\w$]*)\s*$")


def _parameter_pieces(cleaned: str, open_paren: int) -> tuple[list[tuple[int, str]], int]:
    """``(offset, text)`` of each top-level parameter in the list at *open_paren*, and its end.

    A Dart `{..}` / `[..]` optional group at the top is read through, and an
    arrow (`->`, `=>`) closes nothing.
    """
    pieces: list[tuple[int, str]] = []
    depth, start, index = 0, open_paren + 1, open_paren + 1
    while index < len(cleaned):
        char = cleaned[index]
        if char in "(<" or (char in "{[" and depth):
            depth += 1
        elif char == ">" and cleaned[index - 1] in "-=":
            pass
        elif char in ")>" or (char in "}]" and depth):
            if not depth and char == ")":
                pieces.append((start, cleaned[start:index]))
                return pieces, index
            depth = max(depth - 1, 0)
        elif char == "," and not depth:
            pieces.append((start, cleaned[start:index]))
            start = index + 1
        index += 1
    return pieces, index


def _swift_parameter(piece: str) -> str | None:
    """The name a Swift parameter binds in the body: the last word before its type."""
    words = piece.partition(":")[0].split()
    return words[-1] if words and words[-1] != "_" and words[-1].isidentifier() else None


def _dart_parameter(piece: str) -> str | None:
    """The name a Dart parameter binds; a `this.x` / `super.x` one binds no new name."""
    # An optional group's brackets (`{required T name}`, `[T name]`) are not the name.
    head = re.split(r"[=:]", piece.translate(_GROUP_BRACKETS), maxsplit=1)[0].strip()
    if re.search(r"\b(?:this|super)\.", head):
        return None
    found = _TRAILING_NAME.search(head)
    return found.group("name") if found else None


def _listed_parameters(
    cleaned: str, header: re.Pattern[str], name_of: Callable[[str], str | None], body: bool
) -> set[tuple[int, str]]:
    found: set[tuple[int, str]] = set()
    for match in header.finditer(cleaned):
        if body and match.group("fn") in _DART_NOT_HEADERS:
            continue
        pieces, end = _parameter_pieces(cleaned, match.end() - 1)
        if body and not _DART_BODY.match(cleaned, end + 1):
            continue
        for offset, piece in pieces:
            name = name_of(piece)
            if name is not None:
                found.add((offset + piece.find(name), name))
    return found


def scan_call_shadows(text: str, language: str) -> tuple[tuple[int, str], ...]:
    """Every ``(line, name)`` binding in *text* that hides a bare call of that name.

    Everything ``scan_bindings`` reads, but for Kotlin only invokable bindings,
    and Swift and Dart add their declared parameters.
    """
    if language in BINDING_LANGUAGES:
        return scan_bindings(text, language)
    if language not in CALL_SHADOW_LANGUAGES:
        return ()
    cleaned = _without_comments(text, language)
    if language == "kotlin":
        found = _named_bindings(_KT_INVOKABLE, cleaned)
    elif language == "swift":
        found = _listed_parameters(cleaned, _SWIFT_HEADER, _swift_parameter, body=False)
    else:
        found = _listed_parameters(cleaned, _DART_HEADER, _dart_parameter, body=True)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    lines = {(bisect_right(starts, offset), name) for offset, name in found}
    if language != "kotlin":
        lines.update(scan_bindings(text, language))
    return tuple(sorted(lines))


def dart_named_constructors(body: str, class_name: str) -> frozenset[str]:
    """The names *class_name*'s Dart *body* declares constructors under: ``Foo.named(``.

    Read where a declaration starts a line, with comments blanked. Ceiling: a
    body-internal ``Foo.x(..)`` call at a line's start reads as one, which a
    valid program only writes for a constructor or a static of the class.
    """
    declared = re.compile(
        rf"^[ \t]*(?:(?:const|factory|external)\s+)*{re.escape(class_name)}\s*\.\s*(\w+)\s*\(",
        re.MULTILINE,
    )
    return frozenset(declared.findall(_without_comments(body, "dart")))


# What a scope binds beyond its positional bindings. A TypeScript/JavaScript
# ``function`` declaration is hoisted, so it binds from the top of its scope
# whatever its line. A Python ``global`` name is the module's in that scope,
# however the body assigns it.
_HOISTED = {"typescript": re.compile(r"(?<![\w$.])function\*?\s+(?P<name>[A-Za-z_$][\w$]*)")}
_ESCAPES = {"python": re.compile(r"\bglobal\s+(?P<names>\w+(?:\s*,\s*\w+)*)")}


class ScopeMarks(NamedTuple):
    """Names a scope treats specially, each in line order.

    A hoisted entry is ``(line, name, brace depth)``: a declaration hoists
    only into the function whose body holds it directly, one brace below the
    line that function starts on (``line_depths``, indexed by line). A block
    declaration is block-scoped in a module, so it hoists nowhere.
    """

    hoisted: tuple[tuple[int, str, int], ...]
    escaped: tuple[tuple[int, str], ...]
    line_depths: tuple[int, ...]


def _brace_depths(cleaned: str) -> tuple[list[int], list[int]]:
    """Brace depth at every offset of *cleaned*, and at each line's first character."""
    at: list[int] = []
    lines = [0]
    depth = 0
    seen_code = False
    for ch in cleaned:
        at.append(depth)
        if ch == "\n":
            lines.append(depth)
            seen_code = False
            continue
        if not seen_code and not ch.isspace():
            lines[-1] = depth
            seen_code = True
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
    return at, lines


def scan_scope_marks(text: str, language: str) -> ScopeMarks:
    """The hoisted and module-escaped names *text* declares."""
    language = _BINDING_SCAN_AS.get(language, language)
    hoisted, escapes = _HOISTED.get(language), _ESCAPES.get(language)
    if hoisted is None and escapes is None:
        return ScopeMarks((), (), ())
    cleaned = _without_comments(text, language)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    found_hoisted: set[tuple[int, str, int]] = set()
    line_depths: list[int] = []
    if hoisted is not None:
        at, line_depths = _brace_depths(cleaned)
        found_hoisted = {
            (bisect_right(starts, match.start("name")), match.group("name"), at[match.start()])
            for match in hoisted.finditer(cleaned)
        }
    found_escaped = {
        (bisect_right(starts, match.start("names")), name.strip())
        for match in (escapes.finditer(cleaned) if escapes else ())
        for name in match.group("names").split(",")
    }
    return ScopeMarks(
        tuple(sorted(found_hoisted)), tuple(sorted(found_escaped)), (0, *line_depths)
    )


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
    "dart": _BLOCK_COMMENT,
    "kotlin": _BLOCK_COMMENT,
    "typescript": _BLOCK_COMMENT,
}

_LANGUAGE_COMMENTS: dict[str, re.Pattern[str]] = {
    "apex": _LINE_COMMENT,
    "cpp": _LINE_COMMENT,
    "csharp": _LINE_COMMENT,
    "dart": _LINE_COMMENT,
    "go": _LINE_COMMENT,
    "java": _LINE_COMMENT,
    "kotlin": _LINE_COMMENT,
    "rust": _LINE_COMMENT,
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
    method (``self.x = T()``, a constructor parameter property) or, in Go, a
    struct field, which no closer tells from a local. It belongs
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
# semicolon-free TypeScript ``x: T`` are fields too. Kotlin and Swift keep a
# closer only on a `val`/`var`/`let` match, so no parameter reaches here: a
# Kotlin constructor property (`class R @Inject constructor(private val api:
# Api)`) closes on `,` or `)`, a Swift computed property on `{`.
_LANGUAGE_FIELD_CLOSERS: dict[str, frozenset[str]] = {
    "kotlin": frozenset({"=", "\n", ",", ")"}),
    "python": frozenset({"=", "\n"}),
    "swift": frozenset({"=", "\n", "{"}),
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


def _is_java_array(raw: str, language: str) -> bool:
    return language == "java" and raw.endswith(("]", "..."))


def _usable_type_name(raw: str, language: str) -> tuple[str | None, bool]:
    """``(bare name, unwrapped)`` for *raw*, or ``(None, False)``.

    Only C++ looks inside the spelling: ``shared_ptr<Foo>`` is a ``Foo`` behind
    the arrow. Elsewhere the generic head is the value's real type. A C# head
    keeps its arity, ``IFoo`1``, since a same-file ``IFoo`` may be another type;
    the lookup falls back to the bare name. A Java ``Token.Tag`` keeps its
    outer type, which picks the member type the lookup means.
    """
    if _nests_in_a_builtin(raw, language) or _is_java_array(raw, language):
        return None, False
    inner = unwrap_pointer_like(raw) if language == "cpp" else None
    name = inner or (raw if raw.isidentifier() else bare_type_name(raw))
    if not is_resolvable_type_name(name, language) and not _apex_member_of_repo_type(raw, language):
        return None, False
    return written_type_key(name, raw, language), inner is not None


def _apex_member_of_repo_type(raw: str, language: str) -> bool:
    """``LoggerPlugin.Batchable``: a member type named like a builtin, qualified by a repo type."""
    qualifier = type_qualifier(raw) if language == "apex" else None
    return bool(qualifier) and is_resolvable_type_name(bare_type_name(qualifier), language)


# Languages whose constructor parameter lists sit at class scope, unextracted,
# with defaults closing on `=`: a declaration inside parentheses owns no field.
_PARAMETERS_AT_CLASS_SCOPE = frozenset({"dart"})
_PARENTHESIS = re.compile(r"[()]")


def _inside_parentheses(cleaned: str) -> Callable[[int], bool]:
    """Is an offset of *cleaned* inside a parenthesis? Strings are not skipped."""
    offsets: list[int] = []
    depths: list[int] = []
    depth = 0
    for match in _PARENTHESIS.finditer(cleaned):
        depth += 1 if match.group() == "(" else -1
        offsets.append(match.start())
        depths.append(depth)

    def inside(offset: int) -> bool:
        index = bisect_left(offsets, offset) - 1
        return index >= 0 and depths[index] > 0

    return inside


# Kotlin and Swift run code at class scope that is not a function: an ``init``
# block, a lambda or closure a property holds. A declaration there is a local
# of that block, so only one directly in a type's body may be a field.
_TYPE_BODY_SCOPED_LANGUAGES = frozenset({"kotlin", "swift"})
_BRACE = re.compile(r"[{};]")
# A function, accessor or initialiser declared after a bodiless type.
_CODE_HEADER = re.compile(r"\b(?:fun|func|init|get|set)\b")
_TYPE_HEADER = re.compile(r"\b(?:class|object|interface|struct|enum|extension|protocol|actor)\b")


def _opens_type_body(header: str) -> bool:
    """Does a ``{`` after *header* open a type's body?

    The last type keyword decides, unless an expression leads into it:
    ``val f = object : T {`` is an anonymous object, not a declared type.
    """
    keywords = list(_TYPE_HEADER.finditer(header))
    if not keywords:
        return False
    before = header[: keywords[-1].start()].rstrip()
    after = header[keywords[-1].end() :]
    return not before.endswith(("=", "(", ",", "return")) and not _CODE_HEADER.search(after)


def _in_type_body(cleaned: str) -> Callable[[int], bool]:
    """Is the innermost ``{`` open at an offset of *cleaned* a type's body (or none)?

    None counts: a primary constructor's ``val`` parameters sit before any brace.

    A brace opens a type body when the text since the last ``{``, ``}`` or
    ``;`` declares a type (see ``_opens_type_body``), so ``val f = {`` and
    ``init {`` are not one. Strings are not skipped.
    """
    offsets: list[int] = []
    innermost: list[bool] = []
    stack: list[bool] = []
    segment_start = 0
    for match in _BRACE.finditer(cleaned):
        char = match.group()
        if char == "{":
            stack.append(_opens_type_body(cleaned[segment_start : match.start()]))
        elif char == "}" and stack:
            stack.pop()
        segment_start = match.end()
        offsets.append(match.start())
        innermost.append(stack[-1] if stack else True)

    def inside(offset: int) -> bool:
        index = bisect_left(offsets, offset) - 1
        return index < 0 or innermost[index]

    return inside


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
    inside = _inside_parentheses(cleaned) if language in _PARAMETERS_AT_CLASS_SCOPE else None
    in_type_body = _in_type_body(cleaned) if language in _TYPE_BODY_SCOPED_LANGUAGES else None
    for pattern in patterns:
        for match in pattern.finditer(cleaned):
            if pattern is _KT_CONSTRUCTED_LAMBDA and not _ends_statement(
                cleaned, _lambda_end(cleaned, match.end()), language
            ):
                continue
            raw = match.group("type")
            if raw not in resolved:
                resolved[raw] = _usable_type_name(raw, language)
                if resolved[raw][0] is None and language in _MARKED_BUILTIN_LANGUAGES:
                    resolved[raw] = (_builtin_type_name(raw, language), False)
            type_name, unwrapped = resolved[raw]
            if type_name is None:
                continue
            groups = match.groupdict()
            # A keyword-capable shape without its keyword cannot own a field
            # (see `_KT_ANNOTATED`).
            closer = groups.get("closer") or ""
            if "keyword" in groups and not groups["keyword"]:
                closer = ""
            if inside is not None and inside(match.start()):
                closer = ""
            if in_type_body is not None and not in_type_body(match.start()):
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


def _builtin_type_name(raw: str, language: str) -> str | None:
    """The marked builtin *raw* spells, ``Map.Entry`` for a member type, or None.

    Kept where ``_usable_type_name`` drops it, so a call on the value can be
    refused rather than left to name matching. A generic parameter (``T``) or
    any other unknown name stays dropped: unknown is not external.
    """
    if _is_java_array(raw, language):
        # Any array is a builtin, ``Foo[]`` too, so no call on it reaches ``Foo``.
        dimensions = raw.replace(" ", "").count("[]") + raw.endswith("...")
        element = strip_type_arguments(raw.removesuffix("...")).strip()
        return _EXTERNAL_TYPE + element + "[]" * dimensions
    if _nests_in_a_builtin(raw, language):
        return _EXTERNAL_TYPE + strip_type_arguments(raw).strip()
    name = raw if raw.isidentifier() else bare_type_name(raw)
    return _EXTERNAL_TYPE + name if name in get_builtin_types(language) else None


def external_type_name(type_name: str | None) -> str | None:
    """The builtin a marked type names, or None for any other type."""
    if type_name is None or not type_name.startswith(_EXTERNAL_TYPE):
        return None
    return type_name[len(_EXTERNAL_TYPE) :]


class CallAssignment(NamedTuple):
    """``name := receiver.target(..)`` at one line, typed later by the resolver."""

    line: int
    name: str
    receiver: str | None
    target: str
    # Python ``x = await f()``: an ``async def`` yields its annotation only then.
    awaited: bool = False


# The call a local is assigned from: ``[receiver.]target(``. A Kotlin, Swift or
# Dart local annotated with its type is typed by its declaration instead.
_CALL_HEAD = r"(?:(?P<receiver>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.)?(?P<target>[A-Za-z_]\w*)\s*\("

# ``x = f(..)``, ``x = await obj.f(..)``: a plain statement-start assignment
# that ends its line. The resolver refuses a name the body also binds elsewhere.
_PY_CALL_ASSIGNMENT = re.compile(
    rf"(?m)^[ \t]*(?P<name>[a-z_]\w*)[ \t]*=[ \t]*(?P<awaited>await[ \t]+)?{_CALL_HEAD}"
)

# ``val x = f(..)``, ``let x = try await obj.f(..)``, ``final x = await f(..);``.
# Swift's ``try?`` value still calls the callee type's methods. A Dart
# ``await`` unwraps the ``Future`` the callee declares.
_KT_CALL_ASSIGNMENT = re.compile(rf"(?<![\w.])va[lr]\s+(?P<name>[a-z_]\w*)\s*=\s*{_CALL_HEAD}")
_SWIFT_CALL_ASSIGNMENT = re.compile(
    rf"(?<![\w.])(?:let|var)\s+(?P<name>[a-z_]\w*)\s*=\s*(?:try[?!]?\s+)?(?:await\s+)?{_CALL_HEAD}"
)
_DART_CALL_ASSIGNMENT = re.compile(
    rf"(?<![\w.])(?:final|var|const)\s+(?P<name>[a-z_]\w*)\s*=\s*(?P<awaited>await\s+)?"
    rf"(?:(?:new|const)\s+)?{_CALL_HEAD}"
)

_CALL_ASSIGNMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "dart": _DART_CALL_ASSIGNMENT,
    "go": _GO_CALL_DECL,
    "kotlin": _KT_CALL_ASSIGNMENT,
    "python": _PY_CALL_ASSIGNMENT,
    "swift": _SWIFT_CALL_ASSIGNMENT,
}

# Languages whose locals may be typed from a call's declared return type.
CALL_TYPED_LANGUAGES = frozenset(_CALL_ASSIGNMENT_PATTERNS)

_QUOTES = frozenset("\"'`")
_STATEMENT_ENDS = frozenset("\n;}")
# A Python ``;`` may rebind the name later on the line, where no scan sees it.
# Dart ends a statement only at ``;``, so ``f()\n.g()`` is one statement.
_LANGUAGE_STATEMENT_ENDS = {"python": frozenset("\n"), "dart": frozenset(";")}
# Kotlin and Swift may carry a statement onto a line that starts with a member
# access or an operator (``.f()``, ``?: x``, ``as T``, ``&& y``). Refused
# wholesale: a new statement starting so only costs the local its type.
_NEXT_LINE_CONTINUES = {
    language: re.compile(r"[.?:+\-*/%&|=!<>]|(?:as|is)\b") for language in ("kotlin", "swift")
}
_SWIFT_OPTIONAL_BINDING_PREFIX = re.compile(r"(?:\b(?:if|guard|while)|,)\s*$")
_SWIFT_BINDING_ENDS = re.compile(r"[{,]|\belse\b")


def _call_end(text: str, index: int) -> int:
    """Index just past the ``)`` closing the ``(`` before *index*, or -1."""
    depth = 1
    while index < len(text):
        char = text[index]
        if char in _QUOTES:
            # A backquoted string has no escapes; the other two do.
            index += 1
            while index < len(text) and text[index] != char:
                index += 2 if char != "`" and text[index] == "\\" else 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _lambda_end(text: str, index: int) -> int:
    """Index just past the ``}`` closing the ``{`` at *index*, or -1."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char in _QUOTES:
            index += 1
            while index < len(text) and text[index] != char:
                index += 2 if char != "`" and text[index] == "\\" else 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _after_trailing_lambda(text: str, end: int) -> int:
    """*end*, or past a Kotlin trailing lambda that starts on the same line.

    ``f(a) { .. }`` is still the call ``f``: the lambda is its last argument.
    """
    brace = end
    while brace < len(text) and text[brace] in " \t":
        brace += 1
    if brace < len(text) and text[brace] == "{":
        return _lambda_end(text, brace)
    return end


def _call_ends_statement(cleaned: str, opened: int, language: str, start: int = 0) -> bool:
    """Does the call whose ``(`` ends just before *opened* end its statement?"""
    end = _call_end(cleaned, opened)
    if end >= 0 and language == "kotlin":
        end = _after_trailing_lambda(cleaned, end)
    return _ends_statement(cleaned, end, language, start)


def _ends_statement(cleaned: str, end: int, language: str, start: int = 0) -> bool:
    """Does the expression ending at *end* end its statement (-1: it never ends)?"""
    if end < 0:
        return False
    rest = cleaned[end:].lstrip(" \t\n" if language == "dart" else " \t")
    if (
        language == "swift"
        and _SWIFT_OPTIONAL_BINDING_PREFIX.search(cleaned[:start])
        and _SWIFT_BINDING_ENDS.match(rest)
    ):
        return True
    if rest and rest[0] not in _LANGUAGE_STATEMENT_ENDS.get(language, _STATEMENT_ENDS):
        return False
    continues = _NEXT_LINE_CONTINUES.get(language)
    return continues is None or not continues.match(rest.lstrip())


def scan_call_assignments(text: str, language: str) -> tuple[CallAssignment, ...]:
    """Every local *text* declares from a single call, in line order.

    The call must be the whole right-hand side: ``x := f().g()`` or
    ``x := f() + 1`` gives ``x`` no type from ``f``.
    """
    pattern = _CALL_ASSIGNMENT_PATTERNS.get(language)
    if pattern is None:
        return ()
    cleaned = _without_comments(text, language)
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    found: list[CallAssignment] = []
    for match in pattern.finditer(cleaned):
        if not _call_ends_statement(cleaned, match.end(), language, match.start()):
            continue
        found.append(
            CallAssignment(
                bisect_right(starts, match.start()),
                match.group("name"),
                match.group("receiver"),
                match.group("target"),
                bool(match.groupdict().get("awaited")),
            )
        )
    return tuple(found)


def record_type(types: dict[str, str | None], name: str, type_name: str) -> None:
    """``_record`` for a type found outside ``scan_declarations``."""
    _record(types, Declaration(0, name, type_name))


def _record(types: dict[str, str | None], declaration: Declaration) -> None:
    """Add one declaration to a scope, or mark the name unanswerable.

    A name declared with two types maps to ``None`` rather than being dropped:
    only "says nothing" may fall through to a wider scope, not "says something
    unusable".

    A builtin type is the weakest answer: it fills only a name nothing else
    types, so every non-builtin reading is what it was without it.
    """
    name, type_name = declaration.name, declaration.type_name
    if name not in types or (
        external_type_name(types[name]) is not None and external_type_name(type_name) is None
    ):
        types[name] = type_name
    elif external_type_name(type_name) is None and types[name] != type_name:
        types[name] = None


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
RANGE_LANGUAGES = frozenset({"go", "dart"})

# What ranging over each container shape yields, the group name giving the
# position: ``[]T``, ``[N]T`` and a variadic ``...T`` an int index, then a
# ``T``; ``map[K]V`` a ``K``, then a ``V``; ``chan T`` a ``T`` alone. A pointer
# element is still a ``T``. Anything else, a named container type included,
# yields nothing, so its range variables stay untyped. Ceiling: a pointer to an
# array (``*[N]T``, rangeable in Go) is not a shape here either.
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


# Dart's ``for (final p in head[.member[()]])``, and ``await for`` over a
# stream, declare one variable with no type spelled: it is the element of the
# container walked. A written type (``for (Repo p in xs)``) is read by
# ``_DART_TYPED`` and never reaches here, since ``Repo`` is no ``[a-z_]`` name.
# The variable is the clause's *value*; Dart has no key position.
_DART_NAME = r"[a-z_]\w*"
_DART_TYPE_NAME = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?"

# ``List<T>``, ``Iterable<T>``, ``Set<T>``, ``Queue<T>`` and ``Stream<T>`` yield a
# ``T``, nullable element included. A ``Map`` is not iterable, and a nested
# generic (``List<List<T>>``) or an un-awaited ``Future<List<T>>`` is no shape
# here, so each yields nothing.
_DART_ITERABLE = r"(?:List|Iterable|Set|Queue|Stream)"
_DART_ELEMENT_SHAPES = (
    re.compile(rf"{_DART_ITERABLE}\s*<\s*(?P<value>{_DART_TYPE_NAME})\??\s*>"),
)

# The whole spelling, nested arguments too, so the element shape refuses it
# instead of the declaration going unseen.
_DART_CONTAINER = rf"{_DART_ITERABLE}\s*<[^<>\n]*(?:<[^<>\n]*>[^<>\n]*)*>"

# A parameter, a local or a field, ``List<T> name`` with an optional ``final``
# before it and ``?`` after the type. The closer is kept, as ``_DART_TYPED``
# keeps it: ``;`` or ``=`` makes a field at class scope, unless the match sits
# inside parentheses (``Repo({List<T> xs = const []})``), which owns none.
_DART_CONTAINER_DECLARATIONS = (
    re.compile(
        rf"(?<![\w.])(?P<type>{_DART_CONTAINER})\??\s+(?P<name>{_DART_NAME})\s*"
        rf"(?=(?P<closer>[=;,)}}]))"
    ),
)

_DART_FOR_IN = re.compile(
    rf"\bfor\s*\(\s*(?:final|var)\s+(?P<value>{_DART_NAME})\s+in\s+"
    rf"(?P<head>{_DART_NAME})(?:\.(?P<member>[A-Za-z_]\w*)(?P<call>\(\))?)?\s*\)"
)


class RangeClause(NamedTuple):
    """One ``for key, value := range head[.member[()]]`` at one line.

    ``value`` is empty when only one variable is declared; ``_`` names none.
    Dart's ``for (final value in head[.member[()]])`` fills ``value`` alone:
    it has no key position.
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
    if language == "dart":
        clause_pattern, declaration_patterns = _DART_FOR_IN, _DART_CONTAINER_DECLARATIONS
        inside = _inside_parentheses(cleaned)
    else:
        clause_pattern, declaration_patterns = _GO_RANGE, _GO_CONTAINER_DECLARATIONS
        inside = None
    clauses = tuple(
        RangeClause(
            bisect_right(starts, match.start()),
            match.groupdict().get("key") or "",
            match.groupdict().get("value") or "",
            match.group("head"),
            match.group("member") or "",
            bool(match.group("call")),
        )
        for match in clause_pattern.finditer(cleaned)
    )
    containers = sorted(
        Declaration(
            bisect_right(starts, match.start()),
            match.group("name"),
            match.group("type"),
            ""
            if inside is not None and inside(match.start())
            else (match.groupdict().get("closer") or ""),
            member=bool(match.groupdict().get("member")),
        )
        for pattern in declaration_patterns
        for match in pattern.finditer(cleaned)
    )
    return RangeScan(clauses, tuple(containers))


def range_element_types(
    spelling: str | None, language: str, external: frozenset[str] = frozenset()
) -> tuple[str | None, str | None] | None:
    """``(key type, value type)`` for ranging over *spelling*, or None if unknown.

    A position holding a predeclared type (an index's ``int``), or a type
    qualified by an *external* package (``os.FileInfo``, whose bare name a
    repo type may share), is None within a known shape.
    """
    if not spelling or language not in RANGE_LANGUAGES:
        return None

    def element(raw: str | None) -> str | None:
        if not raw or type_qualifier(raw) in external:
            return None
        return _usable_type_name(raw, language)[0]

    shapes = _DART_ELEMENT_SHAPES if language == "dart" else _GO_ELEMENT_SHAPES
    for shape in shapes:
        match = shape.fullmatch(spelling)
        if match is not None:
            groups = match.groupdict()
            return element(groups.get("key")), element(groups.get("value"))
    return None


def clauses_in_span(
    clauses: tuple[RangeClause, ...], start_line: int, end_line: int
) -> tuple[RangeClause, ...]:
    """The range clauses inside one function body, in line order."""
    first = bisect_left(clauses, start_line, key=lambda c: c.line)
    last = bisect_right(clauses, end_line, key=lambda c: c.line)
    return clauses[first:last]


# Dart's one-element ``Iterable`` callbacks. ``items.forEach((p) => p.m())``
# binds ``p`` to an element of ``items``, though no type is spelled. ``fold``
# leads with its accumulator and ``reduce`` and ``sort`` take two parameters,
# so none of those is read: only a callback of exactly one element is.
DART_CALLBACK_METHODS = (
    "forEach",
    "map",
    "where",
    "any",
    "every",
    "firstWhere",
    "lastWhere",
    "singleWhere",
    "expand",
)

# ``head[.member[()]].method((name) => ..)``, or ``((name) { .. })``: the
# function literal is the call's first argument and its list is one bare name.
# A written type (``(Repo p) => ..``) is ``_DART_TYPED``'s and never matches
# here. ``head`` follows no ``.``, so ``a.b.c.forEach`` is refused as the
# for-in clause refuses ``a.b.c``.
_DART_CALLBACK = re.compile(
    rf"(?<![\w.$])(?P<head>{_DART_NAME})"
    rf"(?:\??\.(?P<member>[A-Za-z_]\w*)(?P<call>\(\))?)?"
    rf"\??\.(?P<method>{'|'.join(DART_CALLBACK_METHODS)})\s*\(\s*"
    rf"\(\s*(?P<name>{_DART_NAME})\s*\)\s*(?:async\s*)?(?P<arrow>=>|\{{)"
)


class DartCallback(NamedTuple):
    """One function literal handed to an ``Iterable`` method, and where it lives.

    ``name`` is the literal's one parameter. ``start`` and ``end`` are the
    lines the literal covers, and ``begin`` and ``finish`` its offsets, which
    tell a literal nested in another from one beside it on the same line.
    ``head``, ``member`` and ``call`` read the receiver as a for-in clause does.
    """

    start: int
    end: int
    begin: int
    finish: int
    name: str
    head: str
    member: str
    call: bool
    method: str


def _skip_dart_string(text: str, at: int) -> int:
    """The offset just past the string literal opening at *at*, or -1 if unterminated.

    Interpolation is skipped with the string. Ceiling: a quote of the string's
    own kind inside an interpolation (``'${m['k']}'``) ends it early.
    """
    quote = text[at]
    closer = quote * 3 if text.startswith(quote * 3, at) else quote
    raw = at > 0 and text[at - 1] == "r"
    index = at + len(closer)
    while index < len(text):
        if text[index] == "\\" and not raw:
            index += 2
        elif text.startswith(closer, index):
            return index + len(closer)
        elif len(closer) == 1 and text[index] == "\n":
            return -1
        else:
            index += 1
    return -1


def _callback_end(cleaned: str, at: int, block: bool) -> int | None:
    """The offset just past the function literal whose body starts at *at*.

    A block body ends at the brace matching the one before *at*. An expression
    body ends at the comma or unmatched parenthesis that closes the argument.
    None where the brackets never balance, so the literal is dropped, not
    guessed at.
    """
    depth = 1 if block else 0
    index = at
    while index < len(cleaned):
        char = cleaned[index]
        if char in "'\"":
            index = _skip_dart_string(cleaned, index)
            if index < 0:
                return None
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if block and depth == 0:
                return index + 1
            if not block and depth < 0:
                return index
        elif not block and depth == 0 and char in ",;":
            return index
        index += 1
    return None


def scan_dart_callbacks(text: str) -> tuple[DartCallback, ...]:
    """Every one-element ``Iterable`` callback *text* passes, in source order."""
    cleaned = _without_comments(text, "dart")
    starts = [0, *(newline.end() for newline in _NEWLINE.finditer(cleaned))]
    found: list[DartCallback] = []
    for match in _DART_CALLBACK.finditer(cleaned):
        finish = _callback_end(cleaned, match.end(), match.group("arrow") == "{")
        if finish is None:
            continue
        found.append(
            DartCallback(
                bisect_right(starts, match.start("name")),
                bisect_right(starts, max(finish - 1, match.start("name"))),
                match.start("name"),
                finish,
                match.group("name"),
                match.group("head"),
                match.group("member") or "",
                bool(match.group("call")),
                match.group("method"),
            )
        )
    return tuple(found)


def callbacks_in_span(
    callbacks: tuple[DartCallback, ...], start_line: int, end_line: int
) -> tuple[DartCallback, ...]:
    """The callbacks whose parameter sits inside one function body, in source order."""
    return tuple(c for c in callbacks if start_line <= c.start <= end_line)


def callbacks_binding(
    callbacks: Iterable[DartCallback], name: str, line: int
) -> tuple[DartCallback, ...]:
    """The innermost callbacks that bind *name* on *line*.

    A literal nested in another covers the same lines, so the outer one is
    dropped. Two literals side by side on one line cannot be told apart by
    line alone, so both stay and the caller must agree between them.
    """
    covering = [c for c in callbacks if c.name == name and c.start <= line <= c.end]
    return tuple(
        c
        for c in covering
        if not any(o is not c and c.begin < o.begin and o.finish <= c.finish for o in covering)
    )
