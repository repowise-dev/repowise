"""String expressions as a recogniser reads them: arguments, literals, constants.

A URL, a queue name or a topic is written in source as an expression, not
always a literal: an interpolated string, a format call (``format!``,
``fmt.Sprintf``, ``String.format``), a concatenation, a name bound to one of
those earlier in the file, or a wrapper such as ``URI.create(...)``.
:func:`resolve_string` reads each of these into ``${expr}`` template text, per
language through a :class:`StringSyntax` table, and refuses when the expression
cannot be settled inside the file. A refused site is a missing edge; a guessed
one is a wrong edge.

**What folds and what is refused.** A name folds only when the file assigns it
exactly once, to a single string expression that itself resolves. Assigned
twice, or assigned anything else anywhere in the file, it is left unresolved.
File scope, not lexical scope: a single static assignment means the same thing
wherever it is read. An interpolated expression that is itself such a name is
inlined; any other expression becomes ``${expr}``. An escaped brace (``{{``) is
refused rather than corrupted, because no later step can tell it from a
parameter.

The bracket scanner here is quote- and template-aware and knows nothing about
comments; ``ingestion.framework_routes`` keeps its own comment-aware scanner.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from itertools import pairwise

from .langs import CSHARP, GO, JAVA, JS_TS, KOTLIN, PHP, PYTHON, RUBY, RUST


@dataclass(frozen=True)
class StringSyntax:
    """How one language spells a string expression.

    ``template_quotes`` are quotes whose literal always interpolates (a JS
    backtick); ``template_prefixes`` are prefixes that switch interpolation on
    (Python ``f``, C# ``$``). ``interpolation`` matches one interpolated
    expression inside such a body; its first non-empty group is the expression.
    ``None`` means the body is already in ``${expr}`` form.

    ``format_heads`` name calls whose first argument is a template with
    ``placeholder`` holes; ``format_method`` is a method called on such a
    template (Python's ``"/x/{}".format(id)``). ``unwrap_heads`` name calls whose single argument
    *is* the value. ``join_heads`` name calls that join their arguments as URL
    segments (``Url.Combine(base, "items")``). ``concat`` is the string-concatenation operator, empty when
    the language's dialects do not fold concatenation. ``assignment`` locates
    ``name = rhs`` statements for constant folding; ``assignment_strip`` drops a
    trailing decoration from the right-hand side (Ruby's ``.freeze``).
    ``members`` locates a constant object or enum (``const Q = {``,
    ``enum Q {``), ending at its opening brace; each ``key: value`` or
    ``key = value`` entry folds as ``Q.key``. ``self_prefixes`` read a
    class's own member (PHP ``self::X``, Python ``self.X``), looked up as the
    bare name the class body assigns.
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
    format_method: str = ""
    unwrap_heads: tuple[str, ...] = ()
    join_heads: tuple[str, ...] = ()
    concat: str = ""
    assignment: re.Pattern[str] | None = None
    assignment_strip: re.Pattern[str] | None = None
    members: re.Pattern[str] | None = None
    self_prefixes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Argument scanning
# ---------------------------------------------------------------------------

_QUOTES = "'\"`"

# How far a scan may run past its opening bracket. A stray quote in a comment
# desynchronises the scan, and without a bound every candidate after it would
# read to the end of the file.
SCAN_LIMIT = 50_000


def _scan(
    text: str,
    start: int,
    end: int,
    *,
    closer: str | None = None,
    sep: str | None = None,
    first_sep_only: bool = False,
) -> tuple[int, list[int]]:
    """Walk *text* from *start*, outside quotes and brackets.

    Returns ``(close, seps)``: the index of the *closer* that brings depth back
    to zero (-1 when none does before *end*), and every top-level *sep* index
    seen on the way. Nested ``${...}`` inside a template literal is tracked as
    ordinary depth, which is what makes ``` `/a/${f(x)}/b` ``` parse.
    """
    depth = 0
    # Open template literals, innermost last. A backtick inside a ``${...}``
    # opens a *nested* literal rather than closing the outer one, so the state
    # has to be a stack: ``fetch(`/a/${c ? `x` : `y`}/b`)`` otherwise reads the
    # inner backtick as the end of the string and desynchronises everything
    # after it.
    tmpl: list[int] = []
    quote: str | None = None
    seps: list[int] = []
    i = start
    while i < end:
        ch = text[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < end and text[i + 1] == "{":
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
            elif closer is not None and depth == 0 and ch == closer:
                return i, seps
        elif ch == sep and depth == 0:
            seps.append(i)
            if first_sep_only:
                break
        i += 1
    return -1, seps


def match_paren(content: str, open_idx: int, limit: int = SCAN_LIMIT, closer: str = ")") -> int:
    """Index of the bracket closing the one at *open_idx*, or -1.

    Quote- and template-aware so a parenthesis inside a string literal does not
    unbalance the scan. A call that does not close within *limit* characters
    is -1 as well. *closer* is ``}`` for a trailing lambda block.
    """
    return _scan(content, open_idx, min(len(content), open_idx + limit), closer=closer)[0]


def split_top_level(text: str, sep: str) -> list[str]:
    """Split *text* at each top-level *sep* character, outside quotes and brackets."""
    _, seps = _scan(text, 0, len(text), sep=sep)
    bounds = [-1, *seps, len(text)]
    return [text[a + 1 : b] for a, b in pairwise(bounds)]


def split_first_arg(args: str) -> tuple[str, str]:
    """Split an argument list into ``(first_arg, rest)`` at the top-level comma.

    Stops at that comma: the rest is returned unscanned, so a call whose second
    argument is a large options literal costs only its first argument.
    """
    _, seps = _scan(args, 0, len(args), sep=",", first_sep_only=True)
    if not seps:
        return args.strip(), ""
    return args[: seps[0]].strip(), args[seps[0] + 1 :]


def call_arguments(content: str, paren_offset: int, close: int | None = None) -> list[str] | None:
    """The top-level arguments of the call whose ``(`` is at *paren_offset*.

    ``None`` when the argument list does not close, so a recogniser can tell a
    scanner failure from a call with no arguments. *close* is the matching
    ``)`` when the caller has already found it. A ``{`` at *paren_offset*
    opens a composite literal (Go ``sarama.ProducerMessage{Topic: t}``), whose
    fields are read as its arguments.
    """
    if close is None:
        closer = "}" if content[paren_offset] == "{" else ")"
        close = match_paren(content, paren_offset, closer=closer)
    if close < 0:
        return None
    inner = content[paren_offset + 1 : close]
    if not inner.strip():
        return []
    return [a.strip() for a in split_top_level(inner, ",")]


# ---------------------------------------------------------------------------
# Expression resolution
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"[A-Za-z_$][\w$]*(?:(?:\.|::|->)[A-Za-z_]\w*)*")

# Already-template text: one ``${expr}`` interpolation, for folding a constant
# inside a body a language wrote in the target form to begin with.
_TEMPLATE_INTERP_RE = re.compile(r"\$\{([^{}]+)\}")

# Dropped from an interpolated expression (``settings.base.rstrip('/')``) so a
# base-token reader sees the attribute, not ``rstrip``.
_TRAILING_CALL_RE = re.compile(r"\.\s*\w+\s*\([^()]*\)\s*$")
# A dotted name converted to text by a no-argument method: `id.toString()`, `name.strip()`.
_CONVERSION_RE = re.compile(
    NAME_RE.pattern + r"\.\s*(?:toString|trim|toLowerCase|toUpperCase|strip|lower|upper)\s*\(\s*\)"
)


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


def _template_end(text: str, open_idx: int) -> int:
    """Index of the backtick closing the template literal opened at *open_idx*, or -1.

    A backtick inside a ``${...}`` hole opens a nested literal
    (`` `/a${q ? `?${q}` : ""}` ``), so each hole is skipped whole.
    """
    i, end = open_idx + 1, min(len(text), open_idx + SCAN_LIMIT)
    while i < end:
        ch = text[i]
        if ch == "`":
            return i
        if ch == "$" and text.startswith("{", i + 1):
            i = match_paren(text, i + 1, closer="}")
            if i < 0:
                return -1
        elif ch == "\\":
            i += 1
        i += 1
    return -1


def _parse_literal(text: str, syntax: StringSyntax) -> tuple[str, str, str] | None:
    """``(lowercased prefix, quote, body)`` when *text* is exactly one string literal."""
    text = text.strip()
    m = _literal_re(syntax.quotes, syntax.prefixes).match(text)
    if m is None:
        return None
    body, quote = m.group("body"), m.group("q")
    if quote in body and not (
        quote == "`"
        and quote in syntax.template_quotes
        and _template_end(text, m.start("q")) == len(text) - 1
    ):
        return None  # the literal ended and something else followed it
    return m.group("prefix").lower(), quote, body


def _interpolates(prefix: str, quote: str, syntax: StringSyntax) -> bool:
    # A triple quote interpolates when its single form does (Kotlin raw strings).
    return quote[0] in syntax.template_quotes or any(
        p in syntax.template_prefixes.lower() for p in prefix
    )


# The value recorded for a `self.X` the file assigns but does not settle: it
# resolves to nothing, so `self.X` is refused rather than read as a bare `X`.
_UNSETTLED = ""


def _lookup(name: str, constants: dict[str, str], syntax: StringSyntax) -> str | None:
    const = constants.get(name)
    if const is None and name.startswith("$"):
        const = constants.get(name[1:])  # a PHP variable, keyed without its sigil
    if const is None:
        for prefix in syntax.self_prefixes:
            if name.startswith(prefix):
                # A class's own member: its `self.X` binding first, else the bare `X`.
                bare = name[len(prefix) :]
                own = constants.get(syntax.self_prefixes[0] + bare)
                return own if own is not None else constants.get(bare)
    return const


def _fold(expr: str, syntax: StringSyntax, constants: dict[str, str]) -> str:
    """One interpolated expression as template text, inlining a plain constant.

    Only a constant whose own text does not interpolate is inlined: nothing in
    the corpus needs a second level, and a half-folded template would be wrong.
    """
    expr = expr.strip()
    const = _lookup(expr, constants, syntax)
    if const is not None:
        lit = _parse_literal(const, syntax)
        text = _literal_text(lit, syntax, {}) if lit is not None else None
        if text is not None and "${" not in text:
            return text
    return "${" + _TRAILING_CALL_RE.sub("", expr) + "}"


def _literal_text(
    parsed: tuple[str, str, str], syntax: StringSyntax, constants: dict[str, str]
) -> str | None:
    prefix, quote, body = parsed
    if any(p in syntax.refuse_prefixes for p in prefix):
        return None  # bytes, not text this layer records
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


def _format_template(text: str, syntax: StringSyntax, constants: dict[str, str]) -> str | None:
    """A ``format!("...")``-style call as template text, or ``None``."""
    if not syntax.format_heads:
        return None
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
    return _template_holes(body, syntax) if body is not None else None


def _template_holes(body: str, syntax: StringSyntax) -> str:
    assert syntax.placeholder is not None
    # `%%` is a literal percent sign, not a hole.
    return syntax.placeholder.sub(lambda h: "%" if h.group() == "%%" else "${x}", body)


def _format_method(text: str, syntax: StringSyntax, constants: dict[str, str]) -> str | None:
    """A ``"/x/{}".format(id)``-style call as template text, or ``None``."""
    if not syntax.format_method or not text.endswith(")"):
        return None
    dot = text.rfind(syntax.format_method + "(")
    if dot <= 0 or syntax.placeholder is None:
        return None
    if match_paren(text, dot + len(syntax.format_method)) != len(text) - 1:
        return None  # the call does not run to the end of the expression
    parsed = _parse_literal(text[:dot], syntax)
    if parsed is None or (syntax.refuse is not None and syntax.refuse.search(parsed[2])):
        return None  # `{{` is a literal brace no later step can tell from a hole
    body = _literal_text(parsed, syntax, constants)
    return _template_holes(body, syntax) if body is not None else None


def _whole_call_arguments(text: str, paren: int) -> list[str] | None:
    """The arguments of the call at *paren* when the call is all of *text*.

    ``URI.create(BASE).resolve("/x")`` is not ``URI.create(BASE)``: reading the
    head alone would drop the path the request reaches.
    """
    close = match_paren(text, paren)
    if close < 0 or text[close + 1 :].strip():
        return None
    return call_arguments(text, paren, close)


def _unwrap(text: str, syntax: StringSyntax) -> str | None:
    """The single argument of a ``URI.create(...)``-style wrapper, or ``None``."""
    m = _head_re(syntax.unwrap_heads).match(text)
    if m is None:
        return None
    args = _whole_call_arguments(text, m.end() - 1)
    return args[0] if args and len(args) == 1 else None


# A run of slashes a segment join leaves (`"api/catalog/" + "/items"`), not a scheme's.
_SLASH_RUN_RE = re.compile(r"(?<!:)/{2,}")


def _join(text: str, syntax: StringSyntax, constants: dict[str, str]) -> str | None:
    """``Url.Combine(base, "items")`` as ``${base}/items``, or ``None``.

    Each argument is a segment that must resolve, except the first: a bare
    name there is the base and becomes a ``${name}`` hole. A later name the
    file does not settle (``new Uri(url, UriKind.Relative)``) refuses the call.
    """
    if not syntax.join_heads:
        return None  # `_head_re(())` would match any text opening with `(`
    m = _head_re(syntax.join_heads).match(text)
    if m is None:
        return None
    args = _whole_call_arguments(text, m.end() - 1)
    if not args or len(args) < 2:
        return None
    parts: list[str] = []
    for i, arg in enumerate(a.strip() for a in args):
        value = resolve_string(arg, syntax, constants)
        if value is None:
            if i or not NAME_RE.fullmatch(arg):
                return None
            value = _fold(arg, syntax, constants)
        parts.append(value)
    joined = parts[0]
    for part in parts[1:]:
        joined = joined.rstrip("/") + "/" + part.lstrip("/")
    return _SLASH_RUN_RE.sub("/", joined)


def _concat(text: str, syntax: StringSyntax, constants: dict[str, str]) -> str | None:
    """``a + "/x"`` as template text, or ``None`` when a piece is not readable."""
    if not syntax.concat or syntax.concat not in text:
        return None
    pieces = split_top_level(text, syntax.concat)
    if len(pieces) < 2:
        return None
    out: list[str] = []
    for i, piece in enumerate(pieces):
        piece = piece.strip()
        parsed = _parse_literal(piece, syntax)
        if parsed is not None:
            body = _literal_text(parsed, syntax, constants)
            if body is None:
                return None
            out.append(body)
        elif NAME_RE.fullmatch(piece) or (i and _CONVERSION_RE.fullmatch(piece)):
            # `id.toString()` past the first piece is the value of `id`; the
            # first piece is the base, and a call there stays refused.
            out.append(_fold(piece, syntax, constants))
        else:
            return None  # a call or an operator: not settled in this file
    return "".join(out)


def resolve_string(
    expr: str, syntax: StringSyntax, constants: dict[str, str] | None = None
) -> str | None:
    """The text *expr* denotes, or ``None`` when it cannot be resolved here.

    Interpolations come out as ``${expr}``, so a URL's base placeholder can be
    stripped and a path parameter collapsed with no per-language branch.
    """
    constants = constants or {}
    text = expr.strip()
    parsed = _parse_literal(text, syntax)
    if parsed is not None:
        return _literal_text(parsed, syntax, constants)
    for fold in (_format_template, _format_method, _join, _concat):
        folded = fold(text, syntax, constants)
        if folded is not None:
            return folded
    if syntax.unwrap_heads:
        inner = _unwrap(text, syntax)
        if inner is not None:
            return resolve_string(inner, syntax, constants)
    if NAME_RE.fullmatch(text):
        # A bare name. Its value resolved without constants when it was
        # recorded, so this step cannot reach another bare name.
        const = _lookup(text, constants, syntax)
        if const is not None:
            return resolve_string(const, syntax, constants)
    return None


def inline_names(text: str, names: dict[str, str]) -> str:
    """Resolved template *text* with each ``${name}`` *names* holds replaced by its text.

    For values settled outside the file's own constants, whose text may keep a
    hole of its own (``${environment.apiUrl}/users``); :func:`_fold` inlines
    only hole-free constants.
    """
    return _TEMPLATE_INTERP_RE.sub(lambda m: names.get(m.group(1).strip(), m.group(0)), text)


def string_constants(
    content: str, syntax: StringSyntax, code: str | None = None
) -> dict[str, str]:
    """Names *content* assigns exactly once, to a string expression that resolves.

    The value is the right-hand side's raw text, so an interpolating literal,
    a format call or a concatenation keeps its parts for :func:`resolve_string`
    to fold at the use site.

    *code*, when given, is *content* with comments and string bodies blanked at
    the same offsets. Assignments are located in it and their values read from
    *content*, so the example code in a docstring (``    url = "/docs/example"``)
    is not read as a real assignment.
    """
    if syntax.assignment is None:
        return {}
    text = content if code is None else code
    seen: dict[str, str | None] = {}
    # `self.X = ...` (group `own`): kept apart, so it never folds a bare `X`.
    owned: dict[str, str | None] = {}
    for m in syntax.assignment.finditer(text):
        if code is None and on_comment_line(content, m.start()):
            continue  # an example in a doc comment is not a binding
        name = m.group("name")
        rhs = content[m.start("rhs") : m.end("rhs")].strip()
        if syntax.assignment_strip is not None:
            rhs = syntax.assignment_strip.sub("", rhs).strip()
        into = owned if m.groupdict().get("own") else seen
        # A second assignment retires the name whatever it assigns: the reader
        # cannot tell which one reaches the use site.
        into[name] = None if name in into or resolve_string(rhs, syntax) is None else rhs
    for name, rhs in owned.items():
        # A `self.X` that does not settle, or that a class or module `X` also
        # assigns, blocks the `self.X` lookup alone: the bare `X` keeps folding.
        unsettled = rhs is None or name in seen
        seen[syntax.self_prefixes[0] + name] = _UNSETTLED if unsettled else rhs
    if syntax.members is not None:
        for m in syntax.members.finditer(text):
            if code is None and on_comment_line(content, m.start()):
                continue
            _fold_members(content, m.end() - 1, m.group("name"), syntax, seen)
    # `x += "/v1"` and `x, err = f()` rebind a name without the plain form.
    for m in _COMPOUND_ASSIGN_RE.finditer(text):
        seen[m.group(1)] = None
    for m in _MULTI_ASSIGN_RE.finditer(text):
        for name in m.group(1).replace(" ", "").split(","):
            seen[name.lstrip("$")] = None
    return {name: text for name, text in seen.items() if text is not None}


_CLASS_OPEN_RE = re.compile(r"class(?<![\w$]class)\s+[A-Za-z_$][\w$]*[^{;]*\{")
# A class field and what it is set to: a declaration on its own line
# (`private base = x`, `base: string = x`) or `this.base = x` anywhere, with
# `op` set for a compound assignment (`this.base += x`).
_FIELD_DECL_RE = re.compile(
    r"^[ \t]*(?P<mods>(?:(?:private|protected|public|readonly|static|override)[ \t]+)*)"
    r"(?P<name>[A-Za-z_$][\w$]*)[ \t]*[?!]?(?:[ \t]*:[^=\n]+)?=(?![=>])[ \t]*(?P<rhs>[^\n]+)$",
    re.MULTILINE,
)
_THIS_ASSIGN_RE = re.compile(
    r"this\s*\.\s*(?P<name>[A-Za-z_$][\w$]*)\s*(?P<op>[-+*/%?|&]*)=(?![=>])[ \t]*(?P<rhs>[^\n;]*)"
)
_BARE_NAME_RE = re.compile(r"[A-Za-z_$][\w$]*")


@dataclass(frozen=True)
class ClassFields:
    """One class body's span and its fields: ``this.name -> rhs``, raw text."""

    start: int
    end: int
    fields: dict[str, str]


def js_class_fields(content: str) -> list[ClassFields]:
    """The fields each class in *content* sets exactly once, per class body.

    A declaration counts only directly in the body (not a method's local
    ``url = x``) and not when ``static`` (read as ``A.base``, not ``this.base``).
    A field set twice, or by ``+=``, is not a constant, nor one set to a bare
    name (``this.base = base``), usually a constructor parameter shadowing any
    constant of that name. Innermost classes come first.
    """
    spans: list[tuple[int, int]] = []
    for c in _CLASS_OPEN_RE.finditer(content):
        close = match_paren(content, c.end() - 1, len(content), closer="}")
        if close > 0:
            spans.append((c.end(), close))
    out: list[ClassFields] = []
    for start, end in sorted(spans, key=lambda s: s[1] - s[0]):
        inner = [s for s in spans if start < s[0] and s[1] < end]
        seen: dict[str, str | None] = {}
        depth, at = 0, start
        for m in _FIELD_DECL_RE.finditer(content, start, end):
            depth += content.count("{", at, m.start()) - content.count("}", at, m.start())
            at = m.start()
            if depth == 0 and "static" not in m.group("mods"):
                _record(seen, content, m)
        for m in _THIS_ASSIGN_RE.finditer(content, start, end):
            if not any(a <= m.start() < b for a, b in inner):
                _record(seen, content, m)
        fields = {name: rhs for name, rhs in seen.items() if rhs}
        if fields:
            out.append(ClassFields(start, end, fields))
    return out


def _record(seen: dict[str, str | None], content: str, m: re.Match[str]) -> None:
    if on_comment_line(content, m.start()):
        return
    name = "this." + m.group("name")
    rhs = m.group("rhs").strip()
    if JS_SYNTAX.assignment_strip is not None:
        rhs = JS_SYNTAX.assignment_strip.sub("", rhs).strip()
    refused = name in seen or m.groupdict().get("op") or _BARE_NAME_RE.fullmatch(rhs)
    seen[name] = None if refused else rhs


# One object or enum entry: comments before it, an identifier or quoted key,
# then `:` or `=`.
_MEMBER_RE = re.compile(
    r"""^(?:\s*(?://[^\n]*|/\*.*?\*/))*\s*"""
    r"""(?:(?P<id>[A-Za-z_$][\w$]*)|['"](?P<quoted>[\w$.-]+)['"])\s*[:=](?![=>])\s*(?P<value>.+?)\s*$""",
    re.DOTALL,
)
# A member value worth resolving: a string, a template, a name, or an object.
# A function body or a long expression is neither a constant nor cheap to read.
_MEMBER_VALUE_MAX = 512
_MEMBER_VALUE_START = frozenset("'\"`{$_") | frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


# A literal member value with a comment after it (`url: '/api' // was '/v1'`).
_LITERAL_THEN_COMMENT_RE = re.compile(
    r"""^(?P<lit>(?P<q>['"`])(?:(?!(?P=q))[^\\]|\\.)*(?P=q))\s*(?://[^\n]*|/\*.*?\*/)\s*$""",
    re.DOTALL,
)


def map_entry(entry: str) -> tuple[str, str, int] | None:
    """``(key, value source, value offset)`` of one ``key: value`` / ``key = value`` entry.

    Leading comments are skipped; a spread, a method or a bare enum member is
    ``None``.
    """
    m = _MEMBER_RE.match(entry)
    if m is None:
        return None
    return m.group("id") or m.group("quoted"), m.group("value"), m.start("value")


def _fold_members(
    content: str,
    brace: int,
    prefix: str,
    syntax: StringSyntax,
    seen: dict[str, str | None],
    close: int | None = None,
) -> None:
    """Record each entry of the object literal opening at *brace* as ``prefix.key``.

    A nested object folds one level deeper (``Q.orders.created``); an entry
    that is not a string expression retires its name like any assignment.
    """
    if close is None:
        close = match_paren(content, brace, closer="}")
    if close < 0:
        return
    _, seps = _scan(content, brace + 1, close, sep=",")
    for start, end in pairwise([brace, *seps, close]):
        member = map_entry(content[start + 1 : end])
        if member is None:
            continue
        key, value, value_at = member
        name = f"{prefix}.{key}"
        if value[0] in _QUOTES and "/" in value and len(value) <= _MEMBER_VALUE_MAX:
            value = _LITERAL_THEN_COMMENT_RE.sub(r"\g<lit>", value)
        if value[0] not in _MEMBER_VALUE_START or len(value) > _MEMBER_VALUE_MAX:
            seen[name] = None
            continue
        if value[0] == "{":
            if value.endswith("}"):
                offset = start + 1 + value_at
                _fold_members(content, offset, name, syntax, seen, offset + len(value) - 1)
            continue
        if syntax.assignment_strip is not None:
            value = syntax.assignment_strip.sub("", value).strip()
        seen[name] = None if name in seen or resolve_string(value, syntax) is None else value


# ``x += ...`` / ``$x .= ...``; the name is keyed without a PHP sigil.
_COMPOUND_ASSIGN_RE = re.compile(r"(?<![\w.$-])\$?([A-Za-z_]\w*)[ \t]*[+.]=(?!=)")
# ``a, b := f()`` / ``a, b = f()``: every name on the left is rebound.
_MULTI_ASSIGN_RE = re.compile(
    r"^[ \t]*(\$?[A-Za-z_]\w*(?:[ \t]*,[ \t]*\$?[A-Za-z_]\w*)+)[ \t]*:?=(?!=)", re.MULTILINE
)


def object_members(content: str, brace: int, syntax: StringSyntax) -> dict[str, str]:
    """``key -> value source`` for each string entry of the object literal opening at *brace*.

    The rules of a ``members`` constant: a nested object's keys are dotted, and
    an entry that is not a string expression is left out.
    """
    seen: dict[str, str | None] = {}
    _fold_members(content, brace, "", syntax, seen)
    return {name[1:]: value for name, value in seen.items() if value is not None}


# How far above a use a JS local's declaration is looked for.
_JS_LOCAL_WINDOW = 2_000


@cache
def _js_declaration_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?:var|let|const)\s+{re.escape(name)}\s*=(?![=>])")


@cache
def _js_rebinding_re(name: str) -> re.Pattern[str]:
    """An assignment to *name* or a member of it, or a parameter of that name."""
    n = re.escape(name)
    return re.compile(
        rf"(?<![\w$.]){n}\s*(?:\.\s*[\w$]+\s*|\[[^\]]*\]\s*)?[-+*/|&?]?=(?![=>])"  # `x =`, `x.uri =`, `x['uri'] =`
        rf"|Object\.assign\s*\(\s*{n}(?![\w$])"
        rf"|function(?![\w$])[^(]*\([^)]*(?<![\w$.]){n}(?![\w$])[^)]*\)"  # `function (err, x)`
        rf"|(?<![\w$.]){n}\s*=>|\([^()]*(?<![\w$.]){n}(?![\w$])[^()]*\)\s*=>"  # `x =>`, `(a, x) =>`
        # A method shorthand `save(x) {`; a control header (`if (x) {`) is not one.
        rf"|(?<![\w$.])(?!(?:if|for|while|switch|catch|with)\b)[A-Za-z_$][\w$]*\s*"
        rf"\([^()]*(?<![\w$.]){n}(?![\w$])[^()]*\)\s*\{{"
    )


# A line that continues the statement above it, or a statement left open by an operator.
_CONTINUES_RE = re.compile(r"\s*[-+*/.?:|&]")


def js_local_value(content: str, name: str, pos: int) -> str | None:
    """The value JS local *name* holds at *pos*, as source text, else ``None``.

    The right-hand side of the nearest ``var`` / ``let`` / ``const``
    declaration above *pos*, when the block declaring it is still open at
    *pos* and nothing assigns the name in between: ``var options = {...};
    request(options)``, where the file's constants refuse a name each handler
    declares again. A statement continued on the next line, an assignment to
    the name or one of its members, and a parameter of the same name between
    the two all refuse. Ceiling: only the last :data:`_JS_LOCAL_WINDOW`
    characters are read.
    """
    decl: re.Match[str] | None = None
    for m in _js_declaration_re(name).finditer(content, max(0, pos - _JS_LOCAL_WINDOW), pos):
        if not on_comment_line(content, m.start()):
            decl = m
    if decl is None:
        return None
    _, ends = _scan(content, decl.end(), pos, sep="\n", first_sep_only=True)
    if not ends:
        return None  # the statement runs into the use
    statement = content[decl.end() : ends[0]].rstrip()
    if not statement.endswith(";") and (
        _CONTINUES_RE.match(content, ends[0] + 1) or _CONTINUES_RE.match(statement[-1:] or ";")
    ):
        return None  # the expression goes on past the line
    rhs = split_top_level(statement, ",")[0].strip().rstrip(";").strip()
    between = "{" + content[ends[0] : pos]
    if _scan(between, 0, len(between), closer="}")[0] >= 0:
        return None  # the declaring block closed before the use
    if _js_rebinding_re(name).search(content, ends[0], pos):
        return None  # reassigned, mutated or shadowed before the use
    return rhs or None


# A typed local declaration (`var uri = `, `string url = `, `Uri? u = `). The
# word before the name is its type, unless it is a keyword (`else uri = x`).
_LOCAL_DECL = r"\b(?P<type>\w+)[>\[\]?]*[ \t]+{name}[ \t]*=(?![=>])"
_NOT_A_TYPE = frozenset({"else", "return", "await", "yield", "throw", "case", "do"})
# A modifier marks a field, whose other assignments may sit in any method.
_MEMBER_MODIFIER_RE = re.compile(r"\b(?:private|public|protected|internal|static|readonly|const)\b")
_BRACE_RE = re.compile(r"[{}]")
# How far above a use :func:`enclosing_local` looks for the declaration.
LOCAL_WINDOW = 8_000


def enclosing_local(content: str, code: str, name: str, offset: int) -> str | None:
    """The right-hand side of the local *name* a use at *offset* reads, else ``None``.

    For a brace language whose locals are block scoped (C#): the nearest
    declaration of *name* above *offset* counts only when its block still
    encloses the use and nothing reassigns *name* in between, so the same
    ``var uri`` in each of a file's methods reads as each method's own. A
    declaration with a modifier is a field and is not read here.
    *code* is *content* with comments and string bodies blanked at the same
    offsets. Only the :data:`LOCAL_WINDOW` characters above the use are read,
    so a file of many calls stays linear; a declaration further up is refused.
    """
    n = re.escape(name)
    decls = list(re.compile(_LOCAL_DECL.format(name=n)).finditer(code, max(0, offset - LOCAL_WINDOW), offset))
    if not decls or decls[-1].group("type") in _NOT_A_TYPE:
        return None
    decl = decls[-1]
    if _MEMBER_MODIFIER_RE.search(code, code.rfind("\n", 0, decl.start()) + 1, decl.start()):
        return None
    rhs = decl.end()
    semi = code.find(";", rhs, offset)
    if semi < 0:
        return None
    depth = 0
    for brace in _BRACE_RE.finditer(code, semi, offset):
        depth += 1 if brace.group() == "{" else -1
        if depth < 0:
            return None  # the declaring block closed before the use
    # Reassigned (`x = `, `x += `, `out x`, `ref x`, `(x, y) = `) or shadowed
    # by a lambda parameter (`x => `) between the declaration and the use.
    rebound = re.compile(
        rf"(?<![\w.]){n}\s*(?:[-+?]*=(?!=)|,[^;=]*\)\s*=(?![=>]))|\b(?:out|ref)\s+(?:var\s+)?{n}\b"
    )
    if rebound.search(code, semi, offset):
        return None
    return content[rhs:semi].strip()


def on_comment_line(content: str, offset: int) -> bool:
    """True when the line holding *offset* starts as a line or block comment."""
    start = max(content.rfind("\n", 0, offset) + 1, offset - 200)
    return content[start:offset].lstrip().startswith(("//", "#", "*", "/*"))


def unescape_backslashes(text: str) -> str:
    """A literal body's ``\\\\`` as the one backslash it stands for.

    Bodies are read raw, which keeps a path or queue name byte-exact; a value
    that is itself a pattern or a namespaced name (a Java regex, a PHP class
    in a JS string) is compared after this.
    """
    return text.replace("\\\\", "\\")


def literal_span(content: str, m: re.Match[str], group: int) -> str:
    """The source text of a quoted literal whose *body* is *group* of *m*.

    For a recogniser whose regex captures the text between the quotes: the
    slice one character wider on each side is the literal as written, which is
    what :func:`resolve_string` reads. A template literal whose capture stopped
    at a quote inside a ``${...}`` hole is read to its real closing backtick.
    Any other capture that stopped at a different quote is closed with its own
    opening quote, so the text read is the prefix the regex saw and the row is kept.
    """
    start, end = m.start(group), m.end(group)
    quote = content[start - 1]
    if quote == "`" and "${" in content[start:end]:
        end = max(end, _template_end(content, start - 1))
    return quote + content[start:end] + quote


# ---------------------------------------------------------------------------
# Selecting an argument
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Arg:
    """Where a call carries a value: a keyword or map key first, else a position.

    ``keys`` match a keyword argument (``queue='jobs'``, ``topics = "a"``) or a
    key of a map-literal argument (``{ topic: 'a' }``, ``['queue' => 'a']``).
    A value that is an array literal yields each element. ``variadic`` reads
    every positional argument from ``pos`` on (``ConsumeTopics("a", "b")``),
    keyword arguments left out.
    """

    keys: tuple[str, ...] = ()
    pos: int | None = None
    variadic: bool = False


# One map entry's key: a quoted string or an identifier, then `:` or `=>`.
_MAP_KEY_RE = re.compile(r"""^(?:'[^']*'|"[^"]*"|[A-Za-z_$][\w$]*)\s*(?:=>|:(?!:))""")


@cache
def _keyword_re(keys: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(re.escape(k) for k in keys)
    return re.compile(
        rf"""^['"]?(?:{alts})['"]?\s*(?:=>|:(?!:)|=(?!=))\s*(?P<value>.+)$""", re.DOTALL
    )


def _bracketed(text: str) -> bool:
    return len(text) >= 2 and text[0] in "[{" and text[-1] in "]}"


def _map_entries(text: str) -> list[str] | None:
    """The entries of a map literal (``{a: 1}``, ``['a' => 1]``), or ``None`` if *text* is not one."""
    if not _bracketed(text):
        return None
    entries = [e.strip() for e in split_top_level(text[1:-1], ",") if e.strip()]
    # `any`: a JS object may mix `key: value` with shorthand `{ topic, messages }`.
    if any(_MAP_KEY_RE.match(e) for e in entries):
        return entries
    return None


# A typed collection literal's head, before its `{`: `new[]`, `new string[]`,
# `new List<string>` (C#, Java arrays), `[]string` (Go).
_TYPED_COLLECTION_RE = re.compile(
    r"(?:new\s*(?:[A-Za-z_][\w.]*\s*(?:<[^<>]*>)?)?\s*(?:\[\s*\])?|\[\]\s*[A-Za-z_][\w.]*)\s*(?=\{)"
)
# A keyword argument, which a variadic read leaves out (`bootstrap_servers=x`).
_KEYWORD_ARG_RE = re.compile(r"^[A-Za-z_]\w*\s*=(?!=)")


def _elements(value: str) -> list[str]:
    """*value*'s elements if it is an array or typed collection literal, else ``[value]``."""
    head = _TYPED_COLLECTION_RE.match(value)
    if head is not None and value.endswith("}"):
        value = value[head.end() :]
    if _bracketed(value) and _map_entries(value) is None:
        return [e.strip() for e in split_top_level(value[1:-1], ",") if e.strip()]
    return [value]


def _keyword_value(args: list[str], keys: tuple[str, ...]) -> str | None:
    """The value of the first keyword argument or map entry named one of *keys*."""
    pattern = _keyword_re(keys)
    for a in args:
        for entry in _map_entries(a) or [a]:
            m = pattern.match(entry)
            if m is not None:
                return m.group("value").strip()
    return None


def select_argument(args: list[str], arg: Arg) -> list[str]:
    """The source text of the value(s) *arg* selects from *args*, arrays expanded."""
    value = _keyword_value(args, arg.keys) if arg.keys else None
    if value is None and arg.variadic and arg.pos is not None:
        rest = [a for a in args[arg.pos :] if not _KEYWORD_ARG_RE.match(a)]
        return [e for a in rest for e in _elements(a)]
    if value is None and arg.pos is not None and arg.pos < len(args):
        value = args[arg.pos]
    if value is None:
        return []
    return _elements(value)


def resolve_argument(
    args: list[str],
    arg: Arg,
    syntax: StringSyntax,
    constants: dict[str, str],
    normalize: Callable[[str], str | None] | None = None,
) -> tuple[list[str], bool]:
    """``(values, refused)`` for *arg*: each selected value resolved to plain text.

    A value that does not resolve, or resolves to a template with a hole in
    it, is dropped and ``refused`` is set, so a caller can tell "the call did
    not say" from "the call said something this file cannot settle".
    *normalize* maps the resolved text first (a queue URL to its last
    segment, a parameter to ``{param}``), so a hole it removes is no hole.
    """
    values: list[str] = []
    refused = False
    for raw in select_argument(args, arg):
        text = resolve_string(raw, syntax, constants)
        if text is not None and normalize is not None:
            text = normalize(text)
        if text is None or "${" in text:
            refused = True
        else:
            values.append(text.strip())
    return values, refused


# ---------------------------------------------------------------------------
# Per-language syntax tables
# ---------------------------------------------------------------------------

_C_FORMAT_PLACEHOLDER_RE = re.compile(r"%%|%[-+ #0]*\d*(?:\.\d+)?[a-zA-Z]")
# Node's `util.format` specifiers only, so `%20` in a URL stays text.
_NODE_FORMAT_PLACEHOLDER_RE = re.compile(r"%%|%[sdifjoOc]")
_BRACE_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_BRACE_INTERP_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")
_ESCAPED_BRACE_RE = re.compile(r"\{\{|\}\}")

# ``const NAME = <expr>`` only: a ``let`` or ``var`` may be reassigned by a
# plain ``NAME = ...`` this reader does not track. ``as const`` and the
# statement's ``;`` (with any comment after it) are not part of the value.
JS_SYNTAX = StringSyntax(
    quotes="\"'`",
    template_quotes="`",
    format_heads=("util.format",),
    placeholder=_NODE_FORMAT_PLACEHOLDER_RE,
    concat="+",
    assignment=re.compile(
        # The boundary is checked behind the literal, so the regex keeps its
        # literal prefix and is not tried at every offset of the file.
        r"const(?<![\w$]const)[ \t]+(?P<name>[A-Za-z_$][\w$]*)(?:[ \t]*:[^=\n]+)?[ \t]*=(?![=>])"
        r"[ \t]*(?P<rhs>[^\n]+)$",
        re.MULTILINE,
    ),
    assignment_strip=re.compile(r"\s*(?:as\s+const\s*)?;[^'\"`]*$|\s+as\s+const\s*$"),
    members=re.compile(
        r"(?:const(?<![\w$]const)|enum(?<![\w$]enum))[ \t]+(?P<name>[A-Za-z_$][\w$]*)[ \t]*(?::[^=\n{]+)?"
        r"(?:=[ \t]*(?:Object\.freeze[ \t]*\([ \t]*)?)?\{"
    ),
)

# ``NAME = <expr>`` at any indentation, RHS to end of line. A name with a
# lower-case letter needs whitespace around ``=``, which excludes the usual
# unspaced keyword argument on its own line (``url=str(resp.url),``) that
# otherwise reads as a second assignment and retires the very name being
# folded. An upper-case name is a constant by convention and may be unspaced
# (``EXCHANGE='robot-shop'``). A line after one ending in an open bracket or a
# comma continues a call or literal, so it assigns nothing. ``self.X = <expr>``
# folds ``self.X`` alone; one that does not settle, or whose ``X`` is also
# assigned bare, refuses ``self.X`` and leaves the bare ``X`` as it was.
# ``self.X`` and ``cls.X`` otherwise read the class attribute ``X``.
PYTHON_SYNTAX = StringSyntax(
    prefixes="fFrRbBuU",
    template_prefixes="f",
    interpolation=_BRACE_INTERP_RE,
    refuse=_ESCAPED_BRACE_RE,
    refuse_prefixes="b",
    format_method=".format",
    placeholder=_BRACE_PLACEHOLDER_RE,
    concat="+",
    assignment=re.compile(
        r"^(?<![(\[{,]\n)[ \t]*(?P<own>(?:self|cls)\.)?"
        r"(?P<name>[A-Z_][A-Z0-9_]*(?=[ \t]*=(?!=))|[A-Za-z_]\w*(?=(?:[ \t]*:[^=\n]+)?[ \t]+=[ \t]))"
        r"(?:[ \t]*:[^=\n]+)?[ \t]*=[ \t]*(?P<rhs>.*)$",
        re.MULTILINE,
    ),
    self_prefixes=("self.", "cls."),
)

RUST_SYNTAX = StringSyntax(
    quotes='"',
    format_heads=("format!",),
    placeholder=_BRACE_PLACEHOLDER_RE,
)

# No `assignment`: a C# name is folded only where a dialect checks it is a
# field (see `http/csharp_http.py`), never by file-wide single assignment.
CSHARP_SYNTAX = StringSyntax(
    quotes='"',
    prefixes="$@",
    template_prefixes="$",
    interpolation=_BRACE_INTERP_RE,
    refuse=_ESCAPED_BRACE_RE,
    # `new(...)` is target-typed: `Uri BaseAddress = new("http://x/")`.
    unwrap_heads=("new Uri", "new"),
    # `new Uri(baseUri, "items")`, Flurl's `Url.Combine`, and the `CombineUri`
    # helper eShop-style clients define. Ceiling: a helper is known by name only;
    # reading a same-repo helper's body is the upgrade if other names turn up.
    join_heads=("new Uri", "Url.Combine", "UriHelper.CombineUri", "CombineUri"),
    concat="+",
)

GO_SYNTAX = StringSyntax(
    quotes='"`',
    format_heads=("fmt.Sprintf",),
    placeholder=_C_FORMAT_PLACEHOLDER_RE,
    concat="+",
    assignment=re.compile(
        r"^[ \t]*(?:(?:const|var)[ \t]+)?(?P<name>[A-Za-z_]\w*)[ \t]*:?=[ \t]*(?P<rhs>[^\n]+)$",
        re.MULTILINE,
    ),
)

RUBY_SYNTAX = StringSyntax(
    template_quotes='"',
    interpolation=re.compile(r"#\{([^{}]+)\}"),
    unwrap_heads=("URI.parse", "URI"),
    concat="+",
    assignment=re.compile(
        r"^[ \t]*(?P<name>[A-Za-z_]\w*)[ \t]*=[ \t]*(?P<rhs>[^\n]+)$", re.MULTILINE
    ),
    assignment_strip=re.compile(r"\.freeze$"),
)

JAVA_SYNTAX = StringSyntax(
    quotes='"',
    format_heads=("String.format",),
    placeholder=_C_FORMAT_PLACEHOLDER_RE,
    unwrap_heads=("URI.create", "new URI", "new URL", "URI", "HttpUrl.parse"),
    concat="+",
    assignment=re.compile(
        r"(?<![=!<>.\w])(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)[ \t]*(?P<rhs>[^;\n]+);"
    ),
)

# Apex: `'` is the only quote and nothing interpolates. Ceiling: a constant is
# looked up in the case it is written, though Apex names are case-insensitive.
APEX_SYNTAX = StringSyntax(
    quotes="'",
    concat="+",
    assignment=JAVA_SYNTAX.assignment,
)

KOTLIN_SYNTAX = StringSyntax(
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

PHP_SYNTAX = StringSyntax(
    template_quotes='"',
    interpolation=re.compile(r"\{\$([^{}]+)\}|\$\{([^{}]+)\}|\$([A-Za-z_]\w*(?:->[A-Za-z_]\w*)?)"),
    concat=".",
    assignment=re.compile(
        r"(?:\bconst[ \t]+|\$)(?P<name>[A-Za-z_]\w*)[ \t]*=(?!=)[ \t]*(?P<rhs>[^;\n]+);"
    ),
    self_prefixes=("self::", "static::"),
)

_SYNTAX_BY_LANGUAGE: tuple[tuple[frozenset[str], StringSyntax], ...] = (
    (JS_TS, JS_SYNTAX),
    (PYTHON, PYTHON_SYNTAX),
    (JAVA, JAVA_SYNTAX),
    (KOTLIN, KOTLIN_SYNTAX),
    (GO, GO_SYNTAX),
    (RUBY, RUBY_SYNTAX),
    (PHP, PHP_SYNTAX),
    (CSHARP, CSHARP_SYNTAX),
    (RUST, RUST_SYNTAX),
)


def syntax_for_suffix(suffix: str) -> StringSyntax | None:
    """The syntax table for a file extension, or ``None`` for a language without one."""
    for extensions, syntax in _SYNTAX_BY_LANGUAGE:
        if suffix in extensions:
            return syntax
    return None


__all__ = [
    "APEX_SYNTAX",
    "CSHARP_SYNTAX",
    "GO_SYNTAX",
    "JAVA_SYNTAX",
    "JS_SYNTAX",
    "KOTLIN_SYNTAX",
    "NAME_RE",
    "PHP_SYNTAX",
    "PYTHON_SYNTAX",
    "RUBY_SYNTAX",
    "RUST_SYNTAX",
    "SCAN_LIMIT",
    "Arg",
    "ClassFields",
    "StringSyntax",
    "call_arguments",
    "enclosing_local",
    "inline_names",
    "js_class_fields",
    "js_local_value",
    "literal_span",
    "map_entry",
    "match_paren",
    "object_members",
    "on_comment_line",
    "resolve_argument",
    "resolve_string",
    "select_argument",
    "split_first_arg",
    "split_top_level",
    "string_constants",
    "syntax_for_suffix",
    "unescape_backslashes",
]
