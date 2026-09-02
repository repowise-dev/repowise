"""The three-entity vocabulary: episode, candidate, decision.

An *episode* is an evidenced event. A *candidate* is a possible durable choice
inferred from evidence. A *decision* is a constraint a person accepted. Only the
third governs, and nothing but an explicit acceptance produces one.

Pure: no I/O, no ORM, no provider. The persistence layer, the CLI, the manifest
writer and the migration all resolve the same words here so a review action
cannot mean one thing in the database and another on the wire.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "ACCEPTANCE_ACTIONS",
    "CANDIDATE_REVIEW_STATES",
    "DECISION_CURRENCIES",
    "DECISION_STATUS_ORDER",
    "NEEDS_REVIEW_STALENESS",
    "REVIEW_LANES",
    "STORED_CURRENCIES",
    "AcceptanceRequirement",
    "acceptance_blockers",
    "currency_for_legacy_status",
    "effective_currency",
    "is_governing",
    "legacy_status_for_currency",
    "status_rank",
]

#: What a decision's authority currently amounts to. Replaces the numeric
#: staleness threshold as the *product* answer; the score stays an internal
#: supporting fact that feeds ``needs_review``.
DECISION_CURRENCIES: tuple[str, ...] = (
    "active",  # accepted and still describes the code
    "needs_review",  # accepted, but the code it names has moved
    "uncheckable",  # accepted, but names nothing the repository can be asked about
    "superseded",  # replaced by a later decision, via an explicit lineage edge
    "dismissed",  # authority withdrawn; kept for history
)

#: Where a candidate is in review. ``dismissed`` is a durable tombstone: it is
#: what stops re-extraction from re-proposing something already rejected.
CANDIDATE_REVIEW_STATES: tuple[str, ...] = (
    "open",
    "accepted",
    "merged",
    "needs_split",
    "dismissed",
)

#: The currencies a person or artifact can *set*. ``needs_review`` and
#: ``uncheckable`` are also derived from the code by :func:`effective_currency`,
#: which is why the stored set is smaller than the vocabulary.
STORED_CURRENCIES: tuple[str, ...] = (
    "active",
    "needs_review",
    "superseded",
    "dismissed",
)

#: The one ranking of ``decision_records.status``, best first. A rule the team
#: stands behind, then a candidate, then history, then a tombstone. Four
#: independent copies of this ordering existed and two of them disagreed about
#: where ``superseded`` sat, so a list and the tool describing it ranked the
#: same rows differently.
DECISION_STATUS_ORDER: tuple[str, ...] = (
    "active",
    "proposed",
    "superseded",
    "deprecated",
    "dismissed",
)

_STATUS_RANK: dict[str, int] = {s: i for i, s in enumerate(DECISION_STATUS_ORDER)}


def status_rank(status: str) -> int:
    """Sort key for *status*. An unknown status sorts after every known one."""
    return _STATUS_RANK.get(status, len(DECISION_STATUS_ORDER))


#: The review lanes, which partition a repository: every record is in exactly
#: one, and the five sum to the total. ``candidates`` is the absence of an
#: acceptance; the other four are the currencies, with ``superseded`` and
#: ``dismissed`` folded into ``history`` because a reader working a queue does
#: not need those apart. ``governing`` is deliberately not here: it is the
#: roll-up of ``active`` and ``needs_review``, so it overlaps two lanes and
#: belongs in a filter rather than in a tab row.
REVIEW_LANES: tuple[str, ...] = (
    "active",
    "candidates",
    "needs_review",
    "uncheckable",
    "history",
)

#: What an acceptance row records having happened.
ACCEPTANCE_ACTIONS: tuple[str, ...] = (
    "accepted",
    "reaffirmed",
    "merged",
    "superseded",
    "dismissed",
    "returned_to_review",
)

#: The fraction of a decision's files that must have moved before the decision
#: is worth re-reading. Same 0.5 the staleness surfaces already use; the number
#: stays an internal supporting fact and the product word is what is shown.
NEEDS_REVIEW_STALENESS: float = 0.5

#: Currencies that still bind future work. ``needs_review`` deliberately does:
#: a decision whose code moved is a decision to re-read, not one to ignore.
_GOVERNING: frozenset[str] = frozenset({"active", "needs_review"})


def is_governing(currency: str) -> bool:
    """Whether a decision at *currency* still constrains new work."""
    return currency in _GOVERNING


def effective_currency(
    stored: str,
    *,
    has_scope: bool,
    staleness: float,
) -> str:
    """The currency to show for a decision stored at *stored*.

    ``superseded``, ``dismissed`` and an explicit ``needs_review`` are authority
    states a person set, and the code cannot argue with them. An ``active``
    decision is re-read against the repository: one that names nothing cannot be
    checked at all, and one whose files have moved is worth looking at again.
    """
    if stored != "active":
        return stored if stored in DECISION_CURRENCIES else "active"
    if not has_scope:
        return "uncheckable"
    if staleness >= NEEDS_REVIEW_STALENESS:
        return "needs_review"
    return "active"


@dataclass(frozen=True, slots=True)
class AcceptanceRequirement:
    """The evidence an acceptance has to carry to be storable."""

    reason: str
    scope: Sequence[str]
    evidence: Sequence[str]
    accepter: str = ""
    artifact: str = ""
    #: The accepter wrote the record rather than reviewing something a machine
    #: inferred. Then the entry itself is the provenance, and demanding a
    #: separate reference would make manual capture impossible.
    self_authored: bool = False


#: Named because the review row's ``scope_unresolved`` flag is this exact
#: verdict, and re-deriving it would be a second opinion about scope.
NO_SCOPE_BLOCKER = "no scope: name the files or modules it governs"


def acceptance_blockers(req: AcceptanceRequirement) -> list[str]:
    """Return the reasons *req* cannot become an acceptance, empty if it can.

    The same four checks the ``decision_acceptances`` CHECK constraints
    enforce, stated once in prose so a caller can explain a refusal instead of
    surfacing an IntegrityError.
    """
    blockers: list[str] = []
    if not req.reason.strip():
        blockers.append("no rationale or explicit constraint reason")
    if not [s for s in req.scope if s and s.strip()]:
        blockers.append(NO_SCOPE_BLOCKER)
    if not req.self_authored and not [e for e in req.evidence if e and e.strip()]:
        blockers.append("no evidence reference")
    if not req.accepter.strip() and not req.artifact.strip():
        blockers.append("no accepter or tracked-artifact identity")
    return blockers


#: How the pre-split ``decision_records.status`` column maps onto currency, and
#: back. The column survives as a denormalized projection so unmigrated readers
#: keep working; the acceptance row is the authority.
_LEGACY_TO_CURRENCY: dict[str, str] = {
    "active": "active",
    "deprecated": "dismissed",
    "superseded": "superseded",
    "dismissed": "dismissed",
}

_CURRENCY_TO_LEGACY: dict[str, str] = {
    "active": "active",
    "needs_review": "active",
    "uncheckable": "active",
    "superseded": "superseded",
    "dismissed": "deprecated",
}


def currency_for_legacy_status(status: str) -> str | None:
    """Currency for a legacy status, or ``None`` if it is not a decision at all."""
    return _LEGACY_TO_CURRENCY.get(status)


def legacy_status_for_currency(currency: str) -> str:
    """The ``decision_records.status`` value that projects *currency*."""
    return _CURRENCY_TO_LEGACY.get(currency, "active")
