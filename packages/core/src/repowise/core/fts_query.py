"""Turning a natural-language question into a SQLite FTS5 MATCH expression.

Lifted out of :mod:`repowise.core.persistence.search` when a second store
needed the same expression. It sits at the top of ``repowise.core`` rather than
beside its first caller because the second one — the episode store — is
stdlib-only by test (``tests/unit/precedent/test_structural_perf.py``) and
importing ``repowise.core.persistence`` would pull SQLAlchemy onto a hook path
whose budget was fought down from 965 ms to 155 ms by deleting three
module-level imports.

The escaping in here is the part worth sharing rather than rewriting:
:func:`meaningful_terms` reduces a query to ``[a-zA-Z0-9_]+`` tokens, so every
character that could change the shape of an FTS5 expression is gone before a
term is built, and the one path that quotes raw text doubles its quotes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

#: Shortest term that still gets a prefix wildcard. See :func:`match_term`.
PREFIX_MIN_CHARS = 4

#: A term matching more than this share of the corpus is dropped from the query
#: expression: it cannot discriminate between documents, and every one of them
#: drags thousands of candidates in for BM25 to sort out afterwards.
#: Env-tunable because the right ceiling is a property of a corpus's
#: vocabulary, and the only honest way to set it is to measure recall on the
#: corpus in question.
DF_CEILING = float(os.environ.get("REPOWISE_FTS_DF_CEILING", "0.20"))

#: Never let the ceiling empty a query. A question written entirely out of
#: common words keeps its rarest terms — a thin match beats no match.
MIN_KEPT_TERMS = 3

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")

#: Common English stop words to strip from FTS queries.
STOP_WORDS = frozenset(
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
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "am",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "about",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "we",
        "you",
        "he",
        "she",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "when",
        "where",
        "why",
        "not",
        "no",
        "so",
        "if",
        "or",
        "and",
        "but",
        "all",
        "each",
        "very",
        "just",
        "also",
        "than",
        "too",
        "only",
    }
)


def meaningful_terms(query: str) -> list[str]:
    """Alphanumeric tokens of *query*, minus stop words and single characters."""
    tokens = _TOKEN_RE.findall(query.lower())
    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]


def match_term(term: str) -> str:
    """One FTS5 term, prefix-matched only when it is long enough to be specific.

    A prefix wildcard is what earns morphological recall — ``invalidat*`` finds
    "invalidates" and "invalidation" — and it is also what turns a short token
    into a corpus-wide match: ``set*`` hits settings, setup, setter, setdefault.
    Below the cut the term is matched exactly, which costs the odd plural and
    buys back a candidate set that BM25 was being asked to sort out afterwards.
    """
    return f'"{term}"*' if len(term) >= PREFIX_MIN_CHARS else f'"{term}"'


def build_fts5_query(query: str, document_frequency: Callable[[str], int] | None = None) -> str:
    """Build an FTS5 MATCH expression from a natural-language query.

    Terms are OR-ed, because a developer question is a paraphrase of the page
    that answers it: its rarest words are frequently the asker's vocabulary
    rather than the page's, so requiring all of them (AND) excludes the right
    page far more often than it narrows usefully. Measured on a 3,678-page
    corpus, AND over the meaningful terms returned nothing at all for 65 of 99
    questions, and held the expected page for 25 of 99 against 99 of 99 for OR.

    What OR needs is not a different operator but fewer junk terms. A question
    carries words that are not stop words yet still match a large share of any
    code corpus ("file", "does", "page", "index"), and each one drags in
    thousands of pages that only BM25 tie-breaking then has to sort out. Given
    a *document_frequency* callable, terms matching more than :data:`DF_CEILING`
    of the corpus are dropped from the expression; the same measurement puts
    the median candidate set at 20% of the corpus rather than 65%. Without the
    callable the expression keeps every term, which is the prior behaviour.

    At least :data:`MIN_KEPT_TERMS` terms always survive — the rarest ones — so
    a question written entirely from common words still matches something.
    """
    meaningful = meaningful_terms(query)

    if not meaningful:
        # All stop words — fall back to exact phrase. The one place raw text
        # reaches the expression, so it is the one place quotes are doubled.
        safe = query.replace('"', '""')
        return f'"{safe}"'

    kept = meaningful
    if document_frequency is not None:
        total = document_frequency("")  # corpus size, by convention
        if total > 0:
            ceiling = DF_CEILING * total
            selective = [t for t in meaningful if document_frequency(t) <= ceiling]
            if len(selective) < MIN_KEPT_TERMS:
                # Every term is common. Keep the rarest few rather than none:
                # a thin match beats an empty one.
                selective = sorted(meaningful, key=document_frequency)[:MIN_KEPT_TERMS]
            kept = selective

    return " OR ".join(match_term(t) for t in kept)
