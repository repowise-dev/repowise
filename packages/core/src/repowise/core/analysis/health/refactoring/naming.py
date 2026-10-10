"""Deterministic identifier names for the code a plan asks you to create.

Shared by the detectors that name a new unit (Extract Helper's shared helper,
Extract Method's lifted helper) so one rule produces every ``suggested_name``
in the payload. The alternative — each detector slugging its own way — is how a
consumer ends up unable to say what a name means without knowing which detector
wrote it, which is the defect this module exists to not repeat.

Precision-first, and the same posture in both detectors: without semantics we
cannot name a block for what it *does*, so a name is anchored to something the
plan already knows for certain (where the helper lands, the value it produces,
or the name its author already wrote over it: a banner comment, a stage label)
and never guesses intent. It is an editable starting point, which is
how every surface frames it, not a claim about behaviour.
"""

from __future__ import annotations

import re

# A section banner names its block in a few words; a longer comment is an
# explanation, and a name built from it is a sentence.
_MAX_BANNER_WORDS = 5
_COMMENT_MARK = re.compile(r"^\s*(?:#+|//+|/\*+|\*+)|\*+/\s*$")
# Rules and separators drawn around a banner: ``# ---- Graph metrics ----``.
_RULE_CHARS = " \t-=~#*_+.─━═"
_ENUMERATION = re.compile(r"^(?:\d+[.)]|\[\d+\]|(?:step|pass)\s+\d+\s*[:.)])\s*", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_BANNER_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
# Comments that talk to a tool or a reviewer, not about the code below them.
_DIRECTIVES = frozenset(
    {"todo", "fixme", "xxx", "hack", "note", "noqa", "nosec", "pragma"}
    | {"eslint", "prettier", "istanbul", "c8", "biome", "nolint", "noinspection", "jshint"}
)
_ARTICLES = frozenset({"a", "an", "the"})
# ``with timed(timings, "persist.pages"):`` -- the stage label the timing
# helper already records for the block it opens.
_TIMED_LABEL = re.compile(r"\btimed\(\s*[^,()]+,\s*[\"']([A-Za-z_][\w.]*)[\"']")


def identifier_slug(label: str | None) -> str:
    """*label* reduced to a lowercase identifier-safe slug, or ``""``.

    Non-alphanumerics collapse to single underscores, so a path leaf like
    ``api-client`` becomes ``api_client``. A leading digit is prefixed with an
    underscore because it is not a valid identifier start in most languages.
    """
    if not label:
        return ""
    cleaned: list[str] = []
    prev_us = False
    for ch in label.lower():
        if ch.isalnum():
            cleaned.append(ch)
            prev_us = False
        elif not prev_us:
            cleaned.append("_")
            prev_us = True
    slug = "".join(cleaned).strip("_")
    if slug and slug[0].isdigit():
        slug = f"_{slug}"
    return slug


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
    dropped word, the same way ``identifier_slug`` treats them.
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


def banner_words(comment_lines: list[str]) -> list[str]:
    """The words of a section banner, or ``[]`` when the comment is not one.

    A banner is one line of short prose, optionally drawn between rules
    (``# ==== / # Environment / # ====`` or ``# -- Load edges --``), with a
    leading step number dropped. Two to ``_MAX_BANNER_WORDS`` plain words: one
    word is a heading (``# Decisions``), not a name for what the block does,
    and anything holding code, paths or punctuation (``# key -> value``) is
    prose about the code rather than its name.
    """
    content = []
    for line in comment_lines:
        text = _COMMENT_MARK.sub("", line).strip(_RULE_CHARS)
        if text:
            content.append(text)
    if len(content) != 1:
        return []
    text = _ENUMERATION.sub("", _PARENTHETICAL.sub("", content[0]).strip())
    tokens = text.strip().rstrip(".:").split()
    if not 2 <= len(tokens) <= _MAX_BANNER_WORDS:
        return []
    if not all(_BANNER_WORD.fullmatch(t) for t in tokens):
        return []
    if tokens[0].lower().split("-")[0] in _DIRECTIVES:
        return []
    return [w for t in tokens for w in split_words(t) if w not in _ARTICLES]


def label_words(statement_text: str) -> list[str]:
    """The words of a ``timed(..., "label")`` stage label opening a statement."""
    first_line = statement_text.split("\n", 1)[0]
    match = _TIMED_LABEL.search(first_line)
    return split_words(match.group(1)) if match else []
