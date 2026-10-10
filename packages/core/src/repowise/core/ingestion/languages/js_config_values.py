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

# A ``//`` after ``:`` is a URL (``https://``), not a comment.
_LINE_COMMENT_RE = re.compile(rb"(?<![:\w])//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(rb"/\*.*?\*/", re.DOTALL)
_OPEN, _CLOSE, _QUOTES = b"([{", b")]}", b"\"'`"


def strip_comments(text: bytes) -> bytes:
    """*text* without ``//`` and ``/* */`` comments."""
    return _LINE_COMMENT_RE.sub(b"", _BLOCK_COMMENT_RE.sub(b"", text))


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
