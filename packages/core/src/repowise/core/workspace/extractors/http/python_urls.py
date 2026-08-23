"""Resolve a Python HTTP call's URL argument to text this package can normalize.

Python spells a client call's URL two ways the whole-literal test in
:mod:`.index_clients` cannot read: an f-string (``f"{base}/users"``), and a name
bound to one earlier (``url = f"..."`` then ``client.post(url)``). Both are
settled where they are written, so reading them is substitution rather than
analysis, and no dataflow is claimed by doing it.

**What folds and what is refused.** A name folds only when the file assigns it
exactly once, to a single string literal on one line. Assigned twice, or
assigned anything else anywhere in the file, it is left unresolved and the call
is *counted* as unresolved rather than guessed. File scope, not lexical scope: a
single static assignment means the same thing wherever it is read.

f-string interpolations come out in the ``${expr}`` form the rest of this
package already speaks, so :func:`.paths.strip_leading_base_expr` strips a
Python base placeholder and :func:`.paths.normalize_http_path` collapses a
Python path parameter with no per-language branch in either.
"""

from __future__ import annotations

import re

# A whole-argument string literal, prefix included. ``.*`` is greedy against an
# anchored end, so the quote-inside check below is what rejects a concatenation.
_LITERAL_RE = re.compile(
    r"^(?P<prefix>[A-Za-z]*)(?P<q>'''|\"\"\"|'|\")(?P<body>.*)(?P=q)$", re.DOTALL
)

_NAME_RE = re.compile(r"[A-Za-z_]\w*")

# ``NAME = <expr>`` at any indentation, RHS to end of line. The whitespace
# around ``=`` is required, which excludes the usual unspaced keyword argument
# on its own line (``url=str(resp.url),``) — that otherwise reads as a second
# assignment and retires the very name being folded. A *spaced* kwarg still
# retires it, so the guard is partial; either way the cost is a lost fold, never
# a fabricated path.
_ASSIGN_RE = re.compile(
    r"^[ \t]*([A-Za-z_]\w*)(?:[ \t]*:[^=\n]+)?[ \t]+=[ \t]+(.*)$", re.MULTILINE
)

# One ``{expr}`` interpolation.
_INTERP_RE = re.compile(r"(?<!\{)\{([^{}]+)\}(?!\})")

# ``{{`` / ``}}`` are an escaped brace, which no downstream step distinguishes
# from a path parameter: ``normalize_http_path`` would eat one half and leave
# the other dangling, and the surviving ``}`` passes the unusable-path check.
# A URL needing a literal brace is refused rather than corrupted.
_ESCAPED_BRACE_RE = re.compile(r"\{\{|\}\}")

# Dropped from a base expression (``settings.base.rstrip('/')``) so
# :func:`.paths.base_token_identifier` reads the attribute, not ``rstrip``.
_TRAILING_CALL_RE = re.compile(r"\.\s*\w+\s*\([^()]*\)\s*$")


def _parse_literal(text: str) -> tuple[str, str] | None:
    """``(lowercased prefix, body)`` when *text* is exactly one string literal."""
    m = _LITERAL_RE.match(text.strip())
    if m is None:
        return None
    body = m.group("body")
    if m.group("q") in body:
        return None  # the literal ended and something else followed it
    return m.group("prefix").lower(), body


def string_constants(content: str, code: str) -> dict[str, str]:
    """Names this file assigns exactly once, to one string literal.

    The value is the literal's raw text with its prefix, so an f-string keeps
    its interpolations for :func:`resolve_url_argument` to fold.

    Assignments are located in *code* — the same text as *content* with comments
    and string bodies blanked — and their values are then read from *content* at
    the same offsets. Searching *content* directly would read the example code
    in a docstring (``    url = "/docs/example"``) as a real assignment.
    """
    seen: dict[str, str | None] = {}
    for m in _ASSIGN_RE.finditer(code):
        name, rhs = m.group(1), content[m.start(2) : m.end(2)].rstrip()
        # A second assignment retires the name whatever it assigns: the reader
        # cannot tell which one reaches the call site.
        seen[name] = None if name in seen or _parse_literal(rhs) is None else rhs.strip()
    return {name: text for name, text in seen.items() if text is not None}


def _template(body: str, constants: dict[str, str]) -> str:
    """An f-string body as ``${expr}`` text, folding names bound to a plain string."""

    def sub(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        const = constants.get(expr)
        if const is not None:
            inner = _parse_literal(const)
            # Only a plain literal folds: an f-string constant would need its
            # own interpolations resolved, which no case in the corpus needs.
            if inner is not None and "f" not in inner[0]:
                return inner[1]
        return "${" + _TRAILING_CALL_RE.sub("", expr) + "}"

    return _INTERP_RE.sub(sub, body)


def resolve_url_argument(arg: str, constants: dict[str, str]) -> str | None:
    """The URL text of *arg*, or ``None`` when it cannot be resolved."""
    text = arg.strip()
    parsed = _parse_literal(text)
    if parsed is None:
        # A bare name. Its folded value is a literal by construction, so one
        # step always reaches the text.
        if _NAME_RE.fullmatch(text) is None:
            return None
        folded = constants.get(text)
        parsed = _parse_literal(folded) if folded is not None else None
        if parsed is None:
            return None
    prefix, body = parsed
    if "b" in prefix:
        return None  # bytes, not a URL this layer records
    if "f" not in prefix:
        return body
    return None if _ESCAPED_BRACE_RE.search(body) else _template(body, constants)
