"""Database reads the modes share: corpus, git metadata, evidence rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.crud.authority import accepted_decision_ids
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionEvidence, GitMetadata
from repowise.server.mcp_server._helpers import (
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    decision_is_excluded,
)
from repowise.server.mcp_server.tool_why.caps import _DECISION_CORPUS_LIMIT


async def _git_metadata_for(session: Any, repository_id: Any, file_path: str) -> Any | None:
    """One file's git metadata row, or ``None`` when git never indexed it."""
    git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    return git_res.scalar_one_or_none()


async def _all_git_metadata(session: Any, repository_id: Any) -> list:
    """Every file's git metadata, for the archaeology cross-file search."""
    all_git_res = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
        )
    )
    return all_git_res.scalars().all()


async def _load_target_git(
    session: Any, repository_id: Any, targets: list[str] | None
) -> dict[str, Any]:
    """Load per-target git metadata keyed by file path (only present ones)."""
    target_git: dict[str, Any] = {}
    if not targets:
        return target_git
    for t in targets:
        meta = await _git_metadata_for(session, repository_id, t)
        if meta:
            target_git[t] = meta
    return target_git


async def _decision_corpus(session: Any, repository_id: str, exclude_spec: Any) -> list:
    """The rankable decision records of one repository.

    Records anchored entirely in excluded paths, and dismissed ones, are
    filtered out. Shared with workspace search so every mode ranks the same
    corpus. Takes a session so a caller can share one.
    """
    from repowise.core.persistence.crud import list_decisions as _list_decisions

    records = await _list_decisions(
        session, repository_id, include_proposed=True, limit=_DECISION_CORPUS_LIMIT
    )
    return [d for d in records if not decision_is_excluded(d, exclude_spec)]


async def _attach_decision_evidence(session: Any, records: list) -> None:
    """Attach all persisted provenance rows with one bounded query.

    The child rows are the canonical evidence behind each ``DecisionRecord``
    and must travel with it when trust metadata is projected.
    """
    if not records:
        return
    ids = [record.id for record in records]
    rows = list(
        (
            await session.execute(
                select(DecisionEvidence)
                .where(DecisionEvidence.decision_id.in_(ids))
                .order_by(
                    DecisionEvidence.source_rank.desc(),
                    DecisionEvidence.created_at.asc(),
                    DecisionEvidence.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    by_decision: dict[str, list[DecisionEvidence]] = {}
    for row in rows:
        by_decision.setdefault(row.decision_id, []).append(row)
    for record in records:
        record._why_evidence_rows = by_decision.get(record.id, [])


async def _attach_response_decision_evidence(
    session: Any, result: dict[str, Any], records: list
) -> None:
    """Hydrate the decision rows *result* currently carries.

    Search mode calls it before its cap on purpose: rows the cap sheds go to
    the omission store and must carry their evidence by then.
    """
    ids: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get("id") or value.get("decision_id")
            if isinstance(candidate, str):
                ids.add(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    await _attach_decision_evidence(
        session, [record for record in records if record.id in ids]
    )


async def _hydrate_response_decision_evidence(
    ctx: Any, result: dict[str, Any], records: list
) -> None:
    async with get_session(ctx.session_factory) as session:
        await _attach_response_decision_evidence(session, result, records)


async def _load_corpus(repo: str | None, targets: list[str] | None) -> tuple:
    """Repo context, corpus, git metadata for targets, and the acceptance set.

    The prologue both target-aware modes open with, so both answer from the
    same filtered corpus.
    """
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        all_decisions = await _decision_corpus(session, repository.id, _get_exclude_spec(ctx.path))
        # ``governing_only``: what still binds, not what was ever accepted.
        accepted = await accepted_decision_ids(
            session, repository.id, governing_only=True
        )
        # Load git metadata for targets (for origin context in results)
        target_git = await _load_target_git(session, repository.id, targets)
    return ctx, repository, all_decisions, target_git, accepted
