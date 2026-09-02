"""Classifying legacy decision rows as candidates or decisions.

A runtime repair rather than an Alembic step, following the rule this repository
already learned: a data fix that lives in a migration and a data fix that lives
in the code eventually disagree, and only one of them runs on an existing store.

The classification is deliberately conservative. Before the entity split a row
could reach ``active`` by being seen in two sessions, with no person involved.
Those rows govern today, they are in the agent's standing-decisions block, and
almost none of them can show an acceptance event. Demoting them is the point:
authority the user did not grant should never have been authority. What the
migration owes them is an exact account of what it did and why, which is what
:func:`plan_migration` produces before anything is written.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import currency_for_legacy_status

from .crud.authority import (
    AcceptanceRefusedError,
    accepted_decision_ids,
    candidate_review_signals,
    record_acceptance,
    upsert_candidate_meta,
)
from .models import DecisionCandidateMeta, DecisionRecord

__all__ = [
    "MigrationPlan",
    "RowPlan",
    "apply_migration",
    "plan_json",
    "plan_migration",
    "render_plan",
]

#: Sources whose rows a person authored directly. A ``cli`` row exists because
#: somebody typed it, and an ``adr`` row because somebody committed the document
#: it was parsed from, so both carry their own acceptance evidence. Nothing else
#: does: every other source is an inference over text written for another
#: purpose.
_SELF_ACCEPTING_SOURCES: frozenset[str] = frozenset({"cli", "adr"})

#: Statuses that record a retirement somebody performed. The migration keeps
#: them: reclassifying one as an open candidate would undo the retirement.
_RETIRED_STATUSES: frozenset[str] = frozenset({"dismissed", "deprecated", "superseded"})


@dataclass(slots=True)
class RowPlan:
    """What the migration will do with one legacy row, and why."""

    decision_id: str
    title: str
    status: str
    source: str
    outcome: str  # decision | candidate | tombstone | already_migrated
    reason: str
    review_state: str = "open"
    duplicate_of: str | None = None


@dataclass(slots=True)
class MigrationPlan:
    """The full account of a store's legacy rows."""

    rows: list[RowPlan] = field(default_factory=list)
    duplicate_clusters: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return dict(Counter(r.outcome for r in self.rows))

    def by_outcome(self, outcome: str) -> list[RowPlan]:
        return [r for r in self.rows if r.outcome == outcome]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": len(self.rows),
            "counts": self.counts(),
            "duplicate_clusters": self.duplicate_clusters,
            "rows": [
                {
                    "id": r.decision_id,
                    "title": r.title,
                    "status": r.status,
                    "source": r.source,
                    "outcome": r.outcome,
                    "reason": r.reason,
                    "review_state": r.review_state,
                    "duplicate_of": r.duplicate_of,
                }
                for r in self.rows
            ],
        }


def _normalize_title(title: str) -> str:
    """The staging store's normalization, so clusters agree with promotion.

    Exact and re-extraction duplicates only. Two rows whose titles differ by
    punctuation are the same extraction seen twice; two rows that merely mean
    something similar are not, and merging those is a judgement no migration
    gets to make.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", title.lower())).strip()


def _has_scope(rec: DecisionRecord) -> bool:
    # The acceptance contract's own answer, so the gap this plan reports and
    # the flag the review row carries cannot disagree about one record.
    _, scope_unresolved = candidate_review_signals(rec)
    return not scope_unresolved


async def plan_migration(
    session: AsyncSession, repository_id: str
) -> MigrationPlan:
    """Classify every legacy row without writing anything.

    Four outcomes, and every row gets exactly one:

    - ``already_migrated``: an acceptance row exists, so nothing to decide.
    - ``tombstone``: dismissed, deprecated or superseded. Keeps that status.
    - ``decision``: a person or a tracked document authored it *and* it carries
      the reason, scope and evidence acceptance requires.
    - ``candidate``: everything else, including every automatically promoted
      ``active`` row.
    """
    plan = MigrationPlan()
    records = (
        (
            await session.execute(
                select(DecisionRecord)
                .where(DecisionRecord.repository_id == repository_id)
                .order_by(DecisionRecord.created_at)
            )
        )
        .scalars()
        .all()
    )
    already = await accepted_decision_ids(session, repository_id)
    reviewed = set(
        (
            await session.execute(
                select(DecisionCandidateMeta.decision_id).where(
                    DecisionCandidateMeta.repository_id == repository_id,
                    DecisionCandidateMeta.review_state != "open",
                )
            )
        )
        .scalars()
        .all()
    )

    by_title: dict[str, list[DecisionRecord]] = defaultdict(list)
    for rec in records:
        by_title[_normalize_title(rec.title)].append(rec)
    canonical = {
        norm: group[0].id for norm, group in by_title.items() if len(group) > 1
    }
    plan.duplicate_clusters = {
        canonical[norm]: [r.id for r in group[1:]]
        for norm, group in by_title.items()
        if len(group) > 1
    }

    for rec in records:
        norm = _normalize_title(rec.title)
        dup_of = canonical.get(norm)
        dup_of = dup_of if dup_of and dup_of != rec.id else None

        if rec.id in already:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "already_migrated",
                    "an acceptance event is already recorded",
                    duplicate_of=dup_of,
                )
            )
            continue

        # A retirement is a review action somebody already performed. Reopening
        # it as an unreviewed candidate would put a decision the user retired
        # back in front of them asking to be accepted, so all three retired
        # statuses keep their status and carry a tombstone.
        if rec.status in _RETIRED_STATUSES:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "tombstone",
                    f"{rec.status} before the split; the retirement is preserved",
                    review_state="dismissed",
                    duplicate_of=dup_of,
                )
            )
            continue

        if rec.id in reviewed:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "already_migrated",
                    "already reviewed; its review state is left alone",
                    duplicate_of=dup_of,
                )
            )
            continue

        currency = currency_for_legacy_status(rec.status)
        if currency is None:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "candidate",
                    "never accepted: it was awaiting review",
                    duplicate_of=dup_of,
                )
            )
            continue

        if rec.source not in _SELF_ACCEPTING_SOURCES:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "candidate",
                    f"{rec.status} by recurrence, not by a person: "
                    f"a {rec.source} row carries no acceptance event",
                    duplicate_of=dup_of,
                )
            )
            continue

        gaps: list[str] = []
        if not (rec.rationale.strip() or rec.decision.strip()):
            gaps.append("no rationale")
        if not _has_scope(rec):
            gaps.append("no scope")
        if gaps:
            plan.rows.append(
                RowPlan(
                    rec.id,
                    rec.title,
                    rec.status,
                    rec.source,
                    "candidate",
                    f"authored via {rec.source} but {' and '.join(gaps)}",
                    duplicate_of=dup_of,
                )
            )
            continue

        plan.rows.append(
            RowPlan(
                rec.id,
                rec.title,
                rec.status,
                rec.source,
                "decision",
                f"authored via {rec.source}, with a reason and a scope",
                duplicate_of=dup_of,
            )
        )
    return plan


async def apply_migration(
    session: AsyncSession, repository_id: str, *, plan: MigrationPlan | None = None
) -> MigrationPlan:
    """Write the plan. Idempotent: a second run reclassifies nothing.

    Idempotence falls out of the classification rather than being bolted on: a
    row the first run accepted lands in ``already_migrated`` on the second,
    and a candidate row already carrying its review state is rewritten to the
    same values.
    """
    plan = plan or await plan_migration(session, repository_id)
    records = {
        rec.id: rec
        for rec in (
            (
                await session.execute(
                    select(DecisionRecord).where(
                        DecisionRecord.repository_id == repository_id
                    )
                )
            )
            .scalars()
            .all()
        )
    }

    for row in plan.rows:
        rec = records.get(row.decision_id)
        if rec is None or row.outcome == "already_migrated":
            continue
        if rec.status in _RETIRED_STATUSES and row.outcome != "decision":
            # Record the tombstone without touching the status that carries the
            # retirement.
            existed = await session.get(DecisionCandidateMeta, rec.id) is not None
            await upsert_candidate_meta(session, rec, lane=rec.source)
            meta = await session.get(DecisionCandidateMeta, rec.id)
            if meta is not None and not existed:
                meta.review_state = "dismissed"
            continue

        if row.outcome == "decision":
            try:
                await record_acceptance(
                    session,
                    rec,
                    action="accepted",
                    currency="active",
                    accepter=f"migration:{rec.source}",
                    note="reconstructed from a self-authored legacy record",
                )
            except AcceptanceRefusedError as exc:
                # The gap checks above and the CHECK constraints agree on all
                # four requirements, so this is a row whose evidence went
                # missing rather than one the plan misjudged. Demote it and say
                # so, instead of failing the whole migration over one record.
                row.outcome = "candidate"
                row.reason = f"acceptance refused: {exc}"
            else:
                continue

        existed = await session.get(DecisionCandidateMeta, rec.id) is not None
        priority, scope_unresolved = candidate_review_signals(rec)
        await upsert_candidate_meta(
            session,
            rec,
            lane=rec.source,
            review_priority=priority,
            scope_unresolved=scope_unresolved,
        )
        # Only a row this run created gets its review state set. Rerunning must
        # not walk a split or a dismissal back to unreviewed.
        meta = await session.get(DecisionCandidateMeta, rec.id)
        if meta is not None and not existed:
            meta.review_state = row.review_state
        # The legacy column is a projection: a candidate is not ``active``, and
        # leaving it so would keep every unmigrated reader treating it as one.
        # A tombstone keeps the status that records its retirement.
        if row.outcome == "candidate" and rec.status != "proposed":
            rec.status = "proposed"

    await session.flush()
    return plan


def render_plan(plan: MigrationPlan, *, limit: int = 10) -> str:
    """A human-readable dry-run report."""
    counts = plan.counts()
    lines = [
        "Decision migration (dry run)",
        "",
        f"  Total legacy records         {len(plan.rows):>5}",
        f"  Kept as decisions            {counts.get('decision', 0):>5}",
        f"  Reclassified as candidates   {counts.get('candidate', 0):>5}",
        f"  Dismissed tombstones         {counts.get('tombstone', 0):>5}",
        f"  Already migrated             {counts.get('already_migrated', 0):>5}",
    ]
    if plan.duplicate_clusters:
        clustered = sum(len(v) for v in plan.duplicate_clusters.values())
        lines += [
            "",
            f"  Duplicate clusters        {len(plan.duplicate_clusters)} "
            f"covering {clustered} repeat rows (flagged, not merged)",
        ]
    reasons = Counter(r.reason for r in plan.rows if r.outcome == "candidate")
    if reasons:
        lines += ["", "  Why records became candidates:"]
        lines += [f"    {n:>4}  {reason}" for reason, n in reasons.most_common(limit)]
    return "\n".join(lines)


def plan_json(plan: MigrationPlan) -> str:
    return json.dumps(plan.as_dict(), indent=2)
