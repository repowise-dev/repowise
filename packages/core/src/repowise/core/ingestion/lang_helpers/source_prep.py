"""Byte-preserving source sanitizers for grammar gaps in Pascal, Objective-C, Apex and C++."""

from __future__ import annotations

import re

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
    numbers and byte offsets for everything else in the file survive. Spaces
    and never a newline, so no pattern can consume a line break it did not
    match.
    """
    return _blank_spans(source, [match.span() for match in pattern.finditer(source)])


def _blank_spans(source: bytes, spans: list[tuple[int, int]]) -> bytes:
    """Overwrite each ``(start, end)`` span with spaces, keeping the length."""
    if not spans:
        return source
    out = bytearray(source)
    for start, end in spans:
        out[start:end] = b" " * (end - start)
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
_CPP_CLASS_HEAD_MACRO_RE = re.compile(
    rb"\b(?:class|struct)[ \t]+((?:[A-Z][A-Z0-9_]*(?:\([^()\r\n]*\))?[ \t]+)+)"
    rb"(?!final\b)[A-Za-z_]\w*(?:<[^;{}]*>)?(?:[ \t]+final)?(?=\s+\{|\s*:(?!:))"
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


def cpp_class_head_macros(source: bytes) -> frozenset[str]:
    """Names of the class-head macros :func:`prepare_cpp_source` blanks.

    The parser reads the blanked bytes, so it takes these from the original
    source to keep hiding each macro's own ``#define`` like other export macros.
    """
    return frozenset(
        name.decode("ascii")
        for m in _CPP_CLASS_HEAD_MACRO_RE.finditer(source)
        for name in _CALL_ARGS_RE.sub(b" ", m.group(1)).split()
    )


def prepare_cpp_source(source: bytes) -> bytes:
    """Blank class-head export macros and trailing thread-safety annotations.

    Both are macros the grammar cannot see expanded, and both make it build
    the wrong tree: a missing class and a lost first method, or a hidden field
    and a method named after the annotation. Spans are blanked in place, so
    offsets and line numbers outside them are unchanged.
    """
    spans = [m.span(1) for m in _CPP_CLASS_HEAD_MACRO_RE.finditer(source)]
    spans += [
        m.span()
        for m in _CPP_TRAILING_ANNOTATION_RE.finditer(source)
        if _ends_declarator(source, m.start())
    ]
    return _blank_spans(source, spans)
