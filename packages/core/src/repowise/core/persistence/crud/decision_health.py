"""The decision health summary: lane counts, stale records, ungoverned hotspots."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import status_rank
from repowise.core.analysis.decisions.scope import binds_to_paths

from ..models import DecisionRecord, GitMetadata


async def get_decision_health_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict:
    """Return decision health: counts by lane, stale decisions, ungoverned hotspots.

    The five list fields are returned ranked worst-first: stale by staleness,
    proposed by confidence, ungoverned hotspots by temporal hotspot score,
    retired by lane (history before tombstone) and unscoped by confidence. A
    caller that shows only the first few shows the few that matter.
    Callers may truncate; they must not re-order.

    ``retired_decisions`` holds ``(lane, record)`` pairs: the lane is derived
    from the acceptance, which the ``status`` column may disagree with.

    Counts the acceptance, not the status column. The key names are the ones
    every caller already renders, and they keep their product meaning:
    ``active`` is what governs, ``proposed`` is what nobody has accepted. What
    changed is that a record reaches ``active`` here by having an acceptance,
    the same way it does on every other governance read. Counting the column
    made this the one surface still reporting a hundred governing decisions on
    a store whose acceptances were empty, and it fed ``ungoverned_hotspots``,
    so unaccepted records were also suppressing the files nothing governs.
    """
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
        )
    )
    all_decisions = list(result.scalars().all())
    from .authority import decision_currencies

    currencies = await decision_currencies(session, repository_id, all_decisions)

    lanes = _Lanes()
    for d in all_decisions:
        lanes.add(d, currencies.get(d.id))
    ungoverned = await _ungoverned_hotspots(session, repository_id, lanes.governed_files)
    lanes.rank()
    conflicts = await _conflict_summaries(session, repository_id, all_decisions)
    lanes.counts["conflicts"] = len(conflicts)

    return {
        "summary": lanes.counts,
        "stale_decisions": lanes.stale,
        "proposed_awaiting_review": lanes.proposed,
        "ungoverned_hotspots": ungoverned,
        "conflicts": conflicts,
        "retired_decisions": lanes.retired,
        "unscoped_decisions": lanes.unscoped,
    }


@dataclass
class _Lanes:
    """Every record sorted into the lane its acceptance puts it in."""

    counts: dict[str, int] = field(
        default_factory=lambda: {
            "active": 0,
            "proposed": 0,
            "deprecated": 0,
            "superseded": 0,
            "dismissed": 0,
            "stale": 0,
            # Accepted records naming no file. They score 0.0 because the
            # staleness question cannot be asked of them, which renders
            # identically to a record whose code genuinely has not moved, so
            # they are counted separately rather than banked as fresh.
            "unscoped": 0,
        }
    )
    stale: list[DecisionRecord] = field(default_factory=list)
    proposed: list[DecisionRecord] = field(default_factory=list)
    # Counted-only lanes. The record is in hand at the point that drops it, so
    # naming it costs no query. ``retired`` carries its lane because that lane
    # is derived from the acceptance where there is one, and a record's
    # ``status`` column may disagree with it.
    retired: list[tuple[str, DecisionRecord]] = field(default_factory=list)
    unscoped: list[DecisionRecord] = field(default_factory=list)
    # Files an *accepted* decision names. A candidate naming a hotspot does not
    # make it governed, and counting one did: it removed the file from
    # ``ungoverned_hotspots``, which is the list whose whole job is to say
    # where nobody has decided anything.
    governed_files: set[str] = field(default_factory=set)

    def add(self, d: DecisionRecord, currency: str | None) -> None:
        if currency is None:
            # No acceptance, so it does not govern. A retired one is not
            # awaiting review either: it keeps the status that retired it and
            # stays out of the queue, which is the one thing a tombstone must
            # never be counted as.
            if d.status in ("dismissed", "deprecated", "superseded"):
                self.counts[d.status] = self.counts.get(d.status, 0) + 1
                self.retired.append((d.status, d))
            else:
                self.counts["proposed"] += 1
                self.proposed.append(d)
            return
        if currency in ("superseded", "dismissed"):
            self.counts[currency] += 1
            self.retired.append((currency, d))
            return
        self.counts["active"] += 1
        if currency == "needs_review":
            self.counts["stale"] += 1
            self.stale.append(d)
        if currency == "uncheckable":
            self.counts["unscoped"] += 1
            self.unscoped.append(d)
        # ``governed_files`` is the denominator for "ungoverned hotspots",
        # so a footprint would suppress every file its commit touched.
        if binds_to_paths(d.scope_basis):
            self.governed_files.update(json.loads(d.affected_files_json))

    def rank(self) -> None:
        """Order every list worst-first.

        Ranked here rather than at the five call sites, none of which does. The
        MCP health dashboard and ``repowise decision health`` cut all three
        lists; the overview attention panel cuts the hotspots and renders the
        other two in the order it is handed; the decisions route serves them
        whole in that order; and ``health/governance.py`` walks them to *write*
        one finding row per entry. So the callers that truncate were showing
        whichever rows the scan returned first, and the ones that do not were
        still listing them by it, while the score answering "which of these
        first" rides on every record, unread. The ``or 0.0`` guards match how
        every other reader of these two fields spells it rather than trusting a
        column default to have been back-filled; the id tiebreak makes the key
        total, so two runs agree.
        """
        self.stale.sort(key=lambda d: (-(d.staleness_score or 0.0), d.id))
        self.proposed.sort(key=lambda d: (-(d.confidence or 0.0), d.id))
        # Retired by lane, history before tombstone. Not by ``updated_at``: it
        # moves on any write, so it does not say when a record was retired.
        # ``unscoped`` by confidence, the key ``proposed`` already uses.
        self.retired.sort(key=lambda pair: (status_rank(pair[0]), pair[1].id))
        self.unscoped.sort(key=lambda d: (-(d.confidence or 0.0), d.id))


async def _ungoverned_hotspots(
    session: AsyncSession, repository_id: str, governed_files: set[str]
) -> list[str]:
    """Hotspot files no accepted decision names, hottest first.

    Sorting these by path put the file most in need of a decision behind
    whatever sorts alphabetically first, with the score that answers the
    question sitting unread one column over. The key is the one
    ``routers/overview.py`` already applies to these same rows in SQL (score
    descending with NULLs last, then churn) rather than a second answer to
    "which hotspot matters most". A NULL score is genuinely unknown and is not
    the same as a measured zero.
    """
    hotspot_result = await session.execute(
        select(
            GitMetadata.file_path,
            GitMetadata.temporal_hotspot_score,
            GitMetadata.churn_percentile,
        ).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.is_hotspot == True,  # noqa: E712
        )
    )
    hotspot_rows = {row[0]: (row[1], row[2]) for row in hotspot_result.all()}

    def _hotspot_rank(file_path: str) -> tuple[bool, float, float, str]:
        score, churn = hotspot_rows[file_path]
        return (score is None, -(score or 0.0), -(churn or 0.0), file_path)

    return sorted(hotspot_rows.keys() - governed_files, key=_hotspot_rank)


async def _conflict_summaries(
    session: AsyncSession, repository_id: str, all_decisions: list[DecisionRecord]
) -> list[dict]:
    """Phase 3B: contradictory active decisions (``conflicts_with`` edges)."""
    from ..decision_graph import list_conflict_edges

    by_id = {d.id: d for d in all_decisions}
    conflicts: list[dict] = []
    for edge in await list_conflict_edges(session, repository_id):
        src = by_id.get(edge.src_decision_id)
        dst = by_id.get(edge.dst_decision_id)
        if src is None or dst is None:
            continue
        conflicts.append(
            {
                "src": {"id": src.id, "title": src.title, "status": src.status},
                "dst": {"id": dst.id, "title": dst.title, "status": dst.status},
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            }
        )
    return conflicts
