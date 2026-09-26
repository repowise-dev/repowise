"""Decision evidence rows and the headline score derived from them."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.provenance import completeness, compute_confidence

from ..models import DecisionEvidence, DecisionRecord


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


def _json_list(raw: str | None) -> list[str]:
    """A JSON array column as a list; anything unparsable reads as empty."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def _best_verification(values: list[str]) -> str:
    """Reduce per-evidence verdicts to the strongest: exact > fuzzy > unverified."""
    if "exact" in values:
        return "exact"
    if "fuzzy" in values:
        return "fuzzy"
    return "unverified"


def record_completeness(rec: DecisionRecord) -> int:
    """:func:`completeness` for a stored row, parsing its two JSON columns."""
    return completeness(
        decision=rec.decision,
        rationale=rec.rationale,
        context=rec.context,
        consequences=_json_list(rec.consequences_json),
        alternatives=_json_list(rec.alternatives_json),
    )


def _rederive_headline(rec: DecisionRecord, evidence: list[DecisionEvidence]) -> None:
    """Set a record's confidence + verification from its full evidence set.

    The single definition of how a headline is scored. Every writer uses it:
    the upsert path after accreting a run's evidence, ``reconcile_source_ranks``
    after a ladder edit, and ``reconcile_decision_confidence`` after a formula
    edit. Kept as one function because two of them were briefly copy-pasted and
    nothing would have forced the copies to stay equal.

    Confidence rises with the best source rank and with the number of
    *independent* corroborating sources, and with how much of its body the
    record fills, so it is derived from the whole set plus the row rather than
    from whichever evidence row happened to arrive last. No-op on empty
    evidence: a record with nothing behind it keeps whatever it had.
    """
    if not evidence:
        return
    best_ver = _best_verification([e.verification for e in evidence])
    rec.confidence = compute_confidence(
        max(e.source_rank for e in evidence),
        len({e.source for e in evidence}),
        best_ver,
        filled_fields=record_completeness(rec),
    )
    rec.verification = best_ver

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
