"""The values a JS/TS tool config gives named keys, read as text.

Tool configs are code (spread base configs, helper factories, env-chosen
values), so nothing here evaluates them. A key's value is the expression after
``key:`` / ``key =``, up to the first ``,`` / ``;`` / unmatched closing
bracket or line end outside brackets; the string literals inside it are what
the config names. Comments are dropped first, so a path a config only mentions
in one is not read. Dead-code analysis (files a config loads) and the
test-runner setup edges both read configs through this module.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Keys whose value names files a test runner loads before the tests it runs.
TEST_SETUP_KEYS: tuple[str, ...] = (
    "setupFiles",
    "setupFilesAfterEnv",
    "globalSetup",
    "globalTeardown",
)

_OPEN, _CLOSE, _QUOTES = b"([{", b")]}", b"\"'`"


def strip_comments(text: bytes) -> bytes:
    """*text* without ``//`` and ``/* */`` comments.

    Only comments outside string and template literals are dropped, so a
    ``/*`` or ``//`` inside one (``"docs/*"``, ``"https://..."``) stays text.
    A ``//`` right after ``:`` or a word character is kept too (an unquoted
    URL). Ceiling: a regex literal is read as code.
    """
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _QUOTES:
            end = min(_string_end(text, i) + 1, len(text))
            out += text[i:end]
            i = end
            continue
        if ch == ord("/") and text[i + 1 : i + 2] == b"*":
            close = text.find(b"*/", i + 2)
            if close >= 0:
                i = close + 2
                continue
        elif ch == ord("/") and text[i + 1 : i + 2] == b"/" and not _ends_url_scheme(text, i):
            line_end = text.find(b"\n", i)
            i = len(text) if line_end < 0 else line_end
            continue
        out.append(ch)
        i += 1
    return bytes(out)


def _ends_url_scheme(text: bytes, i: int) -> bool:
    """Whether the ``//`` at *i* follows ``:`` or a word character."""
    if i == 0:
        return False
    prev = text[i - 1]
    return prev in b":_" or bytes([prev]).isalnum()


@lru_cache(maxsize=16)
def _key_re(keys: tuple[str, ...]) -> re.Pattern[bytes]:
    names = b"|".join(re.escape(k.encode()) for k in keys)
    return re.compile(rb"""["']?\b(?:""" + names + rb""")\b["']?\s*[:=](?!=)""")


def value_spans(text: bytes, keys: tuple[str, ...]) -> list[bytes]:
    """The value expression of every occurrence of *keys* in a config's *text*."""
    text = strip_comments(text)
    return [text[m.end() : _value_end(text, m.end())] for m in _key_re(keys).finditer(text)]


def string_literals(span: bytes) -> list[str]:
    """The string literals in *span*, in order; a template literal with a hole is skipped."""
    out: list[str] = []
    i = 0
    while i < len(span):
        if span[i] in _QUOTES:
            end = _string_end(span, i)
            body = span[i + 1 : end]
            if not (span[i] == ord("`") and b"${" in body):
                out.append(body.decode("utf-8", errors="replace"))
            i = end + 1
            continue
        i += 1
    return out


# A plugin factory call: the last name segment ends in ``Plugin``
# (``createNextIntlPlugin(``, ``x.somePlugin(``). Ceiling: the one shape seen
# taking a loaded file; a ``with*`` wrapper taking a path is not read until a
# config shows one.
_PLUGIN_CALL_RE = re.compile(rb"(?<![\w$])(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*Plugin\s*\(")


def plugin_path_arguments(text: bytes) -> list[str]:
    """The string literal each plugin factory call in a config takes first.

    ``createNextIntlPlugin("./src/i18n/request.ts")`` hands next-intl a file to
    load. Any other call's literal (``resolve(...)``, ``glob.sync(...)``) is a
    path the config computes with, not one a tool loads.
    """
    text = strip_comments(text)
    out: list[str] = []
    for call in _code_matches(text, _PLUGIN_CALL_RE):
        arg = text[call.end() : _value_end(text, call.end())].strip()
        if arg[:1] in _QUOTES and _string_end(arg, 0) == len(arg) - 1:
            out.extend(string_literals(arg))
    return out


def _code_matches(text: bytes, pattern: re.Pattern[bytes]) -> list[re.Match[bytes]]:
    """Where *pattern* matches *text* outside any string or template literal.

    A plugin call written inside a literal (``"somePlugin('./x')"``) is text,
    not a call. Ceiling: a call inside a template literal's ``${...}`` hole is
    skipped too.
    """
    out: list[re.Match[bytes]] = []
    i = 0
    for match in pattern.finditer(text):
        while i < match.start():
            i = _string_end(text, i) + 1 if text[i] in _QUOTES else i + 1
        if i == match.start():
            out.append(match)
    return out


def _value_end(text: bytes, start: int) -> int:
    """Where the value expression starting at *start* ends.

    A line end before the value starts does not end it, so a key with its value
    on the next line still reads.
    """
    depth = 0
    started = False
    i = start
    while i < len(text):
        ch = text[i]
        if depth == 0 and (ch in b",;" or (ch == ord("\n") and started)):
            return i
        if ch in _QUOTES:
            i = _string_end(text, i) + 1
            started = True
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            if depth == 0:
                return i
            depth -= 1
        started = started or not chr(ch).isspace()
        i += 1
    return i


def _string_end(text: bytes, start: int) -> int:
    """Index of the quote closing the string opened at *start* (or the text's end)."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        if text[i] == ord("\\"):
            i += 2
            continue
        if text[i] == quote:
            return i
        i += 1
    return len(text)
