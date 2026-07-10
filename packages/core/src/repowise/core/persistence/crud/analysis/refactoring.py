"""CRUD operations for refactoring suggestions (repowise persistence layer)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import RefactoringSuggestion, _new_uuid
from .._shared import _BATCH_SIZE, _finding_file_path


def _refactoring_row_kwargs(suggestion: Any, repository_id: str) -> dict:
    """Normalize a ``RefactoringSuggestion`` dataclass or a plain dict into
    kwargs for the ORM row (folding the open ``plan`` / ``evidence`` /
    ``blast_radius`` dicts into their ``*_json`` columns)."""
    if hasattr(suggestion, "refactoring_type"):
        data = {
            "refactoring_type": suggestion.refactoring_type,
            "file_path": suggestion.file_path,
            "target_symbol": suggestion.target_symbol,
            "line_start": suggestion.line_start,
            "line_end": suggestion.line_end,
            "plan_json": json.dumps(suggestion.plan or {}),
            "evidence_json": json.dumps(suggestion.evidence or {}),
            "impact_delta": float(suggestion.impact_delta),
            "effort_bucket": suggestion.effort_bucket,
            "blast_radius_json": json.dumps(suggestion.blast_radius or {}),
            "confidence": suggestion.confidence,
            "source_biomarker": suggestion.source_biomarker,
        }
    else:
        data = dict(suggestion)
        for key in ("plan", "evidence", "blast_radius"):
            if key in data:
                data[f"{key}_json"] = json.dumps(data.pop(key) or {})

    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(RefactoringSuggestion, k)
        },
    }


async def save_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
) -> None:
    """Replace open refactoring suggestions for *repository_id*.

    Delete-then-insert, mirroring ``save_health_findings``. Accepts
    ``RefactoringSuggestion`` dataclasses or plain dicts.
    """
    existing = await session.execute(
        select(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.status == "open",
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    for i in range(0, len(suggestions), _BATCH_SIZE):
        batch = suggestions[i : i + _BATCH_SIZE]
        for s in batch:
            session.add(RefactoringSuggestion(**_refactoring_row_kwargs(s, repository_id)))
        await session.flush()


async def upsert_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    file_paths: list[str],
) -> None:
    """Replace open suggestions **only for the given file paths**.

    The incremental ``repowise update`` sibling of
    ``save_refactoring_suggestions``: unchanged files keep their suggestions.
    Pass the full set of *changed* paths (not just those that produced a
    suggestion) so a changed-but-now-clean file is cleared.
    """
    if not file_paths:
        return
    allowed = set(file_paths)
    existing = await session.execute(
        select(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.status == "open",
            RefactoringSuggestion.file_path.in_(file_paths),
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    await session.flush()

    scoped = [s for s in suggestions if _finding_file_path(s) in allowed]
    for i in range(0, len(scoped), _BATCH_SIZE):
        batch = scoped[i : i + _BATCH_SIZE]
        for s in batch:
            session.add(RefactoringSuggestion(**_refactoring_row_kwargs(s, repository_id)))
        await session.flush()


async def get_refactoring_suggestion(
    session: AsyncSession,
    repository_id: str,
    suggestion_id: str,
) -> RefactoringSuggestion | None:
    """Return one refactoring suggestion by id, scoped to *repository_id*.

    Powers the web tab's plan-detail drill-down (and any deep link to a single
    plan). Returns ``None`` when the id is unknown or belongs to another repo.
    """
    result = await session.execute(
        select(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.id == suggestion_id,
        )
    )
    return result.scalar_one_or_none()


async def get_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    *,
    refactoring_type: str | None = None,
    file_paths: list[str] | None = None,
    min_confidence: str | None = None,
    status: str = "open",
) -> list[RefactoringSuggestion]:
    """Return refactoring suggestions, highest recovered impact first."""
    q = select(RefactoringSuggestion).where(
        RefactoringSuggestion.repository_id == repository_id,
        RefactoringSuggestion.status == status,
    )
    if refactoring_type is not None:
        q = q.where(RefactoringSuggestion.refactoring_type == refactoring_type)
    if file_paths is not None:
        q = q.where(RefactoringSuggestion.file_path.in_(file_paths))
    if min_confidence is not None:
        order = {"low": 0, "medium": 1, "high": 2}
        threshold = order.get(min_confidence, 0)
        allowed = [k for k, v in order.items() if v >= threshold]
        q = q.where(RefactoringSuggestion.confidence.in_(allowed))
    # Secondary keys (file_path, target_symbol) make the read order stable for
    # ties — notably the common 0.0 no-finding case — so it matches the
    # detector's own deterministic ordering rather than DB row order.
    q = q.order_by(
        RefactoringSuggestion.impact_delta.desc(),
        RefactoringSuggestion.file_path.asc(),
        RefactoringSuggestion.target_symbol.asc(),
    )
    result = await session.execute(q)
    return list(result.scalars().all())
