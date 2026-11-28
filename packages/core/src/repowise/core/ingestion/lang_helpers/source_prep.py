"""Byte-preserving source sanitizers for grammar gaps in Pascal, Objective-C, Apex and C++."""

from __future__ import annotations

import re

from ..extractors.synthetic_symbols.cpp_macros import _FAST_REJECT_TOKENS as _SYNTHESIZED_MACROS
from .preproc_arms import _TOKEN_RE, blank_unbalanced_preproc_arms

_PASCAL_USES_IN_CLAUSE_RE = re.compile(rb"\bin\b[ \t]*'(?:[^'\r\n]|'')*'")


def _sanitize_pascal_project_source(source: bytes) -> bytes:
    """Blank Delphi/FPC project-file ``unit in 'path.pas'`` clauses.

    ``.dpr``/``.dpk``/``.lpr`` files map units to paths in the ``uses``
    clause (``uses MyUnit in 'src\\MyUnit.pas';``), and the Delphi IDE writes
    that form for every unit it adds. tree-sitter-pascal has no rule for it:
    error recovery folds the ``in``, the path and every later unit into one
    corrupt ``moduleName`` node, so the rest of the ``uses`` list is lost before
    any query runs. The regex keeps a doubled ``''`` inside the literal.
    """
    return _blank_matches(source, _PASCAL_USES_IN_CLAUSE_RE)


def _blank_matches(source: bytes, pattern: re.Pattern[bytes]) -> bytes:
    """Overwrite every match of *pattern* with spaces, keeping the length.

    The shared half of every byte-preserving source sanitizer: a construct the
    grammar has no rule for is replaced in place, never removed, so line
    numbers and byte offsets for everything else in the file survive.
    """
    return _blank_spans(source, [match.span() for match in pattern.finditer(source)])


_NOT_LINE_BREAK_RE = re.compile(rb"[^\r\n]")


def _blank_spans(source: bytes, spans: list[tuple[int, int]]) -> bytes:
    """Overwrite each ``(start, end)`` span with spaces, keeping the length
    and every line break inside it, so a span that crosses lines moves none."""
    if not spans:
        return source
    out = bytearray(source)
    for start, end in spans:
        out[start:end] = _NOT_LINE_BREAK_RE.sub(b" ", out[start:end])
    return bytes(out)


_PASCAL_PROJECT_EXTENSIONS = (".dpr", ".dpk", ".lpr")


def prepare_pascal_source(source: bytes, path: str | None) -> bytes:
    """Single entry point for every Pascal byte-preserving sanitizer.

    Called through :func:`~.sfc_source.prepare_source`, the hook the parser and
    the health walkers already run before parsing, so ``parser.py`` needs no
    Pascal branch. Gated on *path*'s extension because the ``in '...'`` syntax
    is invalid in a plain unit file.

    An anonymous ``array[...] of record`` element type still parses badly and
    is not blanked here: its ERROR spans can cover the class's own closing
    ``end;``, so blanking them breaks the structure they meant to fix. That
    case degrades to a wrong parent for the one class, like any grammar gap.
    """
    if path and path.lower().endswith(_PASCAL_PROJECT_EXTENSIONS):
        return _sanitize_pascal_project_source(source)
    return source


# Whole-line macros that expand to nothing a parser needs but which this
# grammar reads as the start of a C declaration, swallowing whatever follows
# into an ERROR node. Only bare whole-line forms are listed: a macro that
# opens a real declaration (``FOUNDATION_EXPORT NSString *const kFoo;``) must
# stay, and a call-shaped one (``NS_ENUM(NSInteger, Kind)``) is a different
# problem that blanking a line cannot fix.
_OBJC_BARE_MACRO_LINE_RE = re.compile(
    rb"^[ \t]*(?:NS_ASSUME_NONNULL_BEGIN|NS_ASSUME_NONNULL_END"
    rb"|NS_HEADER_AUDIT_BEGIN\([^)\r\n]*\)|NS_HEADER_AUDIT_END"
    rb"|CF_ASSUME_NONNULL_BEGIN|CF_ASSUME_NONNULL_END"
    rb"|NS_REFINED_FOR_SWIFT|NS_SWIFT_UI_ACTOR)[ \t]*(?=\r?\n|$)",
    re.MULTILINE,
)


# Availability / naming attributes written call-shaped after a declaration
# (``- (void)done NS_SWIFT_NAME(done())``). The grammar reads the macro as a
# declarator it cannot close, recovery fails to resync at the ``;``, and every
# later call in the file loses its enclosing method.
#
# Enumerated by name because ``NS_ENUM(NSInteger, Kind)`` and
# ``NSLog(@"%@", x)`` share the ``IDENT(...)`` shape. Only a form that ends a
# declaration qualifies: followed by ``;``, a body's ``{``, or end of line. The
# same name before a ``,`` or ``)`` is left alone.
_OBJC_TRAILING_MACRO_RE = re.compile(
    rb"(?<![A-Za-z0-9_])(?:AF_API_AVAILABLE|API_AVAILABLE|API_DEPRECATED"
    rb"|NS_SWIFT_NAME|NS_AVAILABLE|NS_DEPRECATED)"
    rb"\((?:[^()\r\n]|\([^()\r\n]*\))*\)[ \t]*(?=[;{]|\r?$)",
    re.MULTILINE,
)


def _blank_objc_trailing_macros(source: bytes) -> bytes:
    """Blank trailing call-shaped attribute macros, keeping the terminator.

    The ``;``/``{`` the pattern looks ahead to stays in place so the
    declaration itself still ends where it always did; only the macro's own
    span is overwritten. Same offset contract as every other sanitizer here.
    """
    return _blank_matches(source, _OBJC_TRAILING_MACRO_RE)


def prepare_objectivec_source(source: bytes) -> bytes:
    """Blank nullability-audit macros, bare and call-shaped, preserving offsets.

    ``NS_ASSUME_NONNULL_BEGIN`` is not a preprocessor directive and this
    grammar has no rule for it, so it parses as the opening of a C declaration
    and error recovery folds the whole ``@interface … @end`` block after it
    into one ERROR node. The macro wraps almost every modern Objective-C
    header, so this is the dominant parse failure in real source.

    The same failure has a second shape: a declaration whose *last* token is a
    call-shaped availability attribute (``API_AVAILABLE(ios(10))``,
    ``NS_SWIFT_NAME(...)``). There the recovery never resyncs at the closing
    ``;``, so one trailing attribute derails the remainder of the file. Both
    passes blank in place, so every offset outside a match is unchanged.
    """
    return _blank_objc_trailing_macros(_blank_matches(source, _OBJC_BARE_MACRO_LINE_RE))


# CumulusCI ``%%%NAMESPACE%%%`` tokens, rewritten at deploy; the grammar errors on them.
_APEX_NAMESPACE_TOKEN_RE = re.compile(rb"%%%[A-Z_]+%%%")


def prepare_apex_source(source: bytes) -> bytes:
    """Blank CumulusCI namespace tokens in place, preserving offsets."""
    return _blank_matches(source, _APEX_NAMESPACE_TOKEN_RE)


# ``class LEVELDB_EXPORT DB {``: tree-sitter-cpp reads the ALL-CAPS export
# macro as the class name and ``DB`` as a declarator, so the class node is
# lost and its first method is dropped. Group 1 is the macro run; it must be
# followed by the real name (template arguments allowed) and a body or base
# clause, so ``class DB {``, ``class DB final {`` and a bodyless forward
# declaration, which already parses, never match. Call-shaped forms like
# ``CAPABILITY("mutex")`` qualify too. The body brace must follow whitespace,
# so a brace-initialized variable (``struct TM tm{0};``) keeps its type.
# ``[[attr]]`` and ``alignas(..)`` join the run: the grammar takes one of them,
# not two (``class [[nodiscard]] alignas(16) Vec3`` loses the whole class).
# The run may span lines and line comments, as a head split by ``#if`` lines
# does once those go.
_CPP_CLASS_HEAD_SPECIFIER = (
    rb"(?:\[\[[^\[\]\r\n]*\]\]"
    rb"|alignas[ \t]*\((?:[^()\r\n]|\([^()\r\n]*\))*\)"
    rb"|[A-Z][A-Z0-9_]*(?:\([^()\r\n]*\))?)"
)
# Possessive: a comment that could end anywhere would let a specifier start
# inside any word of it, and the scan of a commented header goes exponential.
_CPP_HEAD_GAP = rb"(?:\s|//[^\r\n]*+)++"
_CPP_CLASS_HEAD_MACRO_RE = re.compile(
    rb"\b(?:class|struct)("
    + _CPP_HEAD_GAP
    + rb"(?:"
    + _CPP_CLASS_HEAD_SPECIFIER
    + _CPP_HEAD_GAP
    + rb")+)"
    rb"(?!final\b)[A-Za-z_]\w*(?:<[^;{}]*>)?(?:[ \t]+final)?(?=\s+\{|\s*:(?!:))"
)
_CPP_MACRO_NAME_RE = re.compile(rb"[A-Z][A-Z0-9_]*")
_CPP_CLASS_HEAD_SPECIFIER_RE = re.compile(_CPP_CLASS_HEAD_SPECIFIER)


def _class_head_spans(source: bytes) -> list[tuple[int, int]]:
    """Class-head runs with a macro or two specifiers; one alone already parses."""
    spans = []
    for m in _CPP_CLASS_HEAD_MACRO_RE.finditer(source):
        run = _CPP_CLASS_HEAD_SPECIFIER_RE.findall(m.group(1))
        if len(run) > 1 or run[0][:1].isupper():
            spans.append(m.span(1))
    return spans


# A call-shaped macro alone on the first member line of a class body, at any
# indent (``DECLARE_SERIALIZABLE(EXPORT, Settings)``, ``GENERATED_BODY()``):
# with no ``;`` the grammar reads it as the type of the next member and can
# lose the class. Only a class body's first member qualifies (an access label
# and comments may come first): there a call is never a statement. A head with
# a paren (a function returning ``struct T``) never matches, nor does a
# constructor whose initializer list or body starts on the next line. Group 2
# is the name.
_CPP_FIRST_MEMBER_MACRO_RE = re.compile(
    rb"(?<!enum )\b(?:class|struct)\b[^;{}()]{0,512}\{"
    rb"(?:[ \t]*+(?:\r?\n|//[^\r\n]*+|(?:public|protected|private)[ \t]*:))*+"
    rb"[ \t]*+(([A-Z][A-Z0-9_]+)\((?:[^()\r\n]|\([^()\r\n]*\))*\))[ \t]*(?=//|\r?\n)"
    rb"(?![ \t]*(?://[^\r\n]*)?\r?\n\s*[:{])"
)

# Clang thread-safety annotations written after a declarator
# (``int x_ GUARDED_BY(mu_);``, ``void F() EXCLUSIVE_LOCKS_REQUIRED(mu_);``).
# The grammar reads them as a second declarator, which hides the field and
# renames the method after the macro. Enumerated by name because the plain
# ``IDENT(...)`` shape is also an ordinary call.
_CPP_TRAILING_ANNOTATION_RE = re.compile(
    rb"(?<!\w)(?:ABSL_)?(?:"
    rb"(?:PT_GUARDED_BY|GUARDED_BY|ACQUIRED_AFTER|ACQUIRED_BEFORE"
    rb"|REQUIRES|REQUIRES_SHARED|EXCLUSIVE_LOCKS_REQUIRED|SHARED_LOCKS_REQUIRED"
    rb"|LOCKS_EXCLUDED|EXCLUDES|ACQUIRE|ACQUIRE_SHARED|RELEASE|RELEASE_SHARED"
    rb"|RELEASE_GENERIC|TRY_ACQUIRE|TRY_ACQUIRE_SHARED|EXCLUSIVE_LOCK_FUNCTION"
    rb"|SHARED_LOCK_FUNCTION|UNLOCK_FUNCTION|EXCLUSIVE_TRYLOCK_FUNCTION"
    rb"|SHARED_TRYLOCK_FUNCTION|ASSERT_CAPABILITY|ASSERT_SHARED_CAPABILITY"
    rb"|ASSERT_EXCLUSIVE_LOCK|ASSERT_SHARED_LOCK|RETURN_CAPABILITY|LOCK_RETURNED)"
    rb"\((?:[^()\r\n]|\([^()\r\n]*\))*\)"
    rb"|NO_THREAD_SAFETY_ANALYSIS\b)"
    rb"(?=[ \t]*(?:[;{=,:\w]|//|/\*|\r?$))",
    re.MULTILINE,
)

# An attribute macro before a constructor (``FMT_CONSTEXPR Foo(int x) : x_(x)
# {}``). The grammar reads the macro as a return type, a typed function may not
# have a member-initializer list, and recovery then leaves the class open to
# the end of the file, so every later function loses its symbol. Group 1 is
# the macro run; the ``: init`` after the parameter list, checked in code, is
# what makes the declaration a constructor and not ``TYPE var(args);``.
_CPP_CTOR_MACRO_RE = re.compile(
    rb"^[ \t]*(?:(?:constexpr|explicit|inline)[ \t]+)*"
    rb"((?:[A-Z][A-Z0-9_]+[ \t]+)+)"
    rb"(?:(?:constexpr|explicit|inline)[ \t]+)*[A-Za-z_]\w*[ \t]*\(",
    re.MULTILINE,
)
_CPP_CTOR_INIT_RE = re.compile(rb"\s*(?:noexcept\b\s*(?:\([^()]*\))?\s*)?:(?!:)")

# A macro alone on its line (``FMT_BEGIN_NAMESPACE``, ``FMT_EXPORT``, ``Q_OBJECT``,
# ``FMT_PRAGMA_CLANG(diagnostic push)``) expands to a namespace opener, an
# attribute or nothing. With no ``;`` the grammar reads it as the type of the
# declaration after it, which renames or swallows that declaration. Group 1 is
# a bare name at any indent; group 2 is a call-shaped one at column 0 that a
# declaration follows, so an indented ``LOG(x)`` statement, a ``TEST(a, b)``
# opening a body and a last enumerator before ``}`` all stay. Neither is
# blanked before a typeless function head (``BOOL\nWINAPI\nDllMain(...)``),
# where the macro is the return type, nor is a registration macro whose
# text the synthetic-symbol pass reads (``NAPI_MODULE(name, Init)``). A head
# has a lowercase letter, so another macro line (``FMT_PRAGMA(x)``) is none.
# A call-shaped macro the file defines itself stays too: it may wrap a
# ``push_macro``/``pop_macro`` pragma the macro-state pass reads.
_CPP_TYPELESS_HEAD = (
    rb"[ \t]*\r?\n(?:[\r\n \t]*[A-Z][A-Z0-9_]+[ \t]*\r?\n)*"
    rb"[\r\n \t]*~?(?=[\w:]*[a-z])[A-Za-z_][\w:]*[ \t]*\("
)
_CPP_MACRO_LINE_RE = re.compile(
    rb"^(?<!\\\n)(?<!\\\r\n)(?:[ \t]*([A-Z][A-Z0-9_]+)(?=[ \t]*\r?\n[\r\n \t]*[A-Za-z_#/])"
    rb"|(?!" + b"|".join(re.escape(t.encode()) for t in _SYNTHESIZED_MACROS) + rb")"
    rb"(([A-Z][A-Z0-9_]+)\((?:[^()\r\n]|\([^()\r\n]*\))*\))(?=[ \t]*\r?\n[\r\n \t]*[A-Za-z_]))"
    rb"(?!" + _CPP_TYPELESS_HEAD + rb")",
    re.MULTILINE,
)
# A call-shaped macro at the head of a statement or member with more code
# after it on the line (``IF_ASSERTS(bool ok =) Insert(k);``, ``ALIGN(16) float
# v;``). No call is followed by a name, so the macro is a prefix whose argument
# may not even be an expression, and the grammar can lose the enclosing class
# over it. Group 2 is the name. A declarator's own trailing words
# (``JSON(int a) noexcept;``) are no code after a prefix.
_CPP_PREFIX_MACRO_RE = re.compile(
    rb"(?:^[ \t]*|[{;}][ \t]*)"
    rb"(([A-Z][A-Z0-9_]+)\((?:[^()\r\n]|\([^()\r\n]*\))*\))(?=[ \t]+[A-Za-z_])"
    rb"(?![ \t]+(?:noexcept|const|volatile|override|final|throw|try|requires|mutable|and|or|xor|bitand|bitor|not_eq|and_eq|or_eq|xor_eq)\b)",
    re.MULTILINE,
)
# A call-shaped macro statement in a class body whose arguments hold a string
# or a parenthesized group (``RTTI(Widget, "{guid}", Base);``,
# ``RTTI((Box, "{guid}", T), Base)``): the grammar reads it as a member
# declaration whose parameters cannot parse and loses the whole class. The
# arguments may span lines. Group 2 is the name.
_CPP_ARG_STRING = rb'"(?:[^"\\\r\n]|\\.)*"'
_CPP_MEMBER_MACRO_RE = re.compile(
    rb"^[ \t]*(([A-Z][A-Z0-9_]+)\((?:"
    + _CPP_ARG_STRING
    + rb"|[^()\";{}]|\((?:"
    + _CPP_ARG_STRING
    + rb"|[^()\";{}])*\))*\)[ \t]*;?)[ \t]*(?=//|\r?$)",
    re.MULTILINE,
)
# What precedes a class body's ``{``: no parens or ``=`` once head runs are gone.
_CPP_CLASS_OPENER_RE = re.compile(rb"(?<!enum )\b(?:class|struct|union)\b[^()=;{}]*\Z")
# Every name a ``class``/``struct``/``union`` keyword introduces: an all-caps
# class (``struct RGB``) has constructors shaped like a call-shaped macro.
_CPP_TYPE_NAME_RE = re.compile(rb"\b(?:class|struct|union)\s+(?:[A-Z][A-Z0-9_]*\s+)*(\w+)")
_CPP_FUNCTION_MACRO_DEFINE_RE = re.compile(rb"^[ \t]*#[ \t]*define[ \t]+(\w+)\(", re.MULTILINE)
# The same macros written ahead of a template on its line (``FMT_EXPORT template <...>``).
_CPP_TEMPLATE_PREFIX_MACRO_RE = re.compile(
    rb"^[ \t]*((?:[A-Z][A-Z0-9_]+[ \t]+)+)(?=template\b)", re.MULTILINE
)

# Words that put a following ``RELEASE(p)`` in statement position, where it
# is a real call and must stay.
_CPP_STATEMENT_WORDS = frozenset(
    {b"return", b"else", b"do", b"case", b"throw", b"co_return", b"co_yield", b"goto"}
    | {b"if", b"while", b"for", b"switch", b"catch", b"sizeof", b"new", b"delete"}
)
# How far back a ``)`` looks for its ``(``: longer than any parameter list,
# short enough that a stray paren in a comment cannot scan the whole file.
_CPP_PAREN_SCAN_LIMIT = 4096


def _skip_space_back(source: bytes, end: int) -> int:
    while end > 0 and source[end - 1] in b" \t\r\n":
        end -= 1
    return end


def _word_before(source: bytes, end: int) -> bytes:
    """Return the identifier that ends at *end*, skipping whitespace before it."""
    end = _skip_space_back(source, end)
    start = end
    while start > 0 and (chr(source[start - 1]).isalnum() or source[start - 1] == ord("_")):
        start -= 1
    return source[start:end]


def _open_paren_of(source: bytes, close: int) -> int:
    """Index of the ``(`` matching the ``)`` at *close*, or -1."""
    depth = 0
    for i in range(close, max(close - _CPP_PAREN_SCAN_LIMIT, -1), -1):
        if source[i] == ord(")"):
            depth += 1
        elif source[i] == ord("("):
            depth -= 1
            if depth == 0:
                return i
    return -1


def _close_paren_of(source: bytes, open_: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_*, or -1."""
    depth = 0
    for i in range(open_, min(open_ + _CPP_PAREN_SCAN_LIMIT, len(source))):
        if source[i] == ord("("):
            depth += 1
        elif source[i] == ord(")"):
            depth -= 1
            if depth == 0:
                return i
    return -1


def _ctor_macro_spans(source: bytes) -> list[tuple[int, int]]:
    """Macro runs before a constructor that has a member-initializer list."""
    spans = []
    for m in _CPP_CTOR_MACRO_RE.finditer(source):
        close = _close_paren_of(source, m.end() - 1)
        if close >= 0 and _CPP_CTOR_INIT_RE.match(source, close + 1):
            spans.append(m.span(1))
    return spans


def _macro_line_spans(source: bytes) -> list[tuple[int, int]]:
    """Macro-only lines, less call-shaped ones naming a macro this file defines."""
    matches = list(_CPP_MACRO_LINE_RE.finditer(source))
    if not matches:
        return []
    local = set(_CPP_FUNCTION_MACRO_DEFINE_RE.findall(source))
    return [m.span(1) if m.group(1) else m.span(2) for m in matches if m.group(3) not in local]


def _first_member_macro_spans(source: bytes) -> list[tuple[int, int]]:
    """First-member macro lines, less a constructor of a class the file names."""
    matches = list(_CPP_FIRST_MEMBER_MACRO_RE.finditer(source))
    if not matches:
        return []
    types = set(_CPP_TYPE_NAME_RE.findall(source))
    return [m.span(1) for m in matches if m.group(2) not in types]


def _member_macro_spans(source: bytes) -> list[tuple[int, int]]:
    """Member-macro statements whose innermost enclosing brace is a class body."""
    candidates = [
        m
        for m in _CPP_MEMBER_MACRO_RE.finditer(source)
        if any(c in m.group(1)[len(m.group(2)) + 1 :] for c in b'"(')
    ]
    if not candidates:
        return []
    types = set(_CPP_TYPE_NAME_RE.findall(source))
    in_class = _in_class_body(source, [m.start(1) for m in candidates])
    return [
        m.span(1)
        for m, inside in zip(candidates, in_class, strict=True)
        if inside and m.group(2) not in types
    ]


def _in_class_body(source: bytes, positions: list[int]) -> list[bool]:
    """For each sorted position, is its innermost enclosing ``{`` a class body?"""
    result: list[bool] = []
    i = 0
    for at, in_class in _scope_changes(source):
        while i < len(positions) and positions[i] < at:
            result.append(in_class)
            i += 1
    return result + [False] * (len(positions) - i)


def _scope_changes(source: bytes):
    """Yield ``(offset, in class body)`` at each brace, then once at the end."""
    stack: list[bool] = []
    head_start = 0
    for match in _TOKEN_RE.finditer(source):
        brace = match["brace"]
        if brace is not None:
            yield match.start(), bool(stack) and stack[-1]
            if brace == b"{":
                opener = _CPP_CLASS_OPENER_RE.search(source, head_start, match.start())
                stack.append(opener is not None)
            elif stack:
                stack.pop()
        if brace is not None or match["dir"] is not None:
            head_start = match.end()
    yield len(source) + 1, bool(stack) and stack[-1]


def _prefix_macro_spans(source: bytes) -> list[tuple[int, int]]:
    """Statement-head prefix macros, outside ``#define`` bodies and less local ones."""
    spans = []
    local: set[bytes] | None = None
    for m in _CPP_PREFIX_MACRO_RE.finditer(source):
        line_start = source.rfind(b"\n", 0, m.start(1)) + 1
        line_end = source.find(b"\n", m.end(1))
        line = source[line_start : len(source) if line_end < 0 else line_end].rstrip()
        if line.lstrip().startswith(b"#") or line.endswith(b"\\"):
            continue
        if local is None:
            local = set(_CPP_FUNCTION_MACRO_DEFINE_RE.findall(source))
            local |= set(_CPP_TYPE_NAME_RE.findall(source))
        if m.group(2) not in local:
            spans.append(m.span(1))
    return spans


def _ends_declarator(source: bytes, end: int) -> bool:
    """True when the text before *end* is a declarator, not a statement head.

    ``void F() REQUIRES(mu)`` and ``int x_ GUARDED_BY(mu)`` qualify;
    ``if (p) RELEASE(p);`` and ``return RELEASE(p);`` do not, and neither
    does a macro at the start of a statement or on a preprocessor line. The
    annotation may sit on the line after its declarator.
    """
    line_start = source.rfind(b"\n", 0, end) + 1
    if source[line_start:end].lstrip().startswith(b"#"):
        return False
    end = _skip_space_back(source, end)
    if end == 0:
        return False
    prev = source[end - 1 : end]
    if prev == b")":
        end = _open_paren_of(source, end - 1)
        if end < 0:
            return False
    elif not (prev.isalnum() or prev in b"_]"):
        return False
    return _word_before(source, end) not in _CPP_STATEMENT_WORDS


_CALL_ARGS_RE = re.compile(rb"\([^()]*\)")
_LINE_COMMENT_RE = re.compile(rb"//[^\r\n]*")


def cpp_class_head_macros(source: bytes) -> frozenset[str]:
    """Names of the class-head macros :func:`prepare_cpp_source` blanks.

    The parser reads the blanked bytes, so it takes these from the original
    source to keep hiding each macro's own ``#define`` like other export macros.
    """
    source = blank_unbalanced_preproc_arms(source, split_constructs=True)
    return frozenset(
        name.decode("ascii")
        for start, end in _class_head_spans(source)
        for name in _CALL_ARGS_RE.sub(b" ", _LINE_COMMENT_RE.sub(b" ", source[start:end])).split()
        if _CPP_MACRO_NAME_RE.fullmatch(name)
    )


def prepare_cpp_source(source: bytes) -> bytes:
    """Blank class-head specifier runs, constructor attribute macros,
    macro-only and prefix macros and trailing thread-safety annotations.

    All are macros the grammar cannot see expanded, and all make it build the
    wrong tree: a missing class and a lost first method, a class left open
    over the rest of the file, a declaration read as typed by the macro before
    it, or a hidden field and a method named after the annotation. Spans are
    blanked in place, so offsets and line numbers outside them are unchanged.

    First, the losing arms and directive lines of ``#if`` groups whose arms do
    not balance their braces, or that split one construct, go (see
    :mod:`.preproc_arms`), so the macro scans below read the one arm the
    parser will.
    """
    source = blank_unbalanced_preproc_arms(source, split_constructs=True)
    spans = _class_head_spans(source)
    # Read with the head runs gone, so a head's ``alignas(..)`` is no paren.
    headless = _blank_spans(source, spans)
    spans += _first_member_macro_spans(headless)
    spans += _member_macro_spans(headless)
    spans += _ctor_macro_spans(source)
    spans += _macro_line_spans(source)
    spans += _prefix_macro_spans(source)
    spans += [m.span(1) for m in _CPP_TEMPLATE_PREFIX_MACRO_RE.finditer(source)]
    spans += [
        m.span()
        for m in _CPP_TRAILING_ANNOTATION_RE.finditer(source)
        if _ends_declarator(source, m.start())
    ]
    return _blank_spans(source, spans)
