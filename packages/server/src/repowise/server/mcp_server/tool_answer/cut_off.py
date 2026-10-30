"""Whether a synthesized answer was cut off before it finished.

The provider's stop reason decides when it has one. Without it, a conservative
check of the text's last paragraph decides: an open code fence or inline code
span, a dangling heading or list marker, a trailing comma or colon, or a last
word that cannot end a sentence. A sentence with no period, a list item, or a
closed code block is not enough on its own, so complete answers in those
shapes are never flagged.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING_OR_MARKER = re.compile(r"^(?:#{1,6}\s.*|[-*+]|\d+[.)])$")
# Characters a finished sentence, item or value never ends on.
_OPEN_ENDINGS = frozenset(",:;([{=")
# Words a finished sentence never ends on. Kept short on purpose: a word that
# can close a sentence ("that", "logged in", "defaults to on") would flag
# complete answers.
_DANGLING_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "of", "for", "with", "by", "from", "at", "into",
        "via", "than", "if", "is", "are", "as", "its", "their", "whose", "such", "which",
    }
)  # fmt: skip
_CODE_SPAN = re.compile(r"`[^`]*`")
# A standalone last word: not the tail of a file name, path or identifier.
_LAST_WORD = re.compile(r"(?<![\w./-])([A-Za-z]+)$")


def answer_cut_off(answer_text: str, stop_reason: str | None) -> bool:
    """True when the answer text stops mid-sentence.

    A reported stop reason wins: ``"max_tokens"`` is cut, any other reported
    reason is a finished reply. Only an unreported one reads the text.
    """
    if stop_reason is not None:
        return stop_reason == "max_tokens"
    return _text_looks_cut(answer_text)


def _text_looks_cut(answer_text: str) -> bool:
    lines = answer_text.rstrip().split("\n")
    if not lines[-1].strip():
        return False
    if sum(1 for line in lines if _FENCE.match(line)) % 2:
        return True
    last = lines[-1].strip()
    if _FENCE.match(last):
        return False
    paragraph = _last_paragraph(lines)
    if paragraph.count("`") % 2 or _CODE_SPAN.sub("", paragraph).count("**") % 2:
        return True
    return _dangling_last_line(last)


def _dangling_last_line(last: str) -> bool:
    """A heading or bare list marker, an open ending, or a dangling last word."""
    if _HEADING_OR_MARKER.match(last):
        return True
    # A closed code span ends a value, whatever character it holds.
    if last.endswith("`"):
        return False
    core = last.rstrip("*_ ")
    if not core or core[-1] in _OPEN_ENDINGS:
        return True
    tail = _LAST_WORD.search(core)
    # A lone capital ("option A") is a name, not an article.
    word = tail.group(1) if tail else ""
    return word.lower() in _DANGLING_WORDS and not (len(word) == 1 and word.isupper())


def _last_paragraph(lines: list[str]) -> str:
    """The lines after the last blank line or closing fence."""
    start = 0
    for i, line in enumerate(lines):
        if not line.strip() or _FENCE.match(line):
            start = i + 1
    return "\n".join(lines[start:])
