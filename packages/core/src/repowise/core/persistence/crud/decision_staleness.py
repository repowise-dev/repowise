"""Staleness scoring for decisions, from the git history of the files they name."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..decision_graph import sync_links_from_record
from ..models import DecisionAcceptance, DecisionRecord, GitMetadata, _now_utc


async def _fill_git_meta_gaps(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
    wanted: set[str],
) -> dict[str, dict]:
    """``git_meta_map`` widened with persisted rows for the *wanted* paths.

    The caller's map is one run's git metadata, and on an incremental update
    that is only the files that changed in it. Staleness scoring reads a path
    with no entry as a file that is gone and scores it 1.00, so every decision
    over an untouched file went maximally stale for not having been touched.
    The persisted rows cover every file the index has seen, which is the set
    that answers "is this file still here"; a path missing from those too is
    genuinely untracked and still scores 1.00. The run's own entries win —
    they are this run's fresher numbers for the files it re-read.
    """
    missing = wanted - git_meta_map.keys()
    if not missing:
        return git_meta_map

    filled: dict[str, dict] = {}
    # Chunked so the IN clause stays under SQLite's bind-parameter ceiling.
    batch = sorted(missing)
    for start in range(0, len(batch), 500):
        rows = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository_id,
                GitMetadata.file_path.in_(batch[start : start + 500]),
            )
        )
        for row in rows.scalars().all():
            filled[row.file_path] = {
                col.name: getattr(row, col.name)
                for col in row.__table__.columns
                if col.name not in ("id", "repository_id")
            }
    filled.update(git_meta_map)
    return filled


async def recompute_decision_staleness(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
    repo_path: Path | str | None = None,
) -> int:
    """Recompute staleness_score for all active decisions. Returns update count.

    With *repo_path*, each record is scored from when its own evidence commits
    landed, when that is after its birth; without it, from its birth alone.
    A later reviewed grant (see :func:`latest_grant_times`) moves the anchor
    to it either way.

    Also fills ``last_code_change`` from the same per-file dates the score is
    counted from, and re-derives ``affected_modules_json`` from the files each
    record names.
    The two belong in one pass because they are one repair: a record's module
    linkage used to be the first path segment, which in a ``packages/`` layout
    made almost every record claim ``packages`` or ``tests``, and the rows that
    predate the fix carry it. Deriving here rather than in a data migration
    keeps the single-repair-path rule — the one this repo already learned when
    an alembic migration and a runtime repair disagreed about confidence.
    """
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active", "proposed"]),
        )
    )
    decisions = list(result.scalars().all())

    affected_by_id: dict[str, list[str]] = {}
    for dec in decisions:
        affected = json.loads(dec.affected_files_json)
        if affected:
            affected_by_id[dec.id] = affected

    remodelled = _backfill_module_nodes(decisions, affected_by_id)
    modules_updated = len(remodelled)
    # This repair runs after ``backfill_decision_node_links`` on both index
    # paths, so a module array it rewrites here would otherwise keep the link
    # the backfill had already judged correct.
    for dec in remodelled:
        await sync_links_from_record(session, dec)
    if not affected_by_id:
        if modules_updated:
            await session.flush()
        return 0

    git_meta_map = await _fill_git_meta_gaps(
        session,
        repository_id,
        git_meta_map,
        {fp for paths in affected_by_id.values() for fp in paths},
    )

    landings = _evidence_landings(repo_path, decisions, affected_by_id)
    grants = await latest_grant_times(session, repository_id)
    now = _now_utc()
    updated = 0
    dated = 0
    for dec in decisions:
        affected = affected_by_id.get(dec.id)
        if not affected:
            continue

        # Before the conventions skip below: a date is a fact about the code,
        # not a score, so it is filled for every scoped record.
        if _refresh_last_code_change(dec, affected, git_meta_map, now):
            dated += 1

        # The source writes its own conformance share, and a git-diff score
        # would overwrite it. Only the score is skipped; the repairs above
        # still apply.
        if dec.source == "conventions":
            continue

        if _rescore_staleness(dec, affected, git_meta_map, now, landings, grants.get(dec.id)):
            updated += 1

    if updated or modules_updated or dated:
        await session.flush()
    # Deliberately the staleness count alone. The callers print this as
    # "N decisions rescored"; folding the module repair or the date fill into
    # it would report a rescore that did not happen.
    return updated


def _refresh_last_code_change(
    dec: DecisionRecord, affected: list[str], git_meta_map: dict[str, dict], now: datetime
) -> bool:
    """Fill ``last_code_change`` from the files' git dates; True when it moved."""
    # Function-local, like every other analysis import here: the persistence
    # layer cannot depend on analysis at module scope.
    from repowise.core.analysis.decisions.extractor import DecisionExtractor, _as_aware_utc

    last_change = DecisionExtractor.last_code_change(affected, git_meta_map)
    stored = dec.last_code_change
    # SQLite drops tzinfo, so an aware value would differ from the naive
    # one it just wrote and rewrite ``updated_at`` on every run.
    if last_change == (_as_aware_utc(stored) if stored else None):
        return False
    dec.last_code_change = last_change
    dec.updated_at = now
    return True


def _evidence_landings(
    repo_path: Path | str | None,
    decisions: list[DecisionRecord],
    affected_by_id: dict[str, list[str]],
) -> dict:
    """Landings of every scoped record's evidence commits, in one git read."""
    if repo_path is None:
        return {}
    from repowise.core.analysis.decisions.landing import evidence_landings

    return evidence_landings(
        repo_path,
        (
            sha
            for dec in decisions
            if dec.id in affected_by_id
            for sha in json.loads(dec.evidence_commits_json or "[]")
        ),
    )


def _rescore_staleness(
    dec: DecisionRecord,
    affected: list[str],
    git_meta_map: dict[str, dict],
    now: datetime,
    landings: dict,
    accepted_at: datetime | None = None,
) -> bool:
    """Recompute the git-diff staleness score; True when it moved by more than 0.01."""
    from repowise.core.analysis.decisions.extractor import DecisionExtractor
    from repowise.core.analysis.decisions.landing import record_landing

    landing = record_landing(
        json.loads(dec.evidence_commits_json or "[]"), landings, dec.created_at
    )
    new_score = DecisionExtractor.compute_staleness(
        dec.created_at,
        affected,
        git_meta_map,
        landed_at=landing.at if landing else None,
        accepted_at=accepted_at,
    )
    if abs(new_score - dec.staleness_score) > 0.01:
        dec.staleness_score = round(new_score, 3)
        dec.updated_at = now
        return True
    return False


def _backfill_module_nodes(
    decisions: list[DecisionRecord],
    affected_by_id: dict[str, list[str]],
) -> list[DecisionRecord]:
    """Re-derive each record's module linkage from its files.

    Returns the records whose array moved, because the caller has to relink
    each one: this rewrites half a record's scope and the graph holds the
    other half.

    Records naming no file are left alone: there is nothing to derive from, and
    an invented scope is worse than an absent one.
    """
    from repowise.core.analysis.decisions.scope import resolve_module_nodes

    moved = []
    for dec in decisions:
        affected = affected_by_id.get(dec.id)
        if not affected:
            continue
        derived = resolve_module_nodes(affected)
        if derived != json.loads(dec.affected_modules_json or "[]"):
            dec.affected_modules_json = json.dumps(derived)
            moved.append(dec)
    return moved


#: Signers whose grant means someone read the code as it stood. An ``import``
#: row is stamped when a manifest, migration or tracked ADR is applied on this
#: machine, which says nothing about when the code was last reviewed.
_REVIEWING_KINDS = ("person", "agent")


async def latest_grant_times(
    session: AsyncSession,
    repository_id: str,
    decision_ids: Iterable[str] | None = None,
) -> dict[str, datetime]:
    """When each decision was last accepted, reaffirmed or merged into, by id.

    Reviewed grants only (see :data:`_REVIEWING_KINDS`). Staleness counts
    from here when it is later than the record's birth and landing.
    """
    from repowise.core.analysis.decisions.lifecycle import GRANTING_ACTIONS

    q = (
        select(DecisionAcceptance.decision_id, func.max(DecisionAcceptance.created_at))
        .where(
            DecisionAcceptance.repository_id == repository_id,
            DecisionAcceptance.action.in_(sorted(GRANTING_ACTIONS)),
            DecisionAcceptance.accepter_kind.in_(_REVIEWING_KINDS),
        )
        .group_by(DecisionAcceptance.decision_id)
    )
    if decision_ids is not None:
        q = q.where(DecisionAcceptance.decision_id.in_(sorted(set(decision_ids))))
    return dict((await session.execute(q)).all())


async def rescore_decision_staleness(session: AsyncSession, record: DecisionRecord) -> None:
    """Rescore one record from persisted git metadata, anchored at its latest grant.

    Called right after a grant so ``needs_review`` clears without waiting for
    the next index. No evidence landing is read, since a grant just written is
    later than any landing, so a record with no reviewed grant is left to
    :func:`recompute_decision_staleness`, which reads landings.
    """
    affected = json.loads(record.affected_files_json or "[]")
    # Conventions write their own conformance share; see the batch path.
    if not affected or record.source == "conventions":
        return
    grants = await latest_grant_times(session, record.repository_id, [record.id])
    if record.id not in grants:
        return
    from repowise.core.analysis.decisions.staleness import compute_staleness

    git_meta = await _fill_git_meta_gaps(session, record.repository_id, {}, set(affected))
    score = compute_staleness(record.created_at, affected, git_meta, accepted_at=grants[record.id])
    # A later anchor only lowers the score. A rise means a file this store has
    # no metadata for yet, such as one added since the last index, which the
    # next index scores with the full picture.
    if score < (record.staleness_score or 0.0):
        record.staleness_score = score
        record.updated_at = _now_utc()


def is_stale(currency: str | None) -> bool:
    """The one meaning of a stale decision: accepted, at effective ``needs_review``.

    *currency* is from ``decision_currencies``, where a candidate is ``None``.
    A candidate's staleness score is a fact about its files, not a decision
    going stale, so it never counts.
    """
    return currency == "needs_review"


async def get_stale_decisions(
    session: AsyncSession,
    repository_id: str,
    records: Iterable[DecisionRecord] | None = None,
) -> list[DecisionRecord]:
    """The decisions in *records* (default: the repository's) that :func:`is_stale`."""
    from .authority import decision_currencies

    if records is None:
        records = await session.scalars(
            select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
        )
    records = list(records)
    currencies = await decision_currencies(session, repository_id, records)
    return [r for r in records if is_stale(currencies.get(r.id))]
