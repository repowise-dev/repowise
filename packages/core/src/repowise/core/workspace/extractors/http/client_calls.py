"""Shared recognition layer for HTTP client calls.

The provider side splits recognition from output: ``framework_routes`` finds a
route registration once and each consumer of that match builds its own row.
This module is the consumer-side counterpart. A client library's call shape is
recognised in one function yielding :class:`ClientCallMatch` rows, and
:func:`consumer_contracts` turns those rows into contracts through one URL
resolver, one method inference and one path guard, so a dialect module is a
table of recognisers and nothing else.

**The URL is an expression, not a literal.** Real call sites spell it as a
literal, an interpolated string, a format call (``format!``, ``fmt.Sprintf``,
``String.format``), a concatenation, a name bound to one of those earlier in
the file, or a wrapper such as ``URI.create(...)``. :func:`resolve_url` reads
each of these into the ``${expr}`` template text :mod:`.paths` already
understands, per language through a :class:`UrlSyntax` table, and refuses when
the expression cannot be settled inside the file. A refused site is a missing
edge; a guessed one is a wrong edge.

**What folds and what is refused.** A name folds only when the file assigns it
exactly once, to a single string literal. Assigned twice, or assigned anything
else anywhere in the file, it is left unresolved. File scope, not lexical
scope: a single static assignment means the same thing wherever it is read.
An interpolated expression that is itself such a name is inlined; any other
expression becomes ``${expr}`` and is stripped as a base or collapsed to a
parameter downstream. An escaped brace (``{{``) is refused rather than
corrupted, because no later step can tell it from a parameter.

Placed under ``workspace`` because no graph-edge builder reads client calls;
it moves under ``ingestion`` the day one does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from ..base import line_at
from .dialect import build_consumer_contract, method_from_callee

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Every verb a call shape may name. Wider than ``dialect.METHODS``, which is
# the alternation the *callee-name* inference reads and is left as it is.
VERBS = frozenset({"get", "post", "put", "delete", "patch", "head", "options"})


@dataclass(frozen=True)
class ClientCallMatch:
    """One HTTP client call a recogniser found in a file's text.

    ``url`` is the URL argument's source text, unparsed; :func:`resolve_url`
    reads it. ``method`` is the verb when the call shape settles it (a verb
    argument, a builder terminal, an explicit option) and ``None`` when only
    the callee's name carries it, in which case :func:`method_from_callee`
    reads ``callee``. ``offset`` is where the call starts, for its line.
    """

    client: str
    url: str
    offset: int
    callee: str = ""
    method: str | None = None
    confidence: float = 0.75


@dataclass(frozen=True)
class UrlSyntax:
    """How one language spells a URL expression.

    ``template_quotes`` are quotes whose literal always interpolates (a JS
    backtick); ``template_prefixes`` are prefixes that switch interpolation on
    (Python ``f``, C# ``$``). ``interpolation`` matches one interpolated
    expression inside such a body; its first non-empty group is the expression.
    ``None`` means the body is already in ``${expr}`` form.

    ``format_heads`` name calls whose first argument is a template with
    ``placeholder`` holes. ``unwrap_heads`` name calls whose single argument
    *is* the URL. ``concat`` is the string-concatenation operator, empty when
    the language's dialects do not fold concatenation. ``assignment`` locates
    ``name = rhs`` statements for constant folding; ``assignment_strip`` drops a
    trailing decoration from the right-hand side (Ruby's ``.freeze``).
    """

    quotes: str = "\"'"
    prefixes: str = ""
    template_quotes: str = ""
    template_prefixes: str = ""
    interpolation: re.Pattern[str] | None = None
    refuse: re.Pattern[str] | None = None
    refuse_prefixes: str = ""
    format_heads: tuple[str, ...] = ()
    placeholder: re.Pattern[str] | None = None
    unwrap_heads: tuple[str, ...] = ()
    concat: str = ""
    assignment: re.Pattern[str] | None = None
    assignment_strip: re.Pattern[str] | None = None


# ---------------------------------------------------------------------------
# Argument scanning
# ---------------------------------------------------------------------------

_QUOTES = "'\"`"

# How far a scan may run past its opening bracket. A stray quote in a comment
# desynchronises the scan, and without a bound every candidate after it would
# read to the end of the file.
SCAN_LIMIT = 50_000


def match_paren(content: str, open_idx: int, limit: int = SCAN_LIMIT, closer: str = ")") -> int:
    """Index of the bracket closing the one at *open_idx*, or -1.

    Quote- and template-aware so a parenthesis inside a string literal does not
    unbalance the scan. Nested ``${...}`` inside a template literal is tracked
    as ordinary depth, which is what makes ``` `/a/${f(x)}/b` ``` parse. A
    call that does not close within *limit* characters is -1 as well.
    *closer* is ``}`` for a trailing lambda block.
    """
    depth = 0
    i = open_idx
    n = min(len(content), open_idx + limit)
    # Open template literals, innermost last. A backtick inside a ``${...}``
    # opens a *nested* literal rather than closing the outer one, so the state
    # has to be a stack: ``fetch(`/a/${c ? `x` : `y`}/b`)`` otherwise reads the
    # inner backtick as the end of the string and desynchronises everything
    # after it.
    tmpl: list[int] = []
    quote: str | None = None
    while i < n:
        ch = content[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and content[i + 1] == "{":
                tmpl.append(depth)
                depth += 1
                quote = None
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if tmpl and ch == "}" and depth == tmpl[-1]:
                tmpl.pop()
                quote = "`"  # back inside the template literal that opened it
            elif depth == 0 and ch == closer:
                return i
        i += 1
    return -1


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split *text* at each top-level *sep* character, outside quotes and brackets."""
    out: list[str] = []
    depth = 0
    tmpl: list[int] = []  # see :func:`match_paren`: nested templates need a stack
    quote: str | None = None
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and text[i + 1] == "{":
                tmpl.append(depth)
                depth += 1
                quote = None
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if tmpl and ch == "}" and depth == tmpl[-1]:
                tmpl.pop()
                quote = "`"
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return out


def split_first_arg(args: str) -> tuple[str, str]:
    """Split an argument list into ``(first_arg, rest)`` at the top-level comma.

    Stops at that comma: the rest is returned unscanned, so a call whose second
    argument is a large options literal costs only its first argument.
    """
    depth = 0
    tmpl: list[int] = []  # see :func:`match_paren`: nested templates need a stack
    quote: str | None = None
    i = 0
    n = len(args)
    while i < n:
        ch = args[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and args[i + 1] == "{":
                tmpl.append(depth)
                depth += 1
                quote = None
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in _QUOTES:
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if tmpl and ch == "}" and depth == tmpl[-1]:
                tmpl.pop()
                quote = "`"
        elif ch == "," and depth == 0:
            return args[:i].strip(), args[i + 1 :]
        i += 1
    return args.strip(), ""


def call_arguments(content: str, paren_offset: int, close: int | None = None) -> list[str] | None:
    """The top-level arguments of the call whose ``(`` is at *paren_offset*.

    ``None`` when the argument list does not close, so a recogniser can tell a
    scanner failure from a call with no arguments. *close* is the matching
    ``)`` when the caller has already found it.
    """
    if close is None:
        close = match_paren(content, paren_offset)
    if close < 0:
        return None
    inner = content[paren_offset + 1 : close]
    if not inner.strip():
        return []
    return [a.strip() for a in _split_top_level(inner, ",")]


# ---------------------------------------------------------------------------
# Method inference from an argument
# ---------------------------------------------------------------------------

_QUOTED_WORD_RE = re.compile(r"""^['"]([A-Za-z]+)['"]$""")
_LAST_SEGMENT_RE = re.compile(r"(?:\.|::|->)")


def method_from_argument(text: str) -> str | None:
    """The HTTP verb an argument names, else ``None``.

    Reads a quoted verb (``'GET'``), and a verb constant by its last segment
    (``http.MethodGet``, ``HttpMethod.POST``, ``HTTPMethods.Get``,
    ``Net::HTTP::Get``). Anything else, a variable above all, is not a verb
    this layer may claim to know.
    """
    t = text.strip()
    m = _QUOTED_WORD_RE.match(t)
    if m is None:
        # A bare name is a verb only as a constant (`GET`), never as a
        # lower-case variable that happens to be called `get`.
        segments = _LAST_SEGMENT_RE.split(t)
        if len(segments) == 1 and not t.isupper():
            return None
    word = m.group(1) if m else _LAST_SEGMENT_RE.split(t)[-1]
    if not m and word.startswith("Method") and len(word) > len("Method"):
        word = word[len("Method") :]
    return word.upper() if word.lower() in VERBS else None


# ---------------------------------------------------------------------------
# URL expression resolution
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::|->)[A-Za-z_]\w*)*")

# Already-template text: one ``${expr}`` interpolation, for folding a constant
# inside a body a language wrote in the target form to begin with.
_TEMPLATE_INTERP_RE = re.compile(r"\$\{([^{}]+)\}")

# Dropped from an interpolated expression (``settings.base.rstrip('/')``) so
# :func:`.paths.base_token_identifier` reads the attribute, not ``rstrip``.
_TRAILING_CALL_RE = re.compile(r"\.\s*\w+\s*\([^()]*\)\s*$")


@cache
def _literal_re(quotes: str, prefixes: str) -> re.Pattern[str]:
    alts = []
    if '"' in quotes:
        alts.append('"""')
    if "'" in quotes:
        alts.append("'''")
    alts.extend(re.escape(q) for q in quotes)
    prefix = f"[{re.escape(prefixes)}]*" if prefixes else ""
    return re.compile(
        rf"^(?P<prefix>{prefix})(?P<q>{'|'.join(alts)})(?P<body>.*)(?P=q)$", re.DOTALL
    )


@cache
def _head_re(heads: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(h).replace(r"\ ", r"\s+") for h in heads)
    return re.compile(rf"^(?:{alts})\s*\(")


def _parse_literal(text: str, syntax: UrlSyntax) -> tuple[str, str, str] | None:
    """``(lowercased prefix, quote, body)`` when *text* is exactly one string literal."""
    m = _literal_re(syntax.quotes, syntax.prefixes).match(text.strip())
    if m is None:
        return None
    body = m.group("body")
    if m.group("q") in body:
        return None  # the literal ended and something else followed it
    return m.group("prefix").lower(), m.group("q"), body


def _interpolates(prefix: str, quote: str, syntax: UrlSyntax) -> bool:
    # A triple quote interpolates when its single form does (Kotlin raw strings).
    return quote[0] in syntax.template_quotes or any(
        p in syntax.template_prefixes.lower() for p in prefix
    )


def _lookup(name: str, constants: dict[str, str]) -> str | None:
    const = constants.get(name)
    if const is None and name.startswith("$"):
        const = constants.get(name[1:])  # a PHP variable, keyed without its sigil
    return const


def _fold(expr: str, syntax: UrlSyntax, constants: dict[str, str]) -> str:
    """One interpolated expression as template text, inlining a plain constant.

    Only a constant whose own text does not interpolate is inlined: nothing in
    the corpus needs a second level, and a half-folded template would be wrong.
    """
    expr = expr.strip()
    const = _lookup(expr, constants)
    if const is not None:
        lit = _parse_literal(const, syntax)
        text = _literal_text(lit, syntax, {}) if lit is not None else None
        if text is not None and "${" not in text:
            return text
    return "${" + _TRAILING_CALL_RE.sub("", expr) + "}"


def _literal_text(
    parsed: tuple[str, str, str], syntax: UrlSyntax, constants: dict[str, str]
) -> str | None:
    prefix, quote, body = parsed
    if any(p in syntax.refuse_prefixes for p in prefix):
        return None  # bytes, not a URL this layer records
    if not _interpolates(prefix, quote, syntax):
        return body
    if syntax.refuse is not None and syntax.refuse.search(body):
        return None
    if syntax.interpolation is None:
        if not constants:
            return body
        return _TEMPLATE_INTERP_RE.sub(lambda m: _fold(m.group(1), syntax, constants), body)

    def sub(m: re.Match[str]) -> str:
        expr = next(g for g in m.groups() if g is not None)
        return _fold(expr, syntax, constants)

    return syntax.interpolation.sub(sub, body)


def _format_template(text: str, syntax: UrlSyntax, constants: dict[str, str]) -> str | None:
    """A ``format!("...")``-style call as template text, or ``None``."""
    m = _head_re(syntax.format_heads).match(text)
    if m is None or syntax.placeholder is None:
        return None
    args = _whole_call_arguments(text, m.end() - 1)
    if not args:
        return None
    parsed = _parse_literal(args[0], syntax)
    if parsed is None:
        return None
    body = _literal_text(parsed, syntax, constants)
    if body is None:
        return None
    # `%%` is a literal percent sign, not a hole.
    return syntax.placeholder.sub(lambda h: "%" if h.group() == "%%" else "${x}", body)


def _whole_call_arguments(text: str, paren: int) -> list[str] | None:
    """The arguments of the call at *paren* when the call is all of *text*.

    ``URI.create(BASE).resolve("/x")`` is not ``URI.create(BASE)``: reading the
    head alone would drop the path the request reaches.
    """
    close = match_paren(text, paren)
    if close < 0 or text[close + 1 :].strip():
        return None
    return call_arguments(text, paren, close)


def _unwrap(text: str, syntax: UrlSyntax) -> str | None:
    """The single argument of a ``URI.create(...)``-style wrapper, or ``None``."""
    m = _head_re(syntax.unwrap_heads).match(text)
    if m is None:
        return None
    args = _whole_call_arguments(text, m.end() - 1)
    return args[0] if args and len(args) == 1 else None


def _concat(text: str, syntax: UrlSyntax, constants: dict[str, str]) -> str | None:
    """``a + "/x"`` as template text, or ``None`` when a piece is not readable."""
    if syntax.concat not in text:
        return None
    pieces = _split_top_level(text, syntax.concat)
    if len(pieces) < 2:
        return None
    out: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        parsed = _parse_literal(piece, syntax)
        if parsed is not None:
            body = _literal_text(parsed, syntax, constants)
            if body is None:
                return None
            out.append(body)
        elif _NAME_RE.fullmatch(piece):
            out.append(_fold(piece, syntax, constants))
        else:
            return None  # a call or an operator: not settled in this file
    return "".join(out)


def resolve_url(
    expr: str, syntax: UrlSyntax, constants: dict[str, str] | None = None
) -> str | None:
    """The URL text *expr* denotes, or ``None`` when it cannot be resolved here.

    Interpolations come out as ``${expr}``, so :func:`.paths.strip_leading_base_expr`
    strips a base placeholder and :func:`.paths.normalize_http_path` collapses a
    parameter with no per-language branch in either.
    """
    constants = constants or {}
    text = expr.strip()
    parsed = _parse_literal(text, syntax)
    if parsed is not None:
        return _literal_text(parsed, syntax, constants)
    if syntax.format_heads:
        folded = _format_template(text, syntax, constants)
        if folded is not None:
            return folded
    if syntax.unwrap_heads:
        inner = _unwrap(text, syntax)
        if inner is not None:
            return resolve_url(inner, syntax, constants)
    if syntax.concat:
        folded = _concat(text, syntax, constants)
        if folded is not None:
            return folded
    if _NAME_RE.fullmatch(text):
        # A bare name. Its value resolved without constants when it was
        # recorded, so this step cannot reach another bare name.
        const = _lookup(text, constants)
        if const is not None:
            return resolve_url(const, syntax, constants)
    return None


def string_constants(content: str, syntax: UrlSyntax, code: str | None = None) -> dict[str, str]:
    """Names *content* assigns exactly once, to a URL expression that resolves.

    The value is the right-hand side's raw text, so an interpolating literal,
    a format call or a concatenation keeps its parts for :func:`resolve_url`
    to fold at the call site.

    *code*, when given, is *content* with comments and string bodies blanked at
    the same offsets. Assignments are located in it and their values read from
    *content*, so the example code in a docstring (``    url = "/docs/example"``)
    is not read as a real assignment.
    """
    if syntax.assignment is None:
        return {}
    text = content if code is None else code
    seen: dict[str, str | None] = {}
    for m in syntax.assignment.finditer(text):
        if code is None and _on_comment_line(content, m.start()):
            continue  # an example in a doc comment is not a binding
        name = m.group("name")
        rhs = content[m.start("rhs") : m.end("rhs")].strip()
        if syntax.assignment_strip is not None:
            rhs = syntax.assignment_strip.sub("", rhs).strip()
        # A second assignment retires the name whatever it assigns: the reader
        # cannot tell which one reaches the call site.
        seen[name] = None if name in seen or resolve_url(rhs, syntax) is None else rhs
    # `x += "/v1"` and `x, err = f()` rebind a name without the plain form.
    for m in _COMPOUND_ASSIGN_RE.finditer(text):
        seen[m.group(1)] = None
    for m in _MULTI_ASSIGN_RE.finditer(text):
        for name in m.group(1).replace(" ", "").split(","):
            seen[name.lstrip("$")] = None
    return {name: text for name, text in seen.items() if text is not None}


# ``x += ...`` / ``$x .= ...``; the name is keyed without a PHP sigil.
_COMPOUND_ASSIGN_RE = re.compile(r"(?<![\w.$-])\$?([A-Za-z_]\w*)[ \t]*[+.]=(?!=)")
# ``a, b := f()`` / ``a, b = f()``: every name on the left is rebound.
_MULTI_ASSIGN_RE = re.compile(
    r"^[ \t]*(\$?[A-Za-z_]\w*(?:[ \t]*,[ \t]*\$?[A-Za-z_]\w*)+)[ \t]*:?=(?!=)", re.MULTILINE
)


def _on_comment_line(content: str, offset: int) -> bool:
    """True when the line holding *offset* starts as a line or block comment."""
    start = max(content.rfind("\n", 0, offset) + 1, offset - 200)
    return content[start:offset].lstrip().startswith(("//", "#", "*", "/*"))


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

# A URL concrete enough to name a route on its own: a rooted path, a base
# placeholder, or an absolute URL. A relative path composes onto a base this
# layer cannot see.
_ROOTED_URL_RE = re.compile(r"^(?:/|\$\{|https?:|//)")


def is_rooted_url(url: str) -> bool:
    return _ROOTED_URL_RE.match(url) is not None


def consumer_contracts(
    ctx: ScanContext,
    matches: Iterable[ClientCallMatch],
    syntax: UrlSyntax,
    *,
    constants: dict[str, str] | None = None,
    path_only: bool = False,
    rooted_only: bool = False,
) -> list[Contract]:
    """Consumer contracts for *matches*, resolving each URL through *syntax*.

    *path_only* drops a URL with no ``/`` in it. It is for recognisers whose
    receiver is ambiguous, so a map's ``.get("key")`` never becomes a route.

    *rooted_only* drops a URL that is neither absolute nor rooted. A client
    with a configured base composes ``"some/path"`` onto it, so the path the
    request reaches has a prefix this layer cannot see.
    """
    out: list[Contract] = []
    for m in matches:
        url = resolve_url(m.url, syntax, constants)
        if url is None or (path_only and "/" not in url):
            continue
        if rooted_only and not is_rooted_url(url):
            continue
        method = m.method or method_from_callee(m.callee)
        c = build_consumer_contract(
            ctx,
            method=method.upper(),
            url=url,
            client=m.client,
            line=line_at(ctx.content, m.offset),
            confidence=m.confidence,
        )
        if c is not None:
            out.append(c)
    return out


def literal_span(content: str, m: re.Match[str], group: int) -> str:
    """The source text of a quoted literal whose *body* is *group* of *m*.

    For a recogniser whose regex captures the text between the quotes: the
    slice one character wider on each side is the literal as written, which is
    what :func:`resolve_url` reads. A capture that stopped at a different quote
    (one inside a ``${...}`` expression) is closed with its own opening quote,
    so the text read is the prefix the regex saw and the row is kept.
    """
    quote = content[m.start(group) - 1]
    body = content[m.start(group) : m.end(group)]
    return quote + body + quote


def matches_in(
    content: str,
    pattern: re.Pattern[str],
    *,
    client: str,
    url_group: int,
    method_group: int | None = None,
    callee_group: int | None = None,
    confidence: float = 0.75,
    prefix_group: int | None = None,
) -> Iterator[ClientCallMatch]:
    """Rows for every match of *pattern* whose *url_group* captured a literal body.

    The common recogniser: a regex that reads the callee (or a fixed client
    name), an optional verb group, and the body of a quoted URL literal.
    *prefix_group* is a literal prefix captured before the opening quote (C#'s
    ``$``/``@``), included in the row's ``url`` text.
    """
    for m in pattern.finditer(content):
        url = literal_span(content, m, url_group)
        if prefix_group is not None:
            url = m.group(prefix_group) + url
        yield ClientCallMatch(
            client=client,
            url=url,
            offset=m.start(),
            callee=m.group(callee_group) if callee_group is not None else "",
            method=m.group(method_group).upper() if method_group is not None else None,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Per-language syntax tables
# ---------------------------------------------------------------------------

_C_FORMAT_PLACEHOLDER_RE = re.compile(r"%%|%[-+ #0]*\d*(?:\.\d+)?[a-zA-Z]")
_BRACE_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_BRACE_INTERP_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")
_ESCAPED_BRACE_RE = re.compile(r"\{\{|\}\}")

JS_SYNTAX = UrlSyntax(quotes="\"'`", template_quotes="`")

# ``NAME = <expr>`` at any indentation, RHS to end of line. The whitespace
# around ``=`` is required, which excludes the usual unspaced keyword argument
# on its own line (``url=str(resp.url),``) that otherwise reads as a second
# assignment and retires the very name being folded.
PYTHON_SYNTAX = UrlSyntax(
    prefixes="fFrRbBuU",
    template_prefixes="f",
    interpolation=_BRACE_INTERP_RE,
    refuse=_ESCAPED_BRACE_RE,
    refuse_prefixes="b",
    assignment=re.compile(
        r"^[ \t]*(?P<name>[A-Za-z_]\w*)(?:[ \t]*:[^=\n]+)?[ \t]+=[ \t]+(?P<rhs>.*)$",
        re.MULTILINE,
    ),
)

RUST_SYNTAX = UrlSyntax(
    quotes='"',
    format_heads=("format!",),
    placeholder=_BRACE_PLACEHOLDER_RE,
)

CSHARP_SYNTAX = UrlSyntax(
    quotes='"',
    prefixes="$@",
    template_prefixes="$",
    interpolation=_BRACE_INTERP_RE,
    refuse=_ESCAPED_BRACE_RE,
)

GO_SYNTAX = UrlSyntax(
    quotes='"`',
    format_heads=("fmt.Sprintf",),
    placeholder=_C_FORMAT_PLACEHOLDER_RE,
    concat="+",
    assignment=re.compile(
        r"^[ \t]*(?:(?:const|var)[ \t]+)?(?P<name>[A-Za-z_]\w*)[ \t]*:?=[ \t]*(?P<rhs>[^\n]+)$",
        re.MULTILINE,
    ),
)

RUBY_SYNTAX = UrlSyntax(
    template_quotes='"',
    interpolation=re.compile(r"#\{([^{}]+)\}"),
    unwrap_heads=("URI.parse", "URI"),
    concat="+",
    assignment=re.compile(
        r"^[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*=[ \t]*(?P<rhs>[^\n]+)$", re.MULTILINE
    ),
    assignment_strip=re.compile(r"\.freeze$"),
)

JAVA_SYNTAX = UrlSyntax(
    quotes='"',
    format_heads=("String.format",),
    placeholder=_C_FORMAT_PLACEHOLDER_RE,
    unwrap_heads=("URI.create", "new URI", "new URL", "URI", "HttpUrl.parse"),
    concat="+",
    assignment=re.compile(
        r"(?<![=!<>.\w])(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)[ \t]*(?P<rhs>[^;\n]+);"
    ),
)

KOTLIN_SYNTAX = UrlSyntax(
    quotes='"',
    template_quotes='"',
    interpolation=re.compile(r"\$\{([^{}]+)\}|\$([A-Za-z_]\w*)"),
    unwrap_heads=("Url", "URI.create", "URI"),
    concat="+",
    assignment=re.compile(
        r"\b(?:val|var)[ \t]+(?P<name>[A-Za-z_]\w*)(?:[ \t]*:[ \t]*[\w<>?]+)?[ \t]*=[ \t]*(?P<rhs>[^\n]+)$",
        re.MULTILINE,
    ),
)

PHP_SYNTAX = UrlSyntax(
    template_quotes='"',
    interpolation=re.compile(r"\{\$([^{}]+)\}|\$\{([^{}]+)\}|\$([A-Za-z_]\w*(?:->[A-Za-z_]\w*)?)"),
    concat=".",
    assignment=re.compile(
        r"(?:\bconst[ \t]+|\$)(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)[ \t]*(?P<rhs>[^;\n]+);"
    ),
)

__all__ = [
    "CSHARP_SYNTAX",
    "GO_SYNTAX",
    "JAVA_SYNTAX",
    "JS_SYNTAX",
    "KOTLIN_SYNTAX",
    "PHP_SYNTAX",
    "PYTHON_SYNTAX",
    "RUBY_SYNTAX",
    "RUST_SYNTAX",
    "VERBS",
    "ClientCallMatch",
    "call_arguments",
    "consumer_contracts",
    "is_rooted_url",
    "literal_span",
    "match_paren",
    "matches_in",
    "method_from_argument",
    "resolve_url",
    "split_first_arg",
    "string_constants",
]
