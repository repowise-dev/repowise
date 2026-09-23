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
    ``placeholder`` holes. ``unwrap_heads`` name calls whose single argument
    *is* the value. ``concat`` is the string-concatenation operator, empty when
    the language's dialects do not fold concatenation. ``assignment`` locates
    ``name = rhs`` statements for constant folding; ``assignment_strip`` drops a
    trailing decoration from the right-hand side (Ruby's ``.freeze``).
    ``members`` locates a constant object or enum (``const Q = {``,
    ``enum Q {``), ending at its opening brace; each ``key: value`` or
    ``key = value`` entry folds as ``Q.key``.
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
    members: re.Pattern[str] | None = None


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
    ``)`` when the caller has already found it.
    """
    if close is None:
        close = match_paren(content, paren_offset)
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


def _parse_literal(text: str, syntax: StringSyntax) -> tuple[str, str, str] | None:
    """``(lowercased prefix, quote, body)`` when *text* is exactly one string literal."""
    m = _literal_re(syntax.quotes, syntax.prefixes).match(text.strip())
    if m is None:
        return None
    body = m.group("body")
    if m.group("q") in body:
        return None  # the literal ended and something else followed it
    return m.group("prefix").lower(), m.group("q"), body


def _interpolates(prefix: str, quote: str, syntax: StringSyntax) -> bool:
    # A triple quote interpolates when its single form does (Kotlin raw strings).
    return quote[0] in syntax.template_quotes or any(
        p in syntax.template_prefixes.lower() for p in prefix
    )


def _lookup(name: str, constants: dict[str, str]) -> str | None:
    const = constants.get(name)
    if const is None and name.startswith("$"):
        const = constants.get(name[1:])  # a PHP variable, keyed without its sigil
    if const is None and name.startswith(("self::", "static::")):
        const = constants.get(name.partition("::")[2])  # a PHP class constant
    return const


def _fold(expr: str, syntax: StringSyntax, constants: dict[str, str]) -> str:
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


def _unwrap(text: str, syntax: StringSyntax) -> str | None:
    """The single argument of a ``URI.create(...)``-style wrapper, or ``None``."""
    m = _head_re(syntax.unwrap_heads).match(text)
    if m is None:
        return None
    args = _whole_call_arguments(text, m.end() - 1)
    return args[0] if args and len(args) == 1 else None


def _concat(text: str, syntax: StringSyntax, constants: dict[str, str]) -> str | None:
    """``a + "/x"`` as template text, or ``None`` when a piece is not readable."""
    if syntax.concat not in text:
        return None
    pieces = split_top_level(text, syntax.concat)
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
        elif NAME_RE.fullmatch(piece):
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
    if syntax.format_heads:
        folded = _format_template(text, syntax, constants)
        if folded is not None:
            return folded
    if syntax.unwrap_heads:
        inner = _unwrap(text, syntax)
        if inner is not None:
            return resolve_string(inner, syntax, constants)
    if syntax.concat:
        folded = _concat(text, syntax, constants)
        if folded is not None:
            return folded
    if NAME_RE.fullmatch(text):
        # A bare name. Its value resolved without constants when it was
        # recorded, so this step cannot reach another bare name.
        const = _lookup(text, constants)
        if const is not None:
            return resolve_string(const, syntax, constants)
    return None


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
    for m in syntax.assignment.finditer(text):
        if code is None and _on_comment_line(content, m.start()):
            continue  # an example in a doc comment is not a binding
        name = m.group("name")
        rhs = content[m.start("rhs") : m.end("rhs")].strip()
        if syntax.assignment_strip is not None:
            rhs = syntax.assignment_strip.sub("", rhs).strip()
        # A second assignment retires the name whatever it assigns: the reader
        # cannot tell which one reaches the use site.
        seen[name] = None if name in seen or resolve_string(rhs, syntax) is None else rhs
    if syntax.members is not None:
        for m in syntax.members.finditer(text):
            if code is None and _on_comment_line(content, m.start()):
                continue
            _fold_members(content, m.end() - 1, m.group("name"), syntax, seen)
    # `x += "/v1"` and `x, err = f()` rebind a name without the plain form.
    for m in _COMPOUND_ASSIGN_RE.finditer(text):
        seen[m.group(1)] = None
    for m in _MULTI_ASSIGN_RE.finditer(text):
        for name in m.group(1).replace(" ", "").split(","):
            seen[name.lstrip("$")] = None
    return {name: text for name, text in seen.items() if text is not None}


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


def _on_comment_line(content: str, offset: int) -> bool:
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
    what :func:`resolve_string` reads. A capture that stopped at a different
    quote (one inside a ``${...}`` expression) is closed with its own opening
    quote, so the text read is the prefix the regex saw and the row is kept.
    """
    quote = content[m.start(group) - 1]
    body = content[m.start(group) : m.end(group)]
    return quote + body + quote


# ---------------------------------------------------------------------------
# Selecting an argument
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Arg:
    """Where a call carries a value: a keyword or map key first, else a position.

    ``keys`` match a keyword argument (``queue='jobs'``, ``topics = "a"``) or a
    key of a map-literal argument (``{ topic: 'a' }``, ``['queue' => 'a']``).
    A value that is an array literal yields each element.
    """

    keys: tuple[str, ...] = ()
    pos: int | None = None


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


def select_argument(args: list[str], arg: Arg) -> list[str]:
    """The source text of the value(s) *arg* selects from *args*, arrays expanded."""
    value: str | None = None
    if arg.keys:
        pattern = _keyword_re(arg.keys)
        for a in args:
            for entry in _map_entries(a) or [a]:
                m = pattern.match(entry)
                if m is not None:
                    value = m.group("value").strip()
                    break
            if value is not None:
                break
    if value is None and arg.pos is not None and arg.pos < len(args):
        value = args[arg.pos]
    if value is None:
        return []
    if _bracketed(value) and _map_entries(value) is None:
        return [e.strip() for e in split_top_level(value[1:-1], ",") if e.strip()]
    return [value]


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
_BRACE_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
_BRACE_INTERP_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")
_ESCAPED_BRACE_RE = re.compile(r"\{\{|\}\}")

# ``const NAME = <expr>`` only: a ``let`` or ``var`` may be reassigned by a
# plain ``NAME = ...`` this reader does not track. ``as const`` and the
# statement's ``;`` (with any comment after it) are not part of the value.
JS_SYNTAX = StringSyntax(
    quotes="\"'`",
    template_quotes="`",
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

# ``NAME = <expr>`` at any indentation, RHS to end of line. The whitespace
# around ``=`` is required, which excludes the usual unspaced keyword argument
# on its own line (``url=str(resp.url),``) that otherwise reads as a second
# assignment and retires the very name being folded.
PYTHON_SYNTAX = StringSyntax(
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

RUST_SYNTAX = StringSyntax(
    quotes='"',
    format_heads=("format!",),
    placeholder=_BRACE_PLACEHOLDER_RE,
)

CSHARP_SYNTAX = StringSyntax(
    quotes='"',
    prefixes="$@",
    template_prefixes="$",
    interpolation=_BRACE_INTERP_RE,
    refuse=_ESCAPED_BRACE_RE,
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
    "StringSyntax",
    "call_arguments",
    "literal_span",
    "map_entry",
    "match_paren",
    "resolve_argument",
    "resolve_string",
    "select_argument",
    "split_first_arg",
    "split_top_level",
    "string_constants",
    "syntax_for_suffix",
    "unescape_backslashes",
]
