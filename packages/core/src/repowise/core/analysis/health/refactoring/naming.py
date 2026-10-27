"""Identifier words: split a name into its words, and join words in a
language's casing. Shared by the plan renderer (a parameter object's type
name) and Extract Method's placeholder helper name.
"""

from __future__ import annotations


def _starts_new_word(ch: str, current: list[str]) -> bool:
    """True when *ch* opens a new word after the letters collected in *current*.

    A capital that follows a lowercase letter or a digit starts a new word
    (``meanValue``, ``file2Name``). A capital after a capital does not, so a run
    of capitals stays one word.
    """
    if not current:
        return False
    if not ch.isupper():
        return False
    return current[-1].islower() or current[-1].isdigit()


def split_words(label: str | None) -> list[str]:
    """*label* split into its words, lowercased.

    Splits on any non-alphanumeric run and on a lower-to-upper boundary, so
    ``meanValue``, ``mean_value`` and ``MeanValue`` all give ``["mean", "value"]``.
    A run of capitals stays one word (``HTTPStatus`` -> ``["httpstatus"]``),
    because splitting it needs a dictionary to tell ``HTTPS`` from ``HttpStatus``
    and this module names code, it does not read it.

    Letters are tested with ``str.isalnum`` rather than an ASCII character class,
    so a non-ASCII identifier keeps its characters instead of losing them to a
    dropped word.
    """
    if not label:
        return []
    words: list[str] = []
    current: list[str] = []
    for ch in label:
        if ch.isalnum() and not _starts_new_word(ch, current):
            current.append(ch)
            continue
        words.append("".join(current))
        current = [ch] if ch.isalnum() else []
    words.append("".join(current))
    return [w.lower() for w in words if w]


def join_identifier(words: list[str], convention: str) -> str:
    """*words* rendered in *convention*: ``"snake_case"`` or ``"camelCase"``."""
    if not words:
        return ""
    if convention == "camelCase":
        head, *rest = words
        return head + "".join(w.capitalize() for w in rest)
    return "_".join(words)
