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
    "ACCEPTER_KINDS",
    "ACCEPTER_SESSION_MAX",
    "AGENT_ACCEPTANCE_REMEDY",
    "AGREEMENT_KIND",
    "AGREEMENT_SCOPE",
    "ARCHITECTURAL_KIND",
    "CANDIDATE_REVIEW_STATES",
    "DECISION_CURRENCIES",
    "DECISION_KINDS",
    "DECISION_STATUS_ORDER",
    "GRANTING_ACTIONS",
    "NEEDS_REVIEW_STALENESS",
    "REVIEW_LANES",
    "SPLIT_MARKERS",
    "STORED_CURRENCIES",
    "UNRECORDED_ACCEPTER_KIND",
    "AcceptanceRequirement",
    "acceptance_blockers",
    "accepter_kind_blocker",
    "bundles_decisions",
    "currency_for_legacy_status",
    "effective_currency",
    "is_governing",
    "legacy_status_for_currency",
    "machine_grant_blocker",
    "status_rank",
]

#: The two nouns the store holds. An *architectural* decision is a claim about
#: the code, and a diff can violate it. An *agreement* is a claim about how the
#: work is conducted — the branch to commit on, what a pull request body may
#: say, which command never runs — and no diff can violate it, because it is
#: not about the code at all. They were one noun until now, which cost both:
#: an agreement could not be accepted, and it diluted the records that a diff
#: can be checked against.
ARCHITECTURAL_KIND = "architectural"
AGREEMENT_KIND = "agreement"
DECISION_KINDS: tuple[str, ...] = (ARCHITECTURAL_KIND, AGREEMENT_KIND)

#: The scope an agreement governs. An agreement names no file, but "no scope"
#: and "the whole repository" are different claims, and only the second is
#: true of it. Stating it keeps the acceptance contract's rule intact — nothing
#: is accepted without a scope — instead of carving an exemption out of it,
#: and it is what the ``scope_json`` CHECK on an acceptance row stores.
AGREEMENT_SCOPE = "<repository>"

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

#: Punctuation that joins independent choices into one claim. A candidate
#: carrying any of them is flagged for review and never split by machine: a
#: wrong split files one decision under the other's evidence, which is a
#: harder mistake to see than a bundle nobody separated.
#:
#: Deliberately narrow, and measured that way over 357 records. The three
#: below flag 29; adding a bare ``" and "`` flags 194 of 357, which is 44% of
#: the store held out to catch one more bundle. The guard is partial on
#: purpose: the cost of a marker it misses is a merge a reviewer has to undo,
#: and the cost of one it invents is a decision that never merges at all.
SPLIT_MARKERS: tuple[str, ...] = ("; ", " and also ", " and, ")


def bundles_decisions(text: str) -> bool:
    """Whether *text* joins what look like two independent choices.

    A pure function of the claim, so both capture lanes can ask it, and both
    must: the flag is what stops evidence-keyed identity from folding a
    bundled claim together with the separate decisions it bundles.
    """
    low = text.lower()
    return any(marker in low for marker in SPLIT_MARKERS)


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

#: Actions that create or renew authority, against the three that withdraw it.
GRANTING_ACTIONS: frozenset[str] = frozenset({"accepted", "reaffirmed", "merged"})

#: Who signed an acceptance. ``person`` is a human, ``agent`` a coding agent
#: or a pipeline stage, ``import`` a tracked artifact or manifest speaking for
#: whoever committed it. ``accepter`` alone cannot answer this: it is a free
#: string resolved from the repository's git identity, so a machine signing
#: reads as a person.
#:
#: Stored ``""`` is a row written before this column, never backfilled to
#: ``person``: "unrecorded" and "a human signed" are what this keeps apart.
ACCEPTER_KINDS: tuple[str, ...] = ("person", "agent", "import")
UNRECORDED_ACCEPTER_KIND = ""

#: Width of ``decision_acceptances.accepter_session``, so a caller can refuse
#: a longer id rather than hand Postgres a truncation error.
ACCEPTER_SESSION_MAX = 64

#: What fixes a :func:`machine_grant_blocker` refusal. Addressed to a person,
#: because the party reading it is the agent that was just refused.
AGENT_ACCEPTANCE_REMEDY = (
    "Someone who owns this repository can allow it with "
    "`repowise decision config agent-acceptance --on`, or accept it themselves."
)


def accepter_kind_blocker(kind: str) -> str | None:
    """Why *kind* cannot be stamped on a new acceptance, or ``None``."""
    if kind in ACCEPTER_KINDS:
        return None
    if kind == UNRECORDED_ACCEPTER_KIND:
        return "no accepter kind: say whether a person, an agent or an import signed this"
    return f"unknown accepter kind {kind!r}: one of {', '.join(ACCEPTER_KINDS)}"


def machine_grant_blocker(kind: str, action: str, *, granted: bool) -> str | None:
    """Why an agent may not take *action*, or ``None``.

    A machine revokes but does not grant: withdrawing narrows what a record
    claims and re-accepting undoes it, while granting mints a constraint
    nobody agreed to. Stated rather than left true by omission.
    """
    if kind != "agent" or action not in GRANTING_ACTIONS or granted:
        return None
    return (
        f"an agent may not record a {action!r} acceptance: machines withdraw "
        "authority but do not grant it"
    )


#: The fraction of a decision's files that must have moved before the decision
#: is worth re-reading. Same 0.5 the staleness surfaces already use; the number
#: stays an internal supporting fact and the product word is what is shown.
NEEDS_REVIEW_STALENESS: float = 0.5

#: Currencies that still bind future work. ``needs_review`` deliberately does:
#: a decision whose code moved is a decision to re-read, not one to ignore.
_GOVERNING: frozenset[str] = frozenset({"active", "needs_review"})

#: Statuses that record a retirement somebody performed. The migration keeps
#: them: reclassifying one as an open candidate would undo the retirement.
RETIRED_STATUSES: frozenset[str] = frozenset({"dismissed", "deprecated", "superseded"})

#: Retirements that leave nothing in the decision's place, and so govern no
#: path. A record at one of these keeps its files and its place in a lookup by
#: id and links no code, because the graph is what "what governs this path" is
#: answered from.
#:
#: ``superseded`` is deliberately *not* here even though it is retired: it has
#: a successor, it still governs the path, and ``get_risk``'s
#: ``superseded_decision`` directive finds it through those very links to tell
#: a reviewer their change is governed by a decision that has been replaced.
#: ``proposed`` is absent because a candidate is not retired at all, and the
#: candidate lane delivers on its links.
WITHDRAWN_STATUSES: frozenset[str] = frozenset({"dismissed", "deprecated"})


def is_retired(status: str) -> bool:
    """Whether *status* records a retirement somebody performed."""
    return status in RETIRED_STATUSES


def is_withdrawn(status: str) -> bool:
    """Whether *status* leaves nothing in the decision's place, so governs no path."""
    return status in WITHDRAWN_STATUSES


def is_governing(currency: str) -> bool:
    """Whether a decision at *currency* still constrains new work."""
    return currency in _GOVERNING


def effective_currency(
    stored: str,
    *,
    has_scope: bool,
    staleness: float,
    repo_wide: bool = False,
) -> str:
    """The currency to show for a decision stored at *stored*.

    ``superseded``, ``dismissed`` and an explicit ``needs_review`` are authority
    states a person set, and the code cannot argue with them. An ``active``
    decision is re-read against the repository: one that names nothing cannot be
    checked at all, and one whose files have moved is worth looking at again.

    Neither question can be put to an agreement. *repo_wide* says so: it names
    no file, which makes ``uncheckable`` a complaint about it being what it is,
    and it has no files that can move, which makes ``needs_review`` a reading of
    a staleness score measured over nothing.
    """
    if stored != "active":
        return stored if stored in DECISION_CURRENCIES else "active"
    if repo_wide:
        return "active"
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
