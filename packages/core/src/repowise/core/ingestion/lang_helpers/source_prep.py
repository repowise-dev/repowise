"""Byte-preserving source sanitizers for grammar gaps in Pascal and Objective-C."""

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
    if not pattern.search(source):
        return source
    out = bytearray(source)
    for match in pattern.finditer(source):
        start, end = match.span()
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
