"""Byte-preserving source sanitizers for grammar gaps in Pascal and Objective-C."""

from __future__ import annotations

import re

_PASCAL_USES_IN_CLAUSE_RE = re.compile(rb"\bin\b[ \t]*'(?:[^'\r\n]|'')*'")


def _sanitize_pascal_project_source(source: bytes) -> bytes:
    """Blank Delphi/FPC project-file ``unit in 'path.pas'`` clauses.

    ``.dpr``/``.dpk``/``.lpr`` project files map each unit to its source
    path right in the ``uses`` clause -- ``uses SysUtils, MyUnit in
    'src\\MyUnit.pas';`` -- and Delphi's IDE writes this automatically for
    every unit added to a project, making it the norm rather than the
    exception in real ``.dpr``/``.dpk`` files (confirmed against this
    repo's own ``MTN2.dpr``: every non-RTL unit uses it).

    tree-sitter-pascal's grammar has no rule for the trailing ``in
    '...'`` at all. Hitting it mid-``declUses`` doesn't just fail that
    one unit -- the parser's error recovery folds the ``in``, the path
    string, and every subsequent comma-separated unit into one corrupted
    ``moduleName`` node spanning to wherever it happens to resync, so a
    single ``in`` clause was silently swallowing the rest of the ``uses``
    list (observed on ``MTN2.dpr``: 4 imports extracted instead of ~80,
    the 4th holding several KB of raw multi-line garbage as its
    ``module_path``). This is an upstream grammar gap, not something a
    ``.scm`` query can route around -- the AST itself is malformed before
    any query runs.

    Blanks the matched span with spaces (never a raw newline -- a Pascal
    string literal can't contain one, so no line is fully consumed) to
    preserve every other byte offset in the file, so line numbers for
    symbols/imports/calls elsewhere are unaffected. `'ABC'` doesn't need
    the doubled-quote (`''`) escape handled specially for *finding* the
    end of the string here (the regex already treats `''` as staying
    inside the literal), only for correctness of the match's own extent.

    Scoped to project files specifically: this syntax is invalid outside
    a ``uses`` clause and ``.pas``/``.pp`` unit files can't legally carry
    it, so there's nothing to blank there and no reason to run the regex
    over every unit file in a codebase.
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

    Called from :func:`~.sfc_source.prepare_source` -- the same
    registry-dispatched hook every other tree-sitter consumer (the
    ingestion parser, plus the complexity/dataflow/duplication health
    walkers) already calls before handing bytes to a ``Parser`` -- rather
    than parser.py special-casing Pascal in its own if-blocks. That keeps
    ``docs/architecture/language-support.md``'s "zero changes to
    parser.py" promise for a new language, and means the health walkers
    get the same clean projection the ingestion parser does instead of
    parsing raw bytes.

    Only wraps ``_sanitize_pascal_project_source`` (``.dpr``/``.dpk``/
    ``.lpr`` ``in '...'`` clauses), gated on *path*'s extension since that
    syntax is invalid in a plain unit file. An earlier revision of this
    function also blanked whatever an anonymous ``array[...] of record``
    element type's parse errors touched, discovered via ERROR-node spans
    from a throwaway parse. Dropped after review (PR #1353): tree-sitter's
    error recovery for that construct doesn't cleanly wrap the bad
    construct in one ERROR node -- on the reviewer's repro, one of the
    spans it found was the class's own legitimate closing ``end;``, and
    blanking it produced the exact same broken structure (the following
    method detached from its class) as running no sanitizer at all. A
    correct fix needs a nesting-aware nested-record/variant-part scanner,
    which is more surface area than one occurrence in one file (see the
    dropped function's own docstring) justifies; the anon-record case is
    left to degrade to a wrong parent for that one class, same as any
    other unhandled grammar gap.
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


# Availability / naming attributes written *call-shaped* after a declaration
# (``@property (...) Foo *bar AF_API_AVAILABLE(ios(10));``,
# ``- (void)done NS_SWIFT_NAME(done())``). The bare whole-line pass above
# cannot see these: the macro is the last token of a declaration the grammar
# otherwise reads fine, and it reads as a declarator or attribute list the
# grammar cannot close. Error recovery then fails to resync at the ``;`` and
# the damage runs past the declaration into the rest of the file, so the
# enclosing method of every call after it is lost and those calls are credited
# to ``__module__`` instead of their real caller. Measured on AFNetworking's
# ``AFURLSessionManager.m``: 822 ERROR nodes before, 2 after.
#
# Enumerated by name, the same shape as the list above, because the grammar
# cannot tell an attribute from a call on its own. ``NS_ENUM(NSInteger, Kind)``
# after a ``typedef`` and ``NSLog(@"%@", x)`` both look like
# ``IDENT(...)``; a general rule would blank real code and a match-everything
# rule for these names is no safer. Only a form that *terminates* a declaration
# qualifies: the macro must be followed by the closing ``;``, the ``{`` opening
# a method body, or the end of the line (a definition may put the brace on the
# next one). The same name inside an argument list or a comparison sits before
# a ``,``/``)`` and is left alone.
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
    call-shaped availability attribute (``AF_API_AVAILABLE(ios(10), ...)``,
    ``NS_SWIFT_NAME(...)``). There the recovery never resyncs at the closing
    ``;``, so one trailing attribute derails the remainder of the file. Both
    passes blank in place, so every offset outside a match is unchanged.
    """
    return _blank_objc_trailing_macros(_blank_matches(source, _OBJC_BARE_MACRO_LINE_RE))
