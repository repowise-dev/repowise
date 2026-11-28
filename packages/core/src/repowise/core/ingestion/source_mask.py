"""Blank comments, and optionally string bodies, out of Python and JS-family source.

Offsets are preserved, so a match in the masked text indexes the original.
Shared by the HTTP client extractors, convention mining and dead-code
analysis, none of which should read a name written in a comment as code.
"""

from __future__ import annotations

__all__ = ["can_mask", "mask_source"]

_JS_LIKE = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs"})

# A ``/`` opens a regex literal (rather than dividing) when the previous
# significant character cannot end an expression. The standard heuristic, and
# enough to stop a ``/\\)/`` corrupting a parenthesis scan.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%~^<>")

# A ``<`` after one of these opens a JSX tag, where a tag name follows it: the characters of
# ``_REGEX_PRECEDERS`` that no comparison follows.
_TAG_PRECEDERS = frozenset("(,=:[!&|?{};>}")


def _string_prefix(text: str, quote_idx: int) -> str:
    """The ``rb``/``f``/``u`` prefix letters immediately before a quote, if any."""
    k = quote_idx
    while k > 0 and text[k - 1] in "rRbBuUfF":
        k -= 1
    # Preceded by more identifier means those letters end a name, not a prefix.
    if k > 0 and (text[k - 1].isalnum() or text[k - 1] == "_"):
        return ""
    return text[k:quote_idx]


def _closes(quote: str, ch: str) -> bool:
    """Whether *ch* ends a JS string opened by *quote*.

    A quote or apostrophe string cannot run past its line: where one seems
    to, it is JSX text (``<p>Don't</p>``), not a string.
    """
    return ch == quote or (ch == "\n" and quote != "`")


def _opens_tag(text: str, i: int, prev_sig: str) -> bool:
    """Whether the ``<`` at *i* opens a JSX tag, not a comparison or a type argument list.

    What comes before ``<`` cannot end an expression: a character that precedes a regex, or the
    keyword ``return``, not a member of that name (``this.return``). A tag after any other keyword
    (``default``, ``case``) is not read as a tag, and no tag is read inside the ``${}`` of a
    template literal.
    """
    if prev_sig in _TAG_PRECEDERS:
        return True
    j = i
    while j and text[j - 1].isspace():
        j -= 1
    k = j - len("return")
    if text[max(k, 0) : j] != "return":
        return False
    if k and (text[k - 1].isalnum() or text[k - 1] in "_$"):
        return False
    # ``this.return < max`` names a member, also across spaces and a line break.
    while k and text[k - 1].isspace():
        k -= 1
    return not (k and text[k - 1] == ".")


def _opens_regex(text: str, i: int, prev_sig: str, in_tag: bool) -> bool:
    """Whether the ``/`` at *i* opens a regex literal.

    A ``/`` of a JSX tag does not: ``</`` closes a tag, and ``/>`` ends a self-closing tag after a
    braced attribute (``<A b={x} />``). Both follow a character that precedes a regex. *in_tag* is
    true where the scan is inside a tag, outside the braces of its attributes; without it a
    ``}`` before ``/>`` is a block or an object that ends a line, and the ``/`` opens a regex.
    """
    if i and text[i - 1] == "<":
        return False
    if in_tag and prev_sig == "}" and text[i + 1 : i + 2] == ">":
        return False
    return prev_sig in _REGEX_PRECEDERS


def can_mask(suffix: str) -> bool:
    """Whether :func:`mask_source` reads files with *suffix* (else returns them as-is)."""
    return suffix.lower() == ".py" or suffix.lower() in _JS_LIKE


def mask_source(text: str, suffix: str, *, strings: bool = False) -> str:
    """Blank out comments — and optionally string bodies — preserving offsets.

    Every masked character is replaced by a space and newlines are kept, so the
    result is the same length as *text* and indexes into it interchangeably.
    That is what lets the call scanner match against masked text while slicing
    arguments by the same offsets.

    Two callers, two needs. Sink detection masks strings as well, because a
    ``fetch(`` inside a comment or a string is not a call — without this, a
    symbol is confirmed an HTTP wrapper by a line like
    ``// used to fetch(path) directly``, which is the name-guessing failure
    reappearing through the back door. The call-site scanner masks comments
    only, since it still has to read the URL literal it is looking for.
    """
    if not can_mask(suffix):
        return text

    if suffix.lower() == ".py":
        # Python: ``#`` to end of line, plus string bodies when asked. Both the
        # triple-quoted delimiter and the ``r`` prefix are read, because getting
        # either wrong desynchronises the scan for the rest of the file — a
        # docstring containing a ``"`` would close early and leave real code
        # masked, and ``r"\"`` would swallow its own closing quote.
        out = list(text)
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "#":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
                continue
            if ch not in "'\"":
                i += 1
                continue
            delim = text[i : i + 3] if text[i : i + 3] in ("'''", '"""') else ch
            raw = "r" in _string_prefix(text, i).lower()
            i += len(delim)
            while i < n:
                if text[i] == "\\" and not raw:
                    if strings:
                        out[i] = " "
                        if i + 1 < n and text[i + 1] != "\n":
                            out[i + 1] = " "
                    i += 2
                    continue
                if text.startswith(delim, i):
                    i += len(delim)
                    break
                if strings and text[i] != "\n":
                    out[i] = " "
                i += 1
        return "".join(out)

    out = list(text)
    i, n = 0, len(text)
    # Stack of open template literals, so a nested `` `x` `` inside ``${...}``
    # is opened rather than read as closing the outer one.
    tmpl_depth: list[int] = []
    quote = ""
    prev_sig = ""
    # Inside a JSX tag: from its ``<`` to its ``>``, with the braces of its attributes counted.
    in_tag = False
    tag_braces = 0
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                if strings:
                    out[i] = " "
                    if i + 1 < n and text[i + 1] != "\n":
                        out[i + 1] = " "
                i += 2
                continue
            if quote == "`" and ch == "$" and i + 1 < n and text[i + 1] == "{":
                tmpl_depth.append(1)
                quote = ""
                i += 2
                continue
            if _closes(quote, ch):
                quote = ""
            elif strings:
                out[i] = " " if ch != "\n" else "\n"
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            for j in range(i, min(i + 2, n)):
                out[j] = " "
            i += 2
            continue
        if ch == "/" and _opens_regex(text, i, prev_sig, in_tag and not tag_braces):
            i += 1
            while i < n and text[i] != "\n":
                if text[i] == "\\":
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if text[i] == "/":
                    out[i] = " "
                    i += 1
                    break
                out[i] = " "
                i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif tmpl_depth and ch == "}":
            tmpl_depth.pop()
            quote = "`"
        elif tmpl_depth and ch == "{":
            tmpl_depth[-1] += 1
        elif in_tag:
            if ch == "{":
                tag_braces += 1
            elif ch == "}":
                tag_braces -= 1
            elif ch == ">" and not tag_braces:
                in_tag = False
        elif ch == "<" and _opens_tag(text, i, prev_sig):
            in_tag = True
        if not ch.isspace():
            prev_sig = ch
        i += 1
    return "".join(out)
