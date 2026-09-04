"""Provenance primitives shared by the decision extractor and the persistence
layer: the source-ranking ladder, the corroboration-aware confidence formula,
and the anti-hallucination substring gate helpers.

This module deliberately depends on nothing from ``decision_extractor`` or the
persistence layer so both can import it without a cycle (``crud.py`` consumes
the ranking ladder + confidence formula; ``decision_extractor`` consumes the
verification helpers).

Note: this is the *decision* provenance subsystem and is unrelated to the
page-level provenance in ``generation.page_generator`` (``_attach_file_provenance``),
which records a page's source files / KG layer. They share a word, not a concern.
"""

from __future__ import annotations

import re

__all__ = [
    "LISTABLE_SOURCES",
    "MAX_SOURCE_RANK",
    "PLACEHOLDER_SOURCES",
    "RETIRED_SOURCES",
    "SOURCE_RANK",
    "compute_confidence",
    "normalize_text",
    "rank_for_source",
    "verify_quote",
]

# ---------------------------------------------------------------------------
# Source ranking ladder
# ---------------------------------------------------------------------------
#
# Higher rank = more authoritative provenance. When two sources describe the
# same decision, the headline fields come from the highest-rank evidence and
# the lower-rank source is retained as a corroborating evidence row (never
# discarded). ``test_name`` and ``inferred`` are placeholders for later phases
# but the full ladder is defined now so ranks are stable across phases.
#
# A person outranks a document. ``session`` sits above ``adr`` because a
# transcript carries the user's own words about a choice they were making at the
# time, corroborated by them in the moment; an ADR is someone's later write-up
# of it. With ``session`` below ``adr`` the promotion branch in
# ``crud.decisions`` (``>=``) never fired for a transcript, so a mined document
# overwrote the headline a user had actually said.
#
# ``adr`` and ``pr`` deliberately tie at 7: both are written-up accounts of a
# decision rather than the decision being made, and the ``>=`` comparison means
# the later-arriving one takes the headline. The set of *values* is unchanged by
# the session/adr swap, so ``MAX_SOURCE_RANK`` stays 9 and no other source's
# confidence moves.
SOURCE_RANK: dict[str, int] = {
    "cli": 9,  # human-authored manual entry — most authoritative
    "session": 8,  # a user's own words in a transcript, corroborated by them
    "adr": 7,  # architecture decision records (structured, intentional)
    "pr": 7,  # PR / squash-merge body
    "commit": 6,  # individual commit message
    "git_archaeology": 6,  # alias for commit-mined decisions
    "conventions": 6,  # counted from the code itself, so it sits with commit
    "changelog": 5,  # legacy rows from the removed changelog miner
    "inline_marker": 4,  # # WHY: / # DECISION: code markers
    "comment": 3,  # LLM-curated rationale prose on high-centrality code
    "readme_mining": 3,  # legacy rows from the removed README/docs miner
    "code_comment": 2,  # legacy rows from the removed comment harvest (#751)
    "test_name": 2,  # placeholder — behaviour asserted by a test name
    "inferred": 1,  # placeholder — purely inferred, no verbatim source
    # Legacy rows from the generation-time harvest, which is gone: it only ever
    # ran on file pages, and those are rendered structurally now. Deliberately
    # NOT in RETIRED_SOURCES yet — adding it drains surviving ``proposed`` rows
    # and the retired set is hand-mirrored in packages/ui, so that retirement is
    # its own cross-package change rather than a side effect of this one.
    "llm_inferred": 1,
}

MAX_SOURCE_RANK: int = max(SOURCE_RANK.values())

#: Sources that no longer extract, newest last. Their names stay in
#: ``SOURCE_RANK`` so rows written before each removal still rank instead of
#: falling to the unknown-source floor, but no extractor produces them and the
#: persist path drains whatever they left ``proposed``. One list, because the
#: purge, the ranking annotations and the extractor's own docs all have to agree
#: about which sources are gone; three hand-maintained copies is how the #751
#: drain ended up being the only one anybody remembered to wire.
RETIRED_SOURCES: tuple[str, ...] = ("code_comment", "readme_mining", "changelog")

#: Rungs defined ahead of the extractor that fills them, so the ladder's values
#: stay stable across phases. No row in any store carries these, which is why
#: anything offering the user a list of sources has to leave them out.
PLACEHOLDER_SOURCES: tuple[str, ...] = ("test_name", "inferred")

#: Sources a stored record can actually carry: the ladder minus the retired and
#: the not-yet-real. Derived, because the hand-written copy behind
#: ``decision list --source`` had drifted into offering a retired source while
#: omitting ``session``.
LISTABLE_SOURCES: tuple[str, ...] = tuple(
    sorted(name for name in SOURCE_RANK if name not in (*RETIRED_SOURCES, *PLACEHOLDER_SOURCES))
)

# Rank at/below which the only evidence is a heuristic rationale-comment harvest
# (``code_comment`` and the placeholder ``test_name``/``inferred`` tiers). A
# decision resting solely on a plain code comment is a weak signal and must not
# read as confident as a real ADR/commit-derived decision, so its confidence is
# decayed into a sub-tier below the 0.5 floor unless something stronger
# corroborates it (which lifts ``top_rank`` above this line).
_HEURISTIC_COMMENT_RANK: int = 2
_HEURISTIC_COMMENT_DECAY: float = 0.85


def rank_for_source(source: str | None) -> int:
    """Return the ranking ladder value for *source* (unknown → lowest rank)."""
    return SOURCE_RANK.get((source or "").lower(), 1)


# ---------------------------------------------------------------------------
# Confidence: a function of best source rank + corroboration + verification
# ---------------------------------------------------------------------------


def compute_confidence(
    top_rank: int,
    corroboration_count: int = 1,
    verification: str = "exact",
) -> float:
    """Confidence for a decision backed by ``corroboration_count`` evidence rows.

    Rises with the best source rank and with the number of *independent*
    corroborating sources; multiplicatively decayed when the strongest
    surviving evidence is only fuzzy- or un-verified. Bounded to ``[0, 0.99]``
    (we never claim certainty). Recency decay is deferred — staleness scoring
    already models age separately.
    """
    base = 0.4 + 0.5 * (top_rank / MAX_SOURCE_RANK)
    corroboration_bonus = min(0.12, 0.04 * max(0, corroboration_count - 1))
    conf = base + corroboration_bonus
    if verification == "fuzzy":
        conf *= 0.85
    elif verification == "unverified":
        conf *= 0.6
    # A decision backed only by a heuristic code-comment harvest sits a tier
    # below real ADR/commit-derived intent, so it never clears the 0.5 floor.
    if top_rank <= _HEURISTIC_COMMENT_RANK:
        conf *= _HEURISTIC_COMMENT_DECAY
    return round(max(0.0, min(0.99, conf)), 3)


# ---------------------------------------------------------------------------
# Anti-hallucination substring gate
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Collapse whitespace + lowercase for substring/token comparison."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip().lower()


def verify_quote(
    quote: str | None,
    source_text: str | None,
    *,
    fuzzy_threshold: float = 0.6,
) -> str:
    """Classify how well *quote* is grounded in *source_text*.

    Returns:
        ``"exact"``      — *quote* is a normalized substring of the source span.
        ``"fuzzy"``      — token overlap ≥ *fuzzy_threshold* (paraphrase / reflow).
        ``"unverified"`` — neither (likely hallucinated, or no source to check).
    """
    q = normalize_text(quote)
    src = normalize_text(source_text)
    if not q or not src:
        return "unverified"
    if q in src:
        return "exact"
    q_tokens = set(q.split())
    if not q_tokens:
        return "unverified"
    src_tokens = set(src.split())
    overlap = len(q_tokens & src_tokens) / len(q_tokens)
    return "fuzzy" if overlap >= fuzzy_threshold else "unverified"
