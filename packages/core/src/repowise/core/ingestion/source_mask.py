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


def _string_prefix(text: str, quote_idx: int) -> str:
    """The ``rb``/``f``/``u`` prefix letters immediately before a quote, if any."""
    k = quote_idx
    while k > 0 and text[k - 1] in "rRbBuUfF":
        k -= 1
    # Preceded by more identifier means those letters end a name, not a prefix.
    if k > 0 and (text[k - 1].isalnum() or text[k - 1] == "_"):
        return ""
    return text[k:quote_idx]


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
            if ch == quote:
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
        if ch == "/" and prev_sig in _REGEX_PRECEDERS:
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
        if not ch.isspace():
            prev_sig = ch
        i += 1
    return "".join(out)
