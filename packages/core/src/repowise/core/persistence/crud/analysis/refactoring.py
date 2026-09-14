"""CRUD operations for refactoring suggestions (repowise persistence layer).

One writer owns lifecycle. :func:`finalize_refactoring_suggestions` reconciles a
detector run against what is stored: a plan whose content is unchanged keeps its
row, its id and whatever a person decided about it; a plan nobody detects any
more is resolved rather than deleted, so an id an agent is holding keeps
answering; and a plan someone called a false positive is never re-emitted. The
full and incremental index paths differ only in the scope they hand it, which is
what makes them agree.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import RefactoringSuggestion, _new_uuid, _now_utc
from .._shared import _BATCH_SIZE, _finding_file_path

# The finding-triage vocabulary, shared with health findings so Code Health has
# one triage system rather than one per layer.
ALLOWED_STATUSES = ("open", "acknowledged", "resolved", "false_positive")

# Owned end to end by ``crud/analysis/performance.py``, which rebuilds these
# rows from the merged stored findings once per index on both paths.
_PERFORMANCE_TYPE = "performance_fix"

# Columns the writer refreshes on an unchanged plan. Everything absent here -
# id, public_id, model_version, status, status_reason, created_at - is either
# identity or a decision, and belongs to the row rather than to the run.
_REFRESHED_COLUMNS = (
    "file_path",
    "target_symbol",
    "line_start",
    "line_end",
    "plan_json",
    "evidence_json",
    "impact_delta",
    "effort_bucket",
    "blast_radius_json",
    "confidence",
    "source_biomarker",
    "opportunity_id",
)


def _refactoring_row_kwargs(
    suggestion: Any, repository_id: str, *, public_id: str | None = None
) -> dict:
    """Normalize a ``RefactoringSuggestion`` dataclass or a plain dict into
    kwargs for the ORM row (folding the open ``plan`` / ``evidence`` /
    ``blast_radius`` dicts into their ``*_json`` columns)."""
    from ....analysis.health.refactoring.identity import (
        REFACTORING_MODEL_VERSION,
        refactoring_public_id,
    )

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

    if data.get("refactoring_type") == "performance_fix":
        # Lift the causal id out of the plan payload into its own column. Two
        # surfaces used to answer "does this opportunity have a plan" by reading
        # two different JSON fields, and one of them always said no.
        try:
            plan = json.loads(data.get("plan_json") or "{}")
        except (TypeError, ValueError):
            plan = {}
        if isinstance(plan, dict) and isinstance(plan.get("opportunity_id"), str):
            data.setdefault("opportunity_id", plan["opportunity_id"])

    data["public_id"] = public_id or refactoring_public_id(suggestion)
    data["model_version"] = REFACTORING_MODEL_VERSION

    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(RefactoringSuggestion, k)
        },
    }


def _scope_predicates(
    repository_id: str,
    *,
    file_paths: list[str] | None,
    refactoring_type: str | None,
) -> list[Any]:
    predicates: list[Any] = [RefactoringSuggestion.repository_id == repository_id]
    if file_paths is not None:
        predicates.append(RefactoringSuggestion.file_path.in_(file_paths))
    if refactoring_type is not None:
        predicates.append(RefactoringSuggestion.refactoring_type == refactoring_type)
    else:
        # Performance plans are the performance finalizer's rows: it rebuilds
        # them from the merged stored findings on both index paths. Reconciling
        # them here would resolve live plans this call was simply never told
        # about, and today only the finalizer running moments later hides it.
        predicates.append(RefactoringSuggestion.refactoring_type != _PERFORMANCE_TYPE)
    return predicates


def _scope_suggestions(
    suggestions: list[Any],
    *,
    allowed: set[str] | None,
    refactoring_type: str | None,
) -> list[Any]:
    def _type_of(item: Any) -> Any:
        if hasattr(item, "refactoring_type"):
            return item.refactoring_type
        return item.get("refactoring_type")

    return [
        item
        for item in suggestions
        if (allowed is None or _finding_file_path(item) in allowed)
        and (refactoring_type is None or _type_of(item) == refactoring_type)
    ]


async def finalize_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    file_paths: list[str] | None = None,
    refactoring_type: str | None = None,
) -> int:
    """Reconcile a detector run against the stored plans. Returns rows left open.

    *file_paths* scopes the reconciliation to the files an incremental run
    touched; ``None`` means the whole repository, which is what a full index
    hands it. *refactoring_type* narrows it further for a type-scoped partial
    run. Everything outside the scope is left exactly as it was, and so are
    ``performance_fix`` rows unless they are named explicitly: the performance
    finalizer rebuilds those from the merged stored findings on both paths.

    Lifecycle:

    - a stored plan whose kernel is detected again keeps its row, its public id,
      its ``created_at`` and its triage state, and has its mutable fields
      refreshed;
    - a plan an earlier run resolved and this one detects again reopens, because
      the detector disagreeing with ``no_longer_detected`` is the whole signal.
      A plan a person resolved stays resolved;
    - a ``false_positive`` kernel is never re-emitted;
    - a stored plan nobody detected becomes ``resolved`` with reason
      ``no_longer_detected`` rather than being deleted, so a held id keeps
      answering and stops reading as current;
    - a row from an older model, or one written before public ids existed, is
      resolved for the same reason. Ids are not translated across models.
    """
    from ....analysis.health.refactoring.identity import (
        REFACTORING_MODEL_VERSION,
        assign_public_ids,
    )

    allowed = set(file_paths) if file_paths is not None else None
    scoped = _scope_suggestions(
        suggestions, allowed=allowed, refactoring_type=refactoring_type
    )
    public_ids = assign_public_ids(scoped)

    stored_rows = list(
        (
            await session.execute(
                select(RefactoringSuggestion).where(
                    *_scope_predicates(
                        repository_id,
                        file_paths=file_paths,
                        refactoring_type=refactoring_type,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    reusable: dict[str, RefactoringSuggestion] = {}
    for row in stored_rows:
        if row.public_id and row.model_version == REFACTORING_MODEL_VERSION:
            reusable[row.public_id] = row

    # A clone group and an import cycle are named by their members rather than
    # by the file the row happens to be anchored at, so a scoped run can hold a
    # plan whose stored row sits outside the scope. Look those up by id before
    # deciding anything is new: inserting instead would duplicate the plan, and
    # the identity is unique per repository and model.
    outside = sorted({pid for pid in public_ids if pid not in reusable})
    for index in range(0, len(outside), _BATCH_SIZE):
        found = await session.execute(
            select(RefactoringSuggestion).where(
                RefactoringSuggestion.repository_id == repository_id,
                RefactoringSuggestion.model_version == REFACTORING_MODEL_VERSION,
                RefactoringSuggestion.public_id.in_(outside[index : index + _BATCH_SIZE]),
            )
        )
        for row in found.scalars().all():
            reusable.setdefault(row.public_id, row)

    now = _now_utc()
    seen: set[str] = set()
    pending: list[RefactoringSuggestion] = []
    for suggestion, public_id in zip(scoped, public_ids, strict=True):
        if public_id in seen:
            # Two plans reaching one id would violate the uniqueness readers rely
            # on. assign_public_ids breaks kernel collisions, so reaching here
            # means the detector emitted the same plan twice; keep the first.
            continue
        seen.add(public_id)
        row = reusable.get(public_id)
        if row is None:
            pending.append(
                RefactoringSuggestion(
                    **_refactoring_row_kwargs(suggestion, repository_id, public_id=public_id)
                )
            )
            continue
        if row.status == "false_positive":
            continue
        values = _refactoring_row_kwargs(suggestion, repository_id, public_id=public_id)
        for name in _REFRESHED_COLUMNS:
            if name in values:
                setattr(row, name, values[name])
        if row.status == "resolved" and row.status_reason == "no_longer_detected":
            row.status = "open"
            row.status_reason = None
            row.status_changed_at = now
        row.updated_at = now

    for row in stored_rows:
        if row.public_id in seen and row.model_version == REFACTORING_MODEL_VERSION:
            continue
        if row.status in ("resolved", "false_positive"):
            continue
        row.status = "resolved"
        row.status_reason = "no_longer_detected"
        row.status_changed_at = now
        row.updated_at = now

    for index in range(0, len(pending), _BATCH_SIZE):
        for row in pending[index : index + _BATCH_SIZE]:
            session.add(row)
        await session.flush()
    await session.flush()

    return sum(1 for row in stored_rows if row.status == "open") + len(pending)


async def save_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
) -> None:
    """Reconcile every refactoring suggestion for *repository_id*.

    The full-reindex entry point. Accepts ``RefactoringSuggestion`` dataclasses
    or plain dicts.
    """
    await finalize_refactoring_suggestions(session, repository_id, suggestions)


async def upsert_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    file_paths: list[str],
    refactoring_type: str | None = None,
) -> None:
    """Reconcile suggestions **only for the given file paths**.

    The incremental ``repowise update`` sibling of
    ``save_refactoring_suggestions``: unchanged files keep their suggestions.
    Pass the full set of *changed* paths (not just those that produced a
    suggestion) so a changed-but-now-clean file is resolved.
    """
    if not file_paths:
        return
    await finalize_refactoring_suggestions(
        session,
        repository_id,
        suggestions,
        file_paths=list(file_paths),
        refactoring_type=refactoring_type,
    )


async def update_refactoring_suggestion_status(
    session: AsyncSession,
    repository_id: str,
    suggestion_id: str,
    status: str,
    *,
    reason: str = "user",
) -> RefactoringSuggestion | None:
    """Transition one plan's lifecycle state. The single owner of that write.

    *suggestion_id* is the storage id or the content-derived public id, because
    the two surfaces that address a plan quote different strings. Returns
    ``None`` when the id is unknown or belongs to another repository, and raises
    ``ValueError`` for a status outside the triage vocabulary.
    """
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unknown refactoring status: {status}")
    row = await get_refactoring_suggestion(session, repository_id, suggestion_id)
    if row is None:
        return None
    row.status = status
    row.status_reason = reason
    row.status_changed_at = _now_utc()
    await session.flush()
    return row


async def get_refactoring_suggestion(
    session: AsyncSession,
    repository_id: str,
    suggestion_id: str,
) -> RefactoringSuggestion | None:
    """Return one refactoring suggestion by id, scoped to *repository_id*.

    Resolves the storage id first, then the content-derived public id, so a deep
    link minted by either surface lands on the row. Both are indexed point
    lookups. Returns ``None`` when the id is unknown or belongs to another repo.
    """
    result = await session.execute(
        select(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.id == suggestion_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    result = await session.execute(
        select(RefactoringSuggestion)
        .where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.public_id == suggestion_id,
        )
        .order_by(RefactoringSuggestion.model_version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _suggestion_filters(
    repository_id: str,
    *,
    refactoring_type: str | None,
    file_paths: list[str] | None,
    min_confidence: str | None,
    status: str,
) -> list[Any]:
    predicates: list[Any] = [
        RefactoringSuggestion.repository_id == repository_id,
        RefactoringSuggestion.status == status,
    ]
    if refactoring_type is not None:
        predicates.append(RefactoringSuggestion.refactoring_type == refactoring_type)
    if file_paths is not None:
        predicates.append(RefactoringSuggestion.file_path.in_(file_paths))
    if min_confidence is not None:
        order = {"low": 0, "medium": 1, "high": 2}
        threshold = order.get(min_confidence, 0)
        allowed = [k for k, v in order.items() if v >= threshold]
        predicates.append(RefactoringSuggestion.confidence.in_(allowed))
    return predicates


async def get_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    *,
    refactoring_type: str | None = None,
    file_paths: list[str] | None = None,
    min_confidence: str | None = None,
    status: str = "open",
    limit: int | None = None,
    offset: int | None = None,
) -> list[RefactoringSuggestion]:
    """Return refactoring suggestions, highest recovered impact first.

    *limit* / *offset* page in SQL. Both default to ``None``, which returns the
    whole filtered set exactly as before, because the callers that still rank
    and page in memory have not been rewired yet (R4).
    """
    q = select(RefactoringSuggestion).where(
        *_suggestion_filters(
            repository_id,
            refactoring_type=refactoring_type,
            file_paths=file_paths,
            min_confidence=min_confidence,
            status=status,
        )
    )
    # Secondary keys (file_path, target_symbol) make the read order stable for
    # ties — notably the common 0.0 no-finding case — so it matches the
    # detector's own deterministic ordering rather than DB row order.
    q = q.order_by(
        RefactoringSuggestion.impact_delta.desc(),
        RefactoringSuggestion.file_path.asc(),
        RefactoringSuggestion.target_symbol.asc(),
    )
    if offset is not None:
        q = q.offset(offset)
    if limit is not None:
        q = q.limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def count_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    *,
    refactoring_type: str | None = None,
    file_paths: list[str] | None = None,
    min_confidence: str | None = None,
    status: str = "open",
) -> int:
    """The total behind a page, counted in SQL rather than by materializing it."""
    q = (
        select(func.count())
        .select_from(RefactoringSuggestion)
        .where(
            *_suggestion_filters(
                repository_id,
                refactoring_type=refactoring_type,
                file_paths=file_paths,
                min_confidence=min_confidence,
                status=status,
            )
        )
    )
    return int((await session.execute(q)).scalar_one())
