"""Anti-hallucination substring gate — orchestration shared by every path
that produces decision candidates (the multi-source extractor *and* the
Phase-2 LLM-docs harvest).

The gate is the product guarantee: a ``decision`` / ``rationale`` /
``source_quote`` field that does not substring- or token-match the verbatim
source span it claims to come from is **dropped**, and a candidate whose every
produced field is ungrounded is **rejected**. It became critical in Phase 2
because the page generator now *generates* decision candidates rather than only
reading them — the gate is what stops a fluent-but-invented rationale from being
stored as institutional memory.

This module operates on duck-typed candidates (anything exposing the
``decision`` / ``rationale`` / ``source_quote`` / ``source_text`` /
``verification`` attributes — :class:`~repowise.core.analysis.decision_extractor.ExtractedDecision`
is the in-tree shape) so it depends on neither the extractor nor the generator,
and both can call it without a cycle.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from repowise.core.analysis.decision_provenance import normalize_text, verify_quote

__all__ = ["GateCandidate", "apply_substring_gate"]

# Fields whose verbatim grounding the gate enforces, in priority order. The
# strongest surviving verdict across them is stamped onto ``verification``.
_GATED_FIELDS = ("decision", "rationale", "source_quote")


class GateCandidate(Protocol):
    """Structural shape the gate reads and mutates."""

    decision: str
    rationale: str
    source_quote: str
    source_text: str
    verification: str


_C = TypeVar("_C", bound=GateCandidate)


def apply_substring_gate(decisions: list[_C]) -> tuple[list[_C], int]:
    """Drop ungrounded fields, reject candidates with no surviving evidence.

    Every produced ``decision`` / ``rationale`` / ``source_quote`` must
    substring- or token-match the verbatim ``source_text`` the producer
    recorded (see :func:`decision_provenance.verify_quote`). Unverifiable
    fields are cleared; a candidate whose every produced field is unverifiable
    is rejected. ``verification`` is stamped with the strongest surviving
    verdict. A candidate with no ``source_text`` (nothing to check against) is
    kept but left ``unverified`` — we never fabricate a rejection we cannot
    justify. ``source_text`` is cleared on every survivor since it is a
    transient and must never reach persistence.

    Returns ``(kept, rejected_count)``.
    """
    kept: list[_C] = []
    rejected = 0
    for d in decisions:
        src = getattr(d, "source_text", "") or ""
        if not src:
            d.verification = "unverified"
            d.source_text = ""
            kept.append(d)
            continue

        # Normalize the (possibly large) source span once per candidate and
        # reuse it across the field checks — verify_quote re-normalizes its
        # second arg, but normalizing an already-collapsed string is a cheap
        # no-op, so the gate stays O(source span) per candidate.
        norm_src = normalize_text(src)

        verdicts: list[str] = []
        produced_any = False
        grounded_any = False
        for fname in _GATED_FIELDS:
            val = (getattr(d, fname, "") or "").strip()
            if not val:
                continue
            produced_any = True
            verdict = verify_quote(val, norm_src)
            if verdict == "unverified":
                setattr(d, fname, "")  # drop the hallucinated field
            else:
                grounded_any = True
                verdicts.append(verdict)

        if produced_any and not grounded_any:
            rejected += 1
            continue

        if "exact" in verdicts:
            d.verification = "exact"
        elif "fuzzy" in verdicts:
            d.verification = "fuzzy"
        else:
            d.verification = "unverified"
        d.source_text = ""  # transient — don't carry into persistence
        kept.append(d)

    return kept, rejected
