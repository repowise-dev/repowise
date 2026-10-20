"""Per-kind checks that drop a pattern match the line's syntax shows is not a finding."""

from __future__ import annotations

import re

from .credentials import _string_end

# ``fstring_sql`` asks for a query (``SELECT`` with its ``FROM``) that
# interpolates a value where a bound parameter belongs: after a comparison or
# ``LIKE``. A table, a column, a fragment or a placeholder list cannot be bound,
# so building those into the text is the only way to write them, and a value
# interpolated there has no parameter fix to offer. Ceiling: ``IN ({ids})`` of
# joined values, and an identifier taken from a request, are not seen.
_SQL_FROM = re.compile(r"\bFROM\b")
_SQL_VALUE = re.compile(r"""(?:[=<>]|\b(?i:LIKE|ILIKE))\s*['"%]*\{(?!\{)""")
# A string literal continuing the one before it (implicit concatenation).
_STRING_CONTINUATION = re.compile(r"""\A\s*[rRbBfFuU]{0,2}['"]""")


def _interpolates_sql_value(line: str, start: int, lines: list[str], lineno: int) -> bool:
    """Whether the f-string query opening at *start* on *line* puts a value in its text.

    Only the literal is read, and the one opening the next line when the query
    runs on into it.
    """
    triple = line[start + 1 : start + 4]
    if triple in ('"""', "'''"):
        # A triple-quoted query runs to its closing delimiter, often lines on.
        text = "\n".join(lines[lineno - 1 : lineno + 29])
        end = text.find(triple, start + 4)
        query = text[text.find("SELECT", start) : end if end != -1 else len(text)]
        return bool(_SQL_FROM.search(query) and _SQL_VALUE.search(query))
    body = start + 2  # past ``f`` and the opening quote
    query = line[line.find("SELECT", start) : _string_end(line, body)]
    following = lines[lineno] if lineno < len(lines) else ""
    if (cont := _STRING_CONTINUATION.match(following)) is not None:
        query += "\n" + following[cont.end() : _string_end(following, cont.end())]
    return bool(_SQL_FROM.search(query) and _SQL_VALUE.search(query))


# Python's own declaration that a digest is a checksum or a cache key, not a
# security control (``hashlib.sha1(data, usedforsecurity=False)``).
_NOT_FOR_SECURITY = re.compile(r"\busedforsecurity\s*=\s*False\b")


def _call_text(masked: list[str], lineno: int, start: int, limit: int = 600) -> str:
    """The code from *start* on line *lineno* through the first call's closing paren.

    Read from *masked* (strings and comments blanked, offsets kept), so a ``)``
    or the keyword inside a string neither ends the call nor counts as code.
    Bounded by *limit* characters.
    """
    code = "\n".join(masked[lineno - 1 : lineno + 9])[start : start + limit]
    depth = 0
    for i, char in enumerate(code):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth <= 0:
                return code[: i + 1]
    return code


# ``.hexdigest()[:8]`` or ``.digest("hex").slice(0, 8)`` right after the call.
_TRUNCATED_DIGEST = re.compile(
    r"\A(?:\s*\.\s*update\s*\([^()]*(?:\([^()]*\)[^()]*)*\))*"
    r"\s*\.\s*(?:hex)?digest\s*\([^()]*\)\s*"
    r"(?:\[\s*0?\s*:\s*(\d{1,2})\s*\]|\.\s*(?:slice|substring|substr)\s*\(\s*0\s*,\s*(\d{1,2})\s*\))"
)
_ID_PREFIX_MAX = 16  # longer is most of the digest
# The assignment target of a digest that names content.
_DIGEST_KEY_NAME = re.compile(
    r"(?i)(?<![a-z])(?:e_?tag|cache_?key|slug)s?"
    r"""(?:["']?\s*:\s*|(?:\s*:\s*[\w.|<>\[\] ]+)?\s*=(?!=))[^=:]*\Z"""
)
_CREDENTIAL_WORD = re.compile(
    r"(?i)passw|pwd|\bpw\b|\bpass\b|salt|\botp\b|bearer|secret|token|credential"
    r"|api_?key|auth(?!or)"
)


def _weak_hash_is_an_id(line: str, masked: list[str], lineno: int, start: int) -> bool:
    """Whether the weak hash at *start* on *line* is a declared non-security
    use: ``usedforsecurity=False``, a digest truncated to a short prefix where
    it is computed, or one assigned to a cache key, ETag or slug. A line that
    names a credential is only ever exempt by ``usedforsecurity=False``."""
    call = _call_text(masked, lineno, start)
    if _NOT_FOR_SECURITY.search(call):
        return True
    # ``hashlib.md5`` passed to ``hmac.new`` names a MAC's algorithm, not an id.
    if _CREDENTIAL_WORD.search(line) or "(" not in call:
        return False
    if "\n" not in call:
        code = masked[lineno - 1] if lineno <= len(masked) else line
        cut = _TRUNCATED_DIGEST.match(code[start + len(call) :])
        if cut and int(cut.group(1) or cut.group(2)) <= _ID_PREFIX_MAX:
            return True
    return bool(_DIGEST_KEY_NAME.search(line[:start]))


_TS_TYPE_VALUE = re.compile(
    r"(?:string|number|boolean|any|unknown|never|undefined|null|TrustedHTML)\b"
    r"(?:\s*\|\s*[\w$.]+)*\s*"
)
# What opens an object type on the line: an annotation ``:``, a generic ``<``,
# ``as``, ``interface X`` or ``type X =``. A ternary's ``:`` reads the same; the
# rare ``c ? a : {__html: string}`` is the accepted miss.
_TYPE_OPENER = re.compile(
    r"(?:[:<]|\bas|\binterface\s+[\w$]+[^{=]*|\btype\s+[\w$]+[^=]*=)\s*$"
)


def _is_html_type_annotation(line: str, match: re.Match) -> bool:
    """Whether ``__html: <type keyword>`` sits in a type, not an object value.

    A type keyword counts only in a type position: a member closed by ``;``, or
    an object type opened by ``_TYPE_OPENER``. ``{{__html: string}}`` and
    ``= {__html: string}`` read a variable named ``string`` and stay findings.
    """
    keyword = _TS_TYPE_VALUE.match(line, match.end())
    if keyword is None:
        return False
    if line.startswith(";", keyword.end()):
        return True
    depth = 0
    for i in range(match.start() - 1, -1, -1):
        if line[i] == "}":
            depth += 1
        elif line[i] == "{":
            if depth == 0:
                return bool(_TYPE_OPENER.search(line[:i]))
            depth -= 1
    return False


def _match_starting_in(pattern: re.Pattern, line: str, kept: str) -> re.Match | None:
    """First match of *pattern* on *line* whose first character survived masking in *kept*."""
    for match in pattern.finditer(line):
        start = match.start()
        if start < len(kept) and not kept[start].isspace():
            return match
    return None
