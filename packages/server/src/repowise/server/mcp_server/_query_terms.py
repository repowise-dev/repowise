"""The words of a question that could plausibly name code.

Stdlib-only and dependency-free on purpose: the stopword set is shared by
get_answer's coverage re-ranker (via ``tool_answer.config``) and by the
prose-keyed symbol leg, and neither should have to import the other to reach
it.
"""

from __future__ import annotations

import re

# English stopwords. Minimal list, just enough to keep "what is the" from
# dominating coverage. Not language-specific, not repo-specific.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "and",
        "or",
        "but",
        "not",
        "no",
        "do",
        "does",
        "did",
        "done",
        "have",
        "has",
        "had",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "how",
        "can",
        "could",
        "should",
        "would",
        "may",
        "might",
        "will",
        "shall",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "their",
        "our",
        "if",
        "then",
        "than",
        "so",
        "such",
        "there",
        "here",
        "about",
        "into",
        "through",
        "between",
        "across",
        "over",
        "under",
        "up",
        "down",
        "out",
        "off",
        "via",
    }
)

# Terms shorter than this match too much of any identifier to be worth a
# lookup; it is also the floor the rest of the answer pipeline already uses.
_MIN_TERM_LEN = 3

# Cap so a pasted stack trace or a whole bug report cannot turn one question
# into fifty table scans. Longest-first: a longer word is more specific, the
# same intuition an IDF weight encodes.
_MAX_TERMS = 8

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def split_humps(text: str) -> str:
    """``HTTPServerPool`` -> ``HTTP Server Pool``; ``a_b`` -> ``a_b`` (unchanged)."""
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", out)


def content_terms(question: str, *, max_terms: int = _MAX_TERMS) -> list[str]:
    """Content words of *question*, deduplicated, longest first.

    Deliberately inclusive: plain lowercase English words are kept, unlike the
    identifier extractors elsewhere, which drop them because they are weak
    signals *on their own*. Callers are expected to corroborate: one common
    word must not be allowed to decide anything by itself.

    A camelCase or snake_case token contributes its parts as well as itself, so
    someone who writes ``persist_symbols`` and someone who writes "persist
    symbols" reach the same rows.
    """
    seen: dict[str, None] = {}
    for raw in _WORD_RE.findall(question or ""):
        for tok in (raw, *re.split(r"[^A-Za-z0-9]+", split_humps(raw))):
            t = tok.lower()
            if len(t) < _MIN_TERM_LEN or t in STOPWORDS:
                continue
            seen.setdefault(t, None)
    return sorted(seen, key=lambda t: (-len(t), t))[:max_terms]
