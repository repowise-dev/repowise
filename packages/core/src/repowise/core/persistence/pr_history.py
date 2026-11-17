"""Read PR provenance already captured by this repository's decision store."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from .crud.decision_evidence import _json_list
from .crud.repository import get_repository_by_path
from .database import create_engine, create_session_factory, has_db_store, resolve_db_url
from .models import DecisionEvidence, DecisionRecord


async def load_mined_pr_shas(repo_path: Path) -> set[str]:
    """Include retired/dismissed provenance too, without creating a missing store.

    A read error propagates: silently treating an unreadable store as empty
    would spend model calls re-mining bodies and recreate the original defect.
    """
    if not has_db_store(repo_path):
        return set()
    engine = create_engine(resolve_db_url(repo_path))
    try:
        async with create_session_factory(engine)() as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return set()
            evidence = await session.scalars(
                select(DecisionEvidence.evidence_commit)
                .join(DecisionRecord, DecisionRecord.id == DecisionEvidence.decision_id)
                .where(DecisionRecord.repository_id == repo.id, DecisionEvidence.source == "pr")
            )
            shas = {sha for sha in evidence if sha}
            # Legacy rows can predate accreted evidence. Do not page or filter
            # by status, or old/dismissed PRs would be sent again.
            legacy = await session.scalars(
                select(DecisionRecord.evidence_commits_json).where(
                    DecisionRecord.repository_id == repo.id, DecisionRecord.source == "pr"
                )
            )
            for raw in legacy:
                shas.update(sha for sha in _json_list(raw) if sha)
            return shas
    finally:
        await engine.dispose()
