"""Telling a working agreement from an architectural decision.

Deterministic and deliberately one-sided. A match means the claim is about an
act of conducting work rather than about the code, and anything unmatched stays
architectural, because the two errors do not cost the same: a decision wrongly
left architectural keeps exactly the contract it already had, while one wrongly
called an agreement stops being checked against the code at all.

No model call. This runs on the keyless path and inside the migration, neither
of which has a provider, and a keyword list fitted to one repository's English
is the mistake the staleness score already made once. So the patterns name acts
a repository cannot contain rather than topics it happens to discuss: branching,
committing, authoring a pull request body, running a formatter.

Ceiling, measured over 137 hand-classified records against two independent
raters: 93.3% precision at 46.7% recall. Both misses are records that bundle an
architectural claim with an attribution one, and neither the primary clause nor
the ``needs_split`` flag separates them, so they are a known cost rather than an
open bug. Recall is half on purpose; the unmatched half is not misfiled, it is
left in the class that still gets checked.
"""

from __future__ import annotations

import re

from .lifecycle import AGREEMENT_KIND, ARCHITECTURAL_KIND

__all__ = ["classify_kind"]

#: The sources that mine conversational prose, which is the only place an
#: agreement about conducting the work is ever stated. ``session_discovery``
#: reads the same transcripts through a broader gate, so it sees the same
#: sentences.
_PROSE_SOURCES: frozenset[str] = frozenset({"session", "session_discovery"})

#: Acts of conducting work, grouped by what they name. Each is something a
#: person does to the repository or around it, never something the repository
#: holds, which is what makes a diff unable to violate any of them.
_AGREEMENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Version control: the branch, the staging area, the push. "commit to"
        # is narrowed to a branch name on purpose, because a decision that
        # "commits to one source of truth" is ordinary prose and not VCS.
        r"\bgit (add|commit|push|pull|branch|checkout|stash|reset)\b"
        r"|\bcommit (directly )?to (the )?[`'\"]?(main|master|trunk)\b"
        r"|\bbranch(es)? (from|off) (the )?[`'\"]?(main|master|trunk)\b"
        r"|\bworktrees?\b"
        r"|\bpull request(s)? (bod|without|for)|\bopen(ing)? a pull request\b"
        r"|\bmerg(e|ing) (a |the )?(contributor )?PRs?\b",
        # What a commit message or a pull request body may say. Not repository
        # files, which is precisely why these are agreements and a rule about
        # user-facing product text is not.
        r"co-authored-by"
        r"|\b(commit|PR|pull request|Claude|AI) attribution\b"
        r"|\bAI references\b"
        r"|\battribution (in|to|from) (commits|pull request)",
        # Commands that are run rather than code that is written.
        r"\bruff format\b|\bprettier\b|\buv sync\b|\bredeploy\b"
        r"|\brun (only )?the (full )?test suite\b",
        # Which checkout may be touched at all.
        r"\b(main checkout|primary checkout)\b"
        r"|\bdo not (touch|modify|edit) (the )?(frontend|backend)"
        r"|\bnever modify\b.*\b(frontend|backend)",
    )
)


def classify_kind(
    title: str, decision: str, rationale: str = "", *, source: str = ""
) -> str:
    """Which noun *title* and *decision* describe.

    Returns the architectural kind unless the text names an act of conducting
    work, so a caller that cannot tell is never the reason a record stops being
    checked against the code.

    Only :data:`_PROSE_SOURCES` is asked at all. Every other source reads an
    artifact somebody already wrote *about* the code, where the same words mean
    something else: a comment saying "keep package attribution in one place" is
    about attributing code to packages, not about who signs a commit. Measured
    on the dev store, asking the other sources produced two hits and both were
    wrong.
    """
    if source not in _PROSE_SOURCES:
        return ARCHITECTURAL_KIND
    text = f"{title} {decision} {rationale}"
    if any(p.search(text) for p in _AGREEMENT_PATTERNS):
        return AGREEMENT_KIND
    return ARCHITECTURAL_KIND
