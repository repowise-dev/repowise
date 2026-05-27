"""CRUD operations for the decisions domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decision_provenance import compute_confidence, rank_for_source

from ..decision_graph import sync_decision_node_links
from ..models import (
    DecisionEvidence,
    DecisionRecord,
    GitMetadata,
    _new_uuid,
    _now_utc,
)

# ---------------------------------------------------------------------------
# DecisionRecord CRUD
# ---------------------------------------------------------------------------

_VALID_DECISION_STATUSES = frozenset({"proposed", "active", "deprecated", "superseded"})


async def upsert_decision(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str,
    status: str = "proposed",
    context: str = "",
    decision: str = "",
    rationale: str = "",
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "cli",
    evidence_commits: list[str] | None = None,
    evidence_file: str | None = None,
    evidence_line: int | None = None,
    confidence: float = 1.0,
    verification: str = "unverified",
    last_code_change: datetime | None = None,
    staleness_score: float = 0.0,
    superseded_by: str | None = None,
    decision_id: str | None = None,
) -> DecisionRecord:
    """Create or update a decision record.

    Dedup key: ``(repository_id, title, source, evidence_file)``.
    """
    # Normalise text fields — LLM extractors may return explicit None
    rationale = rationale or ""
    context = context or ""
    decision = decision or ""

    # Build the WHERE clause — evidence_file may be NULL
    q = select(DecisionRecord).where(
        DecisionRecord.repository_id == repository_id,
        DecisionRecord.title == title,
        DecisionRecord.source == source,
    )
    if evidence_file is not None:
        q = q.where(DecisionRecord.evidence_file == evidence_file)
    else:
        q = q.where(DecisionRecord.evidence_file.is_(None))

    result = await session.execute(q)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.status = status
        existing.context = context
        existing.decision = decision
        existing.rationale = rationale
        existing.alternatives_json = json.dumps(alternatives or [])
        existing.consequences_json = json.dumps(consequences or [])
        existing.affected_files_json = json.dumps(affected_files or [])
        existing.affected_modules_json = json.dumps(affected_modules or [])
        existing.tags_json = json.dumps(tags or [])
        existing.evidence_commits_json = json.dumps(evidence_commits or [])
        existing.evidence_line = evidence_line
        existing.confidence = confidence
        existing.verification = verification
        existing.last_code_change = last_code_change
        existing.staleness_score = staleness_score
        existing.superseded_by = superseded_by
        existing.updated_at = _now_utc()
        await session.flush()
        return existing

    rec = DecisionRecord(
        id=decision_id or _new_uuid(),
        repository_id=repository_id,
        title=title,
        status=status,
        context=context,
        decision=decision,
        rationale=rationale,
        alternatives_json=json.dumps(alternatives or []),
        consequences_json=json.dumps(consequences or []),
        affected_files_json=json.dumps(affected_files or []),
        affected_modules_json=json.dumps(affected_modules or []),
        tags_json=json.dumps(tags or []),
        evidence_commits_json=json.dumps(evidence_commits or []),
        source=source,
        evidence_file=evidence_file,
        evidence_line=evidence_line,
        confidence=confidence,
        verification=verification,
        last_code_change=last_code_change,
        staleness_score=staleness_score,
        superseded_by=superseded_by,
    )
    session.add(rec)
    await session.flush()
    return rec


async def get_decision(session: AsyncSession, decision_id: str) -> DecisionRecord | None:
    """Return a DecisionRecord by primary key, or None."""
    return await session.get(DecisionRecord, decision_id)


async def list_decisions(
    session: AsyncSession,
    repository_id: str,
    *,
    status: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    module: str | None = None,
    include_proposed: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[DecisionRecord]:
    """Return decision records with optional filters."""
    q = select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    if status is not None:
        q = q.where(DecisionRecord.status == status)
    elif not include_proposed:
        q = q.where(DecisionRecord.status != "proposed")
    if source is not None:
        q = q.where(DecisionRecord.source == source)
    if tag is not None:
        # Match exact tag value in JSON array, not substring.
        # JSON arrays store as '["tag1", "tag2"]', so we match '"tag"'
        q = q.where(DecisionRecord.tags_json.contains(f'"{tag}"'))
    if module is not None:
        # Match exact module path in JSON array
        q = q.where(DecisionRecord.affected_modules_json.contains(f'"{module}"'))
    q = q.order_by(DecisionRecord.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_decision_metadata(
    session: AsyncSession,
    decision_id: str,
    *,
    affected_modules: list[str] | None = None,
    affected_files: list[str] | None = None,
) -> DecisionRecord | None:
    """Patch the module/file linkage on a decision record.

    Each argument left as ``None`` is preserved. Pass an empty list to clear.
    Returns the updated record, or ``None`` if the id was not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    if affected_modules is not None:
        rec.affected_modules_json = json.dumps(affected_modules)
    if affected_files is not None:
        rec.affected_files_json = json.dumps(affected_files)
    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def update_decision_status(
    session: AsyncSession,
    decision_id: str,
    status: str,
    *,
    superseded_by: str | None = None,
) -> DecisionRecord | None:
    """Update the status of a decision record.

    Raises ValueError for invalid statuses. Returns None if not found.
    """
    if status not in _VALID_DECISION_STATUSES:
        raise ValueError(
            f"Unknown decision status {status!r}. Valid values: {sorted(_VALID_DECISION_STATUSES)}"
        )
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    rec.status = status
    if superseded_by is not None:
        rec.superseded_by = superseded_by
    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def update_decision_by_id(
    session: AsyncSession,
    decision_id: str,
    **fields: Any,
) -> DecisionRecord | None:
    """Update content fields of a decision record by ID (partial update).

    Accepts keyword arguments for any updatable field:
    title, context, decision, rationale, alternatives, consequences,
    affected_files, affected_modules, tags, evidence_file, evidence_line,
    confidence.

    JSON list fields (alternatives, consequences, affected_files,
    affected_modules, tags) accept Python lists and are serialized to JSON.

    Returns None if the decision is not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None

    _json_fields = {
        "alternatives": "alternatives_json",
        "consequences": "consequences_json",
        "affected_files": "affected_files_json",
        "affected_modules": "affected_modules_json",
        "tags": "tags_json",
    }
    _scalar_fields = {
        "title",
        "context",
        "decision",
        "rationale",
        "evidence_file",
        "evidence_line",
        "confidence",
    }

    for key, value in fields.items():
        if key in _json_fields:
            setattr(rec, _json_fields[key], json.dumps(value))
        elif key in _scalar_fields:
            setattr(rec, key, value)

    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def delete_decision(session: AsyncSession, decision_id: str) -> bool:
    """Delete a decision record. Returns True if deleted, False if not found."""
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return False
    await session.delete(rec)
    await session.flush()
    return True


def _normalize_title(title: str) -> str:
    """Normalize a decision title for cross-source dedup comparison."""
    import re as _re

    t = title.lower().strip()
    t = _re.sub(r"[^a-z0-9\s]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t


def _evidence_quote(d: dict) -> str:
    """Pick the verbatim span recorded as this evidence row's source quote.

    Prefers the LLM/parser-supplied ``source_quote``; falls back to the
    decision/rationale text so an evidence row is never empty.
    """
    return (d.get("source_quote") or d.get("decision") or d.get("rationale") or "").strip()


async def _upsert_decision_evidence(
    session: AsyncSession,
    decision_id: str,
    *,
    source: str,
    source_rank: int,
    evidence_file: str | None,
    evidence_line: int | None,
    evidence_commit: str | None,
    source_quote: str,
    confidence: float,
    verification: str,
) -> None:
    """Insert or update one evidence row, idempotent on its natural key.

    Natural key is ``(decision_id, source, evidence_file, evidence_commit)``.
    NULLs are matched explicitly (SQLite treats NULLs as distinct in a unique
    constraint), so re-indexing the same source converges instead of
    duplicating, while an incremental update adds genuinely new evidence.
    """
    q = select(DecisionEvidence).where(
        DecisionEvidence.decision_id == decision_id,
        DecisionEvidence.source == source,
    )
    q = (
        q.where(DecisionEvidence.evidence_file == evidence_file)
        if evidence_file is not None
        else q.where(DecisionEvidence.evidence_file.is_(None))
    )
    q = (
        q.where(DecisionEvidence.evidence_commit == evidence_commit)
        if evidence_commit is not None
        else q.where(DecisionEvidence.evidence_commit.is_(None))
    )
    existing = (await session.execute(q)).scalar_one_or_none()

    if existing is not None:
        existing.source_rank = source_rank
        existing.evidence_line = evidence_line
        existing.source_quote = source_quote
        existing.confidence = confidence
        existing.verification = verification
        return

    session.add(
        DecisionEvidence(
            decision_id=decision_id,
            source=source,
            source_rank=source_rank,
            evidence_file=evidence_file,
            evidence_line=evidence_line,
            evidence_commit=evidence_commit,
            source_quote=source_quote,
            confidence=confidence,
            verification=verification,
        )
    )


def _best_verification(values: list[str]) -> str:
    """Reduce per-evidence verdicts to the strongest: exact > fuzzy > unverified."""
    if "exact" in values:
        return "exact"
    if "fuzzy" in values:
        return "fuzzy"
    return "unverified"


async def list_decision_evidence(
    session: AsyncSession,
    decision_id: str,
) -> list[DecisionEvidence]:
    """Return all evidence rows for a decision, highest source rank first."""
    result = await session.execute(
        select(DecisionEvidence)
        .where(DecisionEvidence.decision_id == decision_id)
        .order_by(DecisionEvidence.source_rank.desc(), DecisionEvidence.created_at.asc())
    )
    return list(result.scalars().all())


def _first_commit(d: dict) -> str | None:
    commits = d.get("evidence_commits") or []
    return commits[0] if commits else None


async def bulk_upsert_decisions(
    session: AsyncSession,
    repository_id: str,
    decisions: list[dict],
    *,
    vector_store: Any | None = None,
) -> list[str]:
    """Upsert decisions, accreting provenance instead of discarding losers.

    Decisions with near-identical normalized titles are merged into a single
    :class:`DecisionRecord`; every contributing source becomes a
    :class:`DecisionEvidence` row. The record's headline fields come from the
    highest-``source_rank`` contributor, and its confidence is recomputed from
    the best rank + the number of independently corroborating sources + the
    strongest surviving verification verdict.

    Idempotent: a full re-index converges (evidence rows upsert on their
    natural key); an incremental update adds new evidence and re-derives the
    headline + confidence from the union.

    When *vector_store* (the shared page-generator store) is supplied, a
    Phase-2C semantic pass augments the cheap normalized-title match: an
    incoming group that matched no existing record by title is looked up in the
    store, and if its nearest ``decision:`` neighbour clears the cosine
    threshold it is folded into that record as additional evidence — so
    paraphrases ("Use Redis" vs "Adopt Redis cache") collapse into one record.
    Every touched record is (re-)embedded into the store, so decisions are
    matchable next run *and* discoverable via ``search_codebase``.

    Returns the ids of every record touched (created or updated) this call, so
    a caller can run the Phase-3 supersession/conflict detection over just the
    records that changed.
    """
    # Group incoming decisions by normalized title.
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        norm = _normalize_title(d.get("title", ""))
        if not norm:
            continue
        groups.setdefault(norm, []).append(d)

    if not groups:
        return []

    # Map existing records (this repo) by normalized title so cross-run merges
    # land on the same row. On a title collision keep the most authoritative
    # existing row as canonical.
    existing_rows = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    )
    existing_by_norm: dict[str, DecisionRecord] = {}
    for rec in existing_rows.scalars().all():
        norm = _normalize_title(rec.title)
        prior = existing_by_norm.get(norm)
        if prior is None or rank_for_source(rec.source) > rank_for_source(prior.source):
            existing_by_norm[norm] = rec

    # Phase 2C semantic dedup runs against the shared vector store. ``id_to_rec``
    # lets a store hit (which returns a decision id) resolve back to the live
    # record, and is grown as records are created so paraphrases *within* one
    # batch also collapse.
    id_to_rec: dict[str, DecisionRecord] = {rec.id: rec for rec in existing_by_norm.values()}
    if vector_store is not None:
        from repowise.core.analysis.decision_semantic_match import (
            find_duplicate_decision,
            upsert_decision_vector,
        )

    touched_ids: list[str] = []

    for norm, members in groups.items():
        # Headline candidate: highest source rank, tie-break by confidence.
        headline = max(
            members,
            key=lambda d: (rank_for_source(d.get("source", "")), d.get("confidence", 0.0)),
        )
        rec = existing_by_norm.get(norm)

        # No title match → ask the store whether a semantically-equivalent
        # decision already exists, and fold into it if so (cheap title dedup
        # stays the first pass; this only runs on the residual).
        if rec is None and vector_store is not None:
            match_id = await find_duplicate_decision(
                vector_store,
                title=headline.get("title", ""),
                decision=headline.get("decision") or "",
            )
            if match_id is not None and match_id in id_to_rec:
                rec = id_to_rec[match_id]
                existing_by_norm[norm] = rec

        if rec is None:
            rec = DecisionRecord(
                id=_new_uuid(),
                repository_id=repository_id,
                title=headline.get("title", ""),
                status=headline.get("status", "proposed"),
                context=headline.get("context") or "",
                decision=headline.get("decision") or "",
                rationale=headline.get("rationale") or "",
                alternatives_json=json.dumps(headline.get("alternatives") or []),
                consequences_json=json.dumps(headline.get("consequences") or []),
                affected_files_json=json.dumps(headline.get("affected_files") or []),
                affected_modules_json=json.dumps(headline.get("affected_modules") or []),
                tags_json=json.dumps(headline.get("tags") or []),
                evidence_commits_json=json.dumps(headline.get("evidence_commits") or []),
                source=headline.get("source", "cli"),
                evidence_file=headline.get("evidence_file"),
                evidence_line=headline.get("evidence_line"),
                confidence=headline.get("confidence", 0.5),
            )
            session.add(rec)
            await session.flush()
            existing_by_norm[norm] = rec
            id_to_rec[rec.id] = rec
        elif rank_for_source(headline.get("source", "")) >= rank_for_source(rec.source):
            # A new contributor at least as authoritative as the current
            # headline → promote its fields (provenance still accretes below).
            rec.title = headline.get("title", rec.title)
            rec.status = headline.get("status", rec.status)
            rec.context = headline.get("context") or rec.context
            rec.decision = headline.get("decision") or rec.decision
            rec.rationale = headline.get("rationale") or rec.rationale
            rec.alternatives_json = json.dumps(headline.get("alternatives") or [])
            rec.consequences_json = json.dumps(headline.get("consequences") or [])
            rec.affected_files_json = json.dumps(headline.get("affected_files") or [])
            rec.affected_modules_json = json.dumps(headline.get("affected_modules") or [])
            rec.tags_json = json.dumps(headline.get("tags") or [])
            rec.evidence_commits_json = json.dumps(headline.get("evidence_commits") or [])
            rec.source = headline.get("source", rec.source)
            rec.evidence_file = headline.get("evidence_file")
            rec.evidence_line = headline.get("evidence_line")

        # Accrete one evidence row per contributing source occurrence.
        for d in members:
            src = d.get("source", "cli")
            await _upsert_decision_evidence(
                session,
                rec.id,
                source=src,
                source_rank=rank_for_source(src),
                evidence_file=d.get("evidence_file"),
                evidence_line=d.get("evidence_line"),
                evidence_commit=_first_commit(d),
                source_quote=_evidence_quote(d),
                confidence=d.get("confidence", 0.5),
                verification=d.get("verification", "unverified"),
            )

        # Re-derive headline confidence + verification from the FULL evidence
        # set (existing + just-added), so corroboration accrues across runs.
        evidence = await list_decision_evidence(session, rec.id)
        if evidence:
            distinct_sources = {e.source for e in evidence}
            top_rank = max(e.source_rank for e in evidence)
            best_ver = _best_verification([e.verification for e in evidence])
            rec.confidence = compute_confidence(top_rank, len(distinct_sources), best_ver)
            rec.verification = best_ver
        rec.updated_at = _now_utc()
        touched_ids.append(rec.id)

        # Mirror the JSON file/module arrays into first-class decision→code
        # links so the graph is traversable both directions (Phase 3A). The
        # JSON stays the cheap read cache; these rows are the queryable truth.
        await sync_decision_node_links(
            session,
            repository_id,
            rec.id,
            files=json.loads(rec.affected_files_json or "[]"),
            modules=json.loads(rec.affected_modules_json or "[]"),
        )

        # (Re-)embed the record into the shared store so it's matchable by
        # later groups in this batch + future runs, and discoverable via
        # search_codebase. Best-effort — never blocks the SQL upsert.
        if vector_store is not None:
            await upsert_decision_vector(
                vector_store,
                rec.id,
                title=rec.title,
                decision=rec.decision or "",
                evidence_file=rec.evidence_file,
            )

    await session.flush()
    return touched_ids


async def recompute_decision_staleness(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
) -> int:
    """Recompute staleness_score for all active decisions. Returns update count."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active", "proposed"]),
        )
    )
    decisions = list(result.scalars().all())

    now = _now_utc()
    updated = 0
    for dec in decisions:
        affected = json.loads(dec.affected_files_json)
        if not affected:
            continue

        from repowise.core.analysis.decision_extractor import DecisionExtractor

        decision_text = f"{dec.title} {dec.decision} {dec.rationale}"
        new_score = DecisionExtractor.compute_staleness(
            dec.created_at,
            affected,
            git_meta_map,
            decision_text=decision_text,
        )
        if abs(new_score - dec.staleness_score) > 0.01:
            dec.staleness_score = round(new_score, 3)
            dec.updated_at = now
            updated += 1

    if updated:
        await session.flush()
    return updated


async def get_stale_decisions(
    session: AsyncSession,
    repository_id: str,
    threshold: float = 0.5,
) -> list[DecisionRecord]:
    """Return active decisions with staleness_score >= threshold."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active"]),
            DecisionRecord.staleness_score >= threshold,
        )
    )
    return list(result.scalars().all())


async def get_decision_health_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict:
    """Return decision health: counts by status, stale decisions, ungoverned hotspots."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
        )
    )
    all_decisions = list(result.scalars().all())

    counts = {"active": 0, "proposed": 0, "deprecated": 0, "superseded": 0, "stale": 0}
    stale_decisions: list[DecisionRecord] = []
    proposed_decisions: list[DecisionRecord] = []

    # Collect all governed files from active decisions
    governed_files: set[str] = set()
    for d in all_decisions:
        counts[d.status] = counts.get(d.status, 0) + 1
        if d.status == "active":
            if d.staleness_score >= 0.5:
                counts["stale"] += 1
                stale_decisions.append(d)
            for fp in json.loads(d.affected_files_json):
                governed_files.add(fp)
        elif d.status == "proposed":
            proposed_decisions.append(d)

    # Find ungoverned hotspots
    hotspot_result = await session.execute(
        select(GitMetadata.file_path).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.is_hotspot == True,  # noqa: E712
        )
    )
    hotspot_files = {row[0] for row in hotspot_result.all()}
    ungoverned = sorted(hotspot_files - governed_files)

    # Phase 3B: surface contradictory active decisions (conflicts_with edges).
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
    counts["conflicts"] = len(conflicts)

    return {
        "summary": counts,
        "stale_decisions": stale_decisions,
        "proposed_awaiting_review": proposed_decisions,
        "ungoverned_hotspots": ungoverned,
        "conflicts": conflicts,
    }
