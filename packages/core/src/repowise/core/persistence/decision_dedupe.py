"""Folding duplicate candidates that are already stored.

Cross-run dedup stops new duplicates at the pass that creates the record; it
cannot reach the ones written before it existed. This is the retroactive half:
it produces the store the write path would have produced, by folding each
duplicate into the record it duplicates and moving its evidence there.

Stars, not components
---------------------
A record is folded only into a record it is itself measured at or above *tau*
against. Similarity is not transitive: A~B and B~C at the threshold leaves A~C
free to be anything, and a chain of near-neighbours can span two genuinely
different decisions bridged by records that bundle both. So a connected
component is covered by one or more *stars*: take its most authoritative member
as canonical, fold the members holding a direct edge to it, and repeat on
whatever is left. Every fold performed is a measured pair, never an inferred
one.

What is eligible
----------------
Both ends must be candidates nobody has touched. Folding into an accepted
decision changes what governs, and :func:`merge_candidate` already owns that
with the review trail it deserves; a retired record carries lineage a fold
would erase; and a record a reviewer has flagged, split or dismissed holds a
judgement that its deletion would throw away. This sweep grants no authority
and overrules no person.

What a fold keeps, and what it drops
------------------------------------
Governed files and modules, evidence commits and tags are unioned onto the
canonical, so no file loses the decision that governs it. Of the duplicate's
evidence, every row citing a source, file and commit the canonical does not
already hold moves across; a row matching one the canonical has corroborates
nothing new and goes with its record, matching the convergence
``_upsert_decision_evidence`` performs on the same key. So the set of distinct
evidence keys is conserved and the row count falls.

Three things are dropped rather than merged, each because merging them is
worse than losing them. ``consequences`` and ``alternatives`` are prose kept
in a list, and unioning them across a cluster yields mostly restatements of
each other; the canonical is chosen partly on completeness, so the
best-stated version survives. The duplicate's review row goes with it, taking
the lane and grounding that recorded how *that* record was extracted — facts
about a row that no longer exists. Nothing here is reconstructible, so a
caller that needs it should read the plan before applying.

One thing this cannot promise: the unioned scope is as durable as any other
record's, which is to say a later re-extraction at equal or higher source rank
replaces it, the same way it replaces the canonical's own.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import ARCHITECTURAL_KIND
from repowise.core.analysis.decisions.provenance import rank_for_source
from repowise.core.analysis.decisions.scope import (
    SCOPE_BASIS_FOOTPRINT,
    SCOPE_BASIS_SELECTED,
    SCOPE_BASIS_STATED,
    binds_to_paths,
    commit_scope_basis,
)
from repowise.core.analysis.decisions.semantic_match import (
    DECISION_VECTOR_PREFIX,
    DEFAULT_DEDUP_TAU,
    SEARCH_FETCH,
    decision_match_text,
    find_related_decisions_many,
)
from repowise.core.providers.embedding import store_has_semantic_vectors

# ``_add_alias`` is private but its chain repointing is the load-bearing part:
# without it a prior alias of a folded record points at an id this sweep deletes.
from .crud.authority import _add_alias, accepted_decision_ids
from .crud.decisions import (
    _json_list,
    _rederive_headline,
    list_decision_evidence,
    record_completeness,
)
from .decision_graph import sync_decision_node_links
from .models import (
    DecisionCandidateMeta,
    DecisionEdge,
    DecisionEvidence,
    DecisionRecord,
)

__all__ = [
    "DedupePlan",
    "FoldPlan",
    "apply_dedupe",
    "plan_dedupe",
    "render_plan",
]

#: Set-valued provenance the canonical absorbs from what it folds. See the
#: module docstring for what is deliberately left out.
_UNION_FIELDS: tuple[tuple[str, str], ...] = (
    ("affected_files_json", "files"),
    ("affected_modules_json", "modules"),
    ("evidence_commits_json", "commits"),
    ("tags_json", "tags"),
)

#: Statuses a duplicate may be folded from. A record with no acceptance is a
#: candidate whatever this column says, so ``active`` belongs here; the retired
#: statuses do not, because their lineage would go with them.
_FOLDABLE_STATUSES: frozenset[str] = frozenset({"proposed", "active"})


@dataclass
class FoldPlan:
    """One star: the record that survives, and what folds into it."""

    canonical_id: str
    canonical_title: str
    #: ``(decision_id, title, cosine against the canonical)``, closest first.
    folded: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.folded) + 1

    def as_dict(self) -> dict:
        return {
            "canonical_id": self.canonical_id,
            "canonical_title": self.canonical_title,
            "folded": [
                {"id": i, "title": t, "similarity": round(s, 4)} for i, t, s in self.folded
            ],
        }


@dataclass
class DedupePlan:
    """What the sweep will fold, and the evidence it rests on."""

    tau: float = DEFAULT_DEDUP_TAU
    #: Foldable candidates carrying match text.
    eligible: int = 0
    #: Pairs at or above *tau* stored as separate records.
    pairs: int = 0
    clusters: list[FoldPlan] = field(default_factory=list)
    #: True when the store could not be asked, so the zeros above mean nothing.
    degraded: bool = False

    @property
    def folded_count(self) -> int:
        return sum(len(c.folded) for c in self.clusters)

    def counts(self) -> dict[str, int]:
        return {
            "eligible": self.eligible,
            "pairs": self.pairs,
            "clusters": len(self.clusters),
            "folded": self.folded_count,
        }

    def as_dict(self) -> dict:
        return {
            "tau": self.tau,
            "degraded": self.degraded,
            **self.counts(),
            "clusters": [c.as_dict() for c in self.clusters],
        }


def _canonical_key(rec: DecisionRecord) -> tuple:
    """Rank the members of a cluster; the maximum survives.

    Source rank first, because that is what the write path promotes a headline
    on. Completeness breaks the rank ties, so a blank record cannot outrank one
    that says what was decided. Confidence reads completeness too, but it
    blends it with rank and verification, so it cannot stand in for the term
    above it. The last two only make the pick deterministic.
    """
    return (
        rank_for_source(rec.source),
        record_completeness(rec),
        rec.confidence or 0.0,
        rec.created_at.isoformat() if rec.created_at else "",
        rec.id,
    )


async def _foldable(session: AsyncSession, repository_id: str) -> list[DecisionRecord]:
    """The records this sweep is allowed to fold. See the module docstring."""
    result = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    )
    accepted = await accepted_decision_ids(session, repository_id)
    reviewed = {
        decision_id
        for decision_id, in (
            await session.execute(
                select(DecisionCandidateMeta.decision_id).where(
                    DecisionCandidateMeta.repository_id == repository_id,
                    DecisionCandidateMeta.review_state != "open",
                )
            )
        ).all()
    }
    return [
        rec
        for rec in result.scalars().all()
        if rec.status in _FOLDABLE_STATUSES
        and rec.id not in accepted
        and rec.id not in reviewed
        and decision_match_text(rec.title, rec.decision or "")
    ]


async def plan_dedupe(
    session: AsyncSession,
    repository_id: str,
    *,
    vector_store,
    tau: float = DEFAULT_DEDUP_TAU,
) -> DedupePlan:
    """Work out what folds into what. Writes nothing.

    Sets ``degraded`` on a store that cannot answer semantically, so a keyless
    run reads as "could not look" rather than "found nothing".
    """
    plan = DedupePlan(tau=tau)
    if not store_has_semantic_vectors(vector_store):
        plan.degraded = True
        return plan

    records = await _foldable(session, repository_id)
    plan.eligible = len(records)
    if len(records) < 2:
        return plan

    by_id = {rec.id: rec for rec in records}
    # The neighbour window is the store's own, so a record with more near-twins
    # than it holds keeps the closest; a second sweep picks up any remainder.
    neighbours = await find_related_decisions_many(
        vector_store,
        [(rec.title, rec.decision or "", {rec.id}) for rec in records],
        lo=tau,
        limit=SEARCH_FETCH,
    )

    # Symmetrise: one record's window can be crowded while the other's is not,
    # and a pair measured from either side is the same pair.
    edges: dict[str, dict[str, float]] = defaultdict(dict)
    for rec, related in zip(records, neighbours, strict=True):
        for other_id, score in related:
            if other_id not in by_id or other_id == rec.id:
                continue
            best = max(score, edges[rec.id].get(other_id, 0.0))
            edges[rec.id][other_id] = best
            edges[other_id][rec.id] = best
    plan.pairs = sum(len(v) for v in edges.values()) // 2
    plan.clusters = _star_cover(records, edges)
    return plan


def _star_cover(
    records: list[DecisionRecord], edges: dict[str, dict[str, float]]
) -> list[FoldPlan]:
    """Cover each component with stars around its most authoritative members."""
    by_id = {rec.id: rec for rec in records}
    unassigned = {rec.id for rec in records if edges.get(rec.id)}
    clusters: list[FoldPlan] = []
    while unassigned:
        canonical = max(unassigned, key=lambda i: _canonical_key(by_id[i]))
        unassigned.discard(canonical)
        folded = sorted(
            (i for i in edges[canonical] if i in unassigned),
            key=lambda i: -edges[canonical][i],
        )
        unassigned -= set(folded)
        if folded:
            clusters.append(
                FoldPlan(
                    canonical_id=canonical,
                    canonical_title=by_id[canonical].title,
                    folded=[(i, by_id[i].title, edges[canonical][i]) for i in folded],
                )
            )
    # Largest first, so the report's head is the corpus's worst duplication.
    clusters.sort(key=lambda c: (-c.size, c.canonical_id))
    return clusters


async def apply_dedupe(
    session: AsyncSession,
    repository_id: str,
    *,
    vector_store,
    plan: DedupePlan | None = None,
    tau: float = DEFAULT_DEDUP_TAU,
) -> DedupePlan:
    """Fold the plan, and return what was actually folded.

    Idempotent: a second run finds nothing left to fold. The caller owns the
    transaction, and the vectors go *before* it commits. The other order leaves
    a vector whose record is gone, which the matcher drops with no log line
    while silently re-creating the duplicate this removed; this order can only
    leave a record whose vector is gone, which the next ``reindex`` restores.

    A supplied *plan* is re-checked against the store rather than trusted: it
    may have been built before someone accepted one of its members.
    """
    if plan is None:
        plan = await plan_dedupe(session, repository_id, vector_store=vector_store, tau=tau)
    if not plan.clusters:
        return plan

    eligible = {rec.id for rec in await _foldable(session, repository_id)}
    applied = DedupePlan(
        tau=plan.tau, eligible=plan.eligible, pairs=plan.pairs, degraded=plan.degraded
    )
    retired: list[str] = []
    for cluster in plan.clusters:
        if cluster.canonical_id not in eligible:
            continue
        canonical = await session.get(DecisionRecord, cluster.canonical_id)
        if canonical is None:
            continue
        union = {
            name: set(_json_list(getattr(canonical, column))) for column, name in _UNION_FIELDS
        }
        done = FoldPlan(canonical_id=canonical.id, canonical_title=canonical.title)
        # Whether every list going into the union was chosen file by file,
        # and whether any of them was a list no surface may bind to.
        all_selected = canonical.scope_basis == SCOPE_BASIS_SELECTED
        any_unbound = not binds_to_paths(canonical.scope_basis)
        for folded_id, title, score in cluster.folded:
            folded = await session.get(DecisionRecord, folded_id)
            if folded is None or folded_id not in eligible:
                continue
            all_selected = all_selected and folded.scope_basis == SCOPE_BASIS_SELECTED
            any_unbound = any_unbound or not binds_to_paths(folded.scope_basis)
            for column, name in _UNION_FIELDS:
                union[name] |= set(_json_list(getattr(folded, column)))
            # The checkable noun wins a fold. An agreement that absorbs a
            # record about the code is about the code too, and being checked
            # against files it does name is the recoverable error of the two.
            if folded.kind == ARCHITECTURAL_KIND:
                canonical.kind = ARCHITECTURAL_KIND
            await _absorb_evidence(session, canonical.id, folded.id)
            await _repoint_references(session, canonical.id, folded.id)
            await _add_alias(session, folded.id, canonical.id, reason="merged")
            await session.delete(folded)
            await session.flush()
            retired.append(folded_id)
            done.folded.append((folded_id, title, score))
        if not done.folded:
            continue

        for column, name in _UNION_FIELDS:
            setattr(canonical, column, json.dumps(sorted(union[name])))
        # The union widens the file list, so the basis is recomputed from
        # it rather than inherited: a footprint folding into a narrow
        # canonical would otherwise hand it the wide list under a binding
        # basis, on a record whose source the backfill does not repair.
        #
        # A fold of records that each chose their own files is the exception.
        # Duplicates are restatements of one decision, so the union of their
        # selections is still a selection, and recomputing it by breadth would
        # demote a scope no breadth rule ever produced. That holds only while
        # every member chose.
        #
        # Otherwise, a member that could not bind on its own must not bind
        # through the union. The breadth rule cannot express that any more:
        # ``backfill_scope_basis`` now marks a legacy commit list a footprint
        # at any width, so ``commit_scope_basis`` over a small union returns
        # the binding empty basis and would hand the canonical files nobody
        # ever chose. Breadth is left to decide only the case it still owns --
        # two lists that were both binding to begin with.
        if canonical.scope_basis == SCOPE_BASIS_STATED:
            pass
        elif all_selected:
            canonical.scope_basis = SCOPE_BASIS_SELECTED
        elif any_unbound:
            canonical.scope_basis = SCOPE_BASIS_FOOTPRINT
        else:
            canonical.scope_basis = commit_scope_basis(sorted(union["files"]))
        # Sync replaces rather than accretes, so it must see the union.
        binds = binds_to_paths(canonical.scope_basis)
        await sync_decision_node_links(
            session,
            repository_id,
            canonical.id,
            files=sorted(union["files"]) if binds else [],
            modules=sorted(union["modules"]) if binds else [],
        )
        _rederive_headline(canonical, await list_decision_evidence(session, canonical.id))
        applied.clusters.append(done)

    await session.flush()
    if retired and vector_store is not None:
        await vector_store.delete_many(
            [f"{DECISION_VECTOR_PREFIX}{decision_id}" for decision_id in sorted(retired)]
        )
    return applied


async def _absorb_evidence(session: AsyncSession, canonical_id: str, folded_id: str) -> None:
    """Move the folded record's evidence across, keeping what is already there.

    A row whose natural key the canonical already holds cites the same source,
    file and commit, so it corroborates nothing the canonical does not have and
    is left to go with its record; re-upserting it instead would overwrite the
    canonical's quote, line and verdict with the duplicate's. ``held`` compares
    NULLs equal, which is what ``_upsert_decision_evidence`` does explicitly and
    not what the unique constraint does, so a fold converges on that key the
    same way a re-index of the same source does.
    """
    held = {
        (source, file, commit)
        for source, file, commit in (
            await session.execute(
                select(
                    DecisionEvidence.source,
                    DecisionEvidence.evidence_file,
                    DecisionEvidence.evidence_commit,
                ).where(DecisionEvidence.decision_id == canonical_id)
            )
        ).all()
    }
    rows = (
        (
            await session.execute(
                select(DecisionEvidence).where(DecisionEvidence.decision_id == folded_id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        key = (row.source, row.evidence_file, row.evidence_commit)
        if key in held:
            continue
        row.decision_id = canonical_id
        held.add(key)
    await session.flush()


async def _repoint_references(session: AsyncSession, canonical_id: str, folded_id: str) -> None:
    """Send every pointer at the folded record to the canonical instead.

    ``superseded_by`` and the edge endpoints carry no foreign key, so nothing
    would catch them dangling.
    """
    await session.execute(
        update(DecisionRecord)
        .where(DecisionRecord.superseded_by == folded_id)
        .values(superseded_by=canonical_id)
    )
    # An edge between the two ends would become a self-edge; drop it first, so
    # the rewrite below only ever sees edges pointing outside the pair.
    await session.execute(
        delete(DecisionEdge).where(
            DecisionEdge.src_decision_id.in_([canonical_id, folded_id]),
            DecisionEdge.dst_decision_id.in_([canonical_id, folded_id]),
        )
    )
    for column in (DecisionEdge.src_decision_id, DecisionEdge.dst_decision_id):
        edges = (
            (await session.execute(select(DecisionEdge).where(column == folded_id)))
            .scalars()
            .all()
        )
        for edge in edges:
            src = canonical_id if edge.src_decision_id == folded_id else edge.src_decision_id
            dst = canonical_id if edge.dst_decision_id == folded_id else edge.dst_decision_id
            clash = (
                await session.execute(
                    select(DecisionEdge.id).where(
                        DecisionEdge.src_decision_id == src,
                        DecisionEdge.dst_decision_id == dst,
                        DecisionEdge.kind == edge.kind,
                    )
                )
            ).first()
            if clash is not None:
                await session.delete(edge)
            else:
                edge.src_decision_id, edge.dst_decision_id = src, dst
    await session.flush()


def render_plan(plan: DedupePlan, *, limit: int = 10) -> str:
    """Report the plan for a person. Same shape dry run and applied."""
    if plan.degraded:
        return (
            "\n  Decision dedupe (dry run)\n\n"
            "  This store has no semantic vectors to compare decisions with, so\n"
            "  nothing could be examined. Set an embedder key and run\n"
            "  'repowise reindex' first.\n"
        )
    counts = plan.counts()
    remain = counts["eligible"] - counts["folded"]
    lines = [
        "",
        "  Decision dedupe (dry run)",
        "",
        f"  Candidates scanned        {counts['eligible']}",
        f"  Duplicate pairs >= {plan.tau:.2f}   {counts['pairs']}",
        f"  Merge clusters            {counts['clusters']}",
        f"  Records folded            {counts['folded']}  ->  {remain} candidates remain",
    ]
    if not plan.clusters:
        return "\n".join([*lines, "", "  Nothing to fold."]) + "\n"
    lines += ["", f"  Largest clusters (showing {min(limit, len(plan.clusters))}):"]
    for cluster in plan.clusters[:limit]:
        lines.append(f"    {cluster.canonical_id[:8]}  {cluster.canonical_title}")
        for folded_id, title, score in cluster.folded:
            lines.append(f"      <- {folded_id[:8]}  {score:.3f}  {title}")
    return "\n".join(lines) + "\n"
