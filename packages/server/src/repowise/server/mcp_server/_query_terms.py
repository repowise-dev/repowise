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

# Verbs that frame what is asked ("where is X implemented", "how does X work")
# rather than name it. Code rarely says them, so a ranking that weighs rare
# words would let a page win on a class name that happens to hold one
# (``NotImplemented``). Retrieval still searches them; ranking and coverage
# do not count them.
QUESTION_FRAME_VERBS = frozenset(
    {
        "work",
        "works",
        "working",
        "implement",
        "implemented",
        "implementation",
        "handle",
        "handled",
        "handles",
        "handling",
        "happen",
        "happens",
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


def content_terms(
    question: str, *, max_terms: int | None = _MAX_TERMS, identifiers: bool = True
) -> list[str]:
    """Content words of *question*, deduplicated, longest first.

    Deliberately inclusive: plain lowercase English words are kept, unlike the
    identifier extractors elsewhere, which drop them because they are weak
    signals *on their own*. Callers are expected to corroborate: one common
    word must not be allowed to decide anything by itself.

    A camelCase or snake_case token contributes its parts as well as itself, so
    someone who writes ``persist_symbols`` and someone who writes "persist
    symbols" reach the same rows. With ``identifiers=False`` only the parts
    are kept: for text written in prose, which names ``persist_symbols`` by its
    words if at all. ``max_terms=None`` keeps every term.
    """
    seen: dict[str, None] = {}
    for raw in _WORD_RE.findall(question or ""):
        parts = re.split(r"[^A-Za-z0-9]+", split_humps(raw))
        for tok in (raw, *parts) if identifiers else parts:
            t = tok.lower()
            if len(t) < _MIN_TERM_LEN or t in STOPWORDS:
                continue
            seen.setdefault(t, None)
    return sorted(seen, key=lambda t: (-len(t), t))[:max_terms]


def path_words(text: str) -> set[str]:
    """Lower-case words of a path or sentence, stopwords dropped. Two letters
    count: ``go`` or ``ui`` is often the directory that tells definitions apart."""
    words = re.split(r"[^a-z0-9]+", split_humps(text).lower())
    return {w for w in words if len(w) >= 2 and w not in STOPWORDS}


def paths_the_question_names(
    name: str, paths: list[str], question: str, *, best_only: bool = False
) -> list[str]:
    """The files of *name*'s definitions whose path shares a word with *question*.

    The name's own words do not count: every definition shares them. With
    ``best_only``, only the paths sharing the most words: "the cloudflare pages
    handler" names ``cloudflare-pages/handler.ts`` over
    ``cloudflare-workers/serve-static.ts``.
    """
    asked = path_words(question) - path_words(name)
    shared = {p: len(asked & path_words(p.rsplit(".", 1)[0])) for p in paths}
    floor = max(shared.values(), default=0) if best_only else 1
    return [p for p in paths if shared[p] and shared[p] >= floor]
