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
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Select,
    and_,
    bindparam,
    case,
    func,
    inspect,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ....analysis.finding_registry import excluded_types
from ...batches import chunked
from ...models import RefactoringSuggestion, _new_uuid, _now_utc
from ...sql import rule_predicate
from .._shared import _BATCH_SIZE, _finding_file_path
from .shown import gated_counts, ungated

if TYPE_CHECKING:
    from datetime import datetime

    from .refactoring_payoff import PayoffContext

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
    file_paths: Sequence[str] | None,
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


def _field(item: Any, name: str) -> Any:
    """*name* off a ``RefactoringSuggestion`` dataclass or a plain dict."""
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _target_of(item: Any) -> tuple[str, str, str]:
    return (
        _field(item, "refactoring_type"),
        _finding_file_path(item),
        _field(item, "target_symbol"),
    )


def _scope_suggestions(
    suggestions: list[Any],
    *,
    allowed: set[str] | None,
    refactoring_type: str | None,
) -> list[Any]:
    return [
        item
        for item in suggestions
        if (allowed is None or _finding_file_path(item) in allowed)
        and (refactoring_type is None or _field(item, "refactoring_type") == refactoring_type)
    ]


async def _reusable_rows(
    session: AsyncSession,
    repository_id: str,
    stored_rows: list[RefactoringSuggestion],
    public_ids: list[str],
    model_version: int,
) -> dict[str, RefactoringSuggestion]:
    """Stored current-model rows by public id, the scoped ones and any named elsewhere."""
    reusable: dict[str, RefactoringSuggestion] = {}
    for row in stored_rows:
        if row.public_id and row.model_version == model_version:
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
                RefactoringSuggestion.model_version == model_version,
                RefactoringSuggestion.public_id.in_(outside[index : index + _BATCH_SIZE]),
            )
        )
        for row in found.scalars().all():
            reusable.setdefault(row.public_id, row)
    return reusable


def _refresh_detected_row(
    row: RefactoringSuggestion,
    suggestion: Any,
    repository_id: str,
    public_id: str,
    now: datetime,
) -> bool:
    """Refresh a detected-again row in place. True when it reopened."""
    values = _refactoring_row_kwargs(suggestion, repository_id, public_id=public_id)
    for name in _REFRESHED_COLUMNS:
        if name in values:
            setattr(row, name, values[name])
    # Unranked until the finalizer ranks the refreshed content; a finalize
    # that fails then leaves no stale rank beside fresh fields.
    row.rank_position = row.blast_size = row.rank_json = None
    reopened = row.status == "resolved" and row.status_reason == "no_longer_detected"
    if reopened:
        row.status = "open"
        row.status_reason = None
        row.status_changed_at = now
    row.updated_at = now
    return reopened


def _resolve_undetected_rows(
    stored_rows: list[RefactoringSuggestion],
    seen: set[str],
    now: datetime,
    model_version: int,
) -> list[RefactoringSuggestion]:
    """Resolve every open stored row this run did not detect.

    Returns the current-model ones, the rows a payoff is settled for.
    """
    resolved: list[RefactoringSuggestion] = []
    for row in stored_rows:
        if row.public_id in seen and row.model_version == model_version:
            continue
        if row.status in ("resolved", "false_positive"):
            continue
        # An older model's row resolves because its id changed, not its code.
        if row.public_id and row.model_version == model_version:
            resolved.append(row)
        row.status = "resolved"
        row.status_reason = "no_longer_detected"
        row.status_changed_at = now
        row.updated_at = now
    return resolved


async def finalize_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    file_paths: list[str] | None = None,
    refactoring_type: str | None = None,
    payoff: PayoffContext | None = None,
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

    With *payoff*, each current-model plan this call resolves gets a payoff row
    (``refactoring_payoff``), and a reopened plan loses its own.
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

    stored_rows: list[RefactoringSuggestion] = []
    for chunk in [None] if allowed is None else chunked(sorted(allowed)):
        stored_rows.extend(
            (
                await session.execute(
                    select(RefactoringSuggestion).where(
                        *_scope_predicates(
                            repository_id,
                            file_paths=chunk,
                            refactoring_type=refactoring_type,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    reusable = await _reusable_rows(
        session, repository_id, stored_rows, public_ids, REFACTORING_MODEL_VERSION
    )

    now = _now_utc()
    seen: set[str] = set()
    pending: list[RefactoringSuggestion] = []
    reopened: list[str] = []
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
        if _refresh_detected_row(row, suggestion, repository_id, public_id, now):
            reopened.append(row.id)

    resolved = _resolve_undetected_rows(stored_rows, seen, now, REFACTORING_MODEL_VERSION)

    for index in range(0, len(pending), _BATCH_SIZE):
        for row in pending[index : index + _BATCH_SIZE]:
            session.add(row)
        await session.flush()
    await session.flush()
    if payoff is not None:
        from .refactoring_payoff import settle_refactoring_payoffs

        await settle_refactoring_payoffs(
            session,
            repository_id,
            resolved=resolved,
            reopened=reopened,
            detected={_target_of(item) for item in scoped},
            ctx=payoff,
        )

    return sum(1 for row in stored_rows if row.status == "open") + len(pending)


async def save_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    payoff: PayoffContext | None = None,
) -> None:
    """Reconcile every refactoring suggestion for *repository_id*.

    The full-reindex entry point. Accepts ``RefactoringSuggestion`` dataclasses
    or plain dicts.
    """
    await finalize_refactoring_suggestions(session, repository_id, suggestions, payoff=payoff)


async def upsert_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    suggestions: list[Any],
    *,
    file_paths: list[str],
    refactoring_type: str | None = None,
    payoff: PayoffContext | None = None,
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
        payoff=payoff,
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


def _registry_shown_plan() -> Any:
    return or_(
        RefactoringSuggestion.source_biomarker.is_(None),
        RefactoringSuggestion.source_biomarker.not_in(excluded_types()),
    )


def shown_plan_predicate(*, include_unverified: bool = False, file_holds: bool = True) -> Any:
    """Plans whose source biomarker the finding registry does not withhold, on
    files of a language no gate holds refactoring back on.

    Plans are persisted whatever the registry says; every surface that lists
    them filters here. A plan with no source biomarker (structural) is shown.
    ``include_unverified`` opts into the gated languages and the plans on test
    files; ``file_holds=False`` keeps the test-file plans without that opt-in.
    """
    return and_(
        _registry_shown_plan(),
        ungated(
            "refactoring",
            RefactoringSuggestion.file_path,
            RefactoringSuggestion.refactoring_type,
            include_unverified=include_unverified,
            file_holds=file_holds,
        ),
    )


async def gated_plan_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    status: str = "open",
    include_unverified: bool = False,
) -> dict[str, dict]:
    """The plans a language gate holds back, by language (see ``shown.gated_counts``)."""
    return await gated_counts(
        session,
        "refactoring",
        RefactoringSuggestion,
        RefactoringSuggestion.repository_id == repository_id,
        RefactoringSuggestion.status == status,
        _registry_shown_plan(),
        kind_col=RefactoringSuggestion.refactoring_type,
        include_unverified=include_unverified,
    )


def _suggestion_filters(
    repository_id: str,
    *,
    refactoring_type: str | None,
    file_paths: list[str] | None,
    min_confidence: str | None,
    status: str,
    include_unverified: bool = False,
) -> list[Any]:
    predicates: list[Any] = [
        RefactoringSuggestion.repository_id == repository_id,
        RefactoringSuggestion.status == status,
        shown_plan_predicate(include_unverified=include_unverified),
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
    include_unverified: bool = False,
) -> list[RefactoringSuggestion]:
    """Return refactoring suggestions, highest recovered impact first.

    Plans from a registry-withheld biomarker or a gated language are left out
    (:func:`shown_plan_predicate`). *limit* / *offset* page in SQL. Both default to ``None``, which returns the
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
            include_unverified=include_unverified,
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
    include_unverified: bool = False,
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
                include_unverified=include_unverified,
            )
        )
    )
    return int((await session.execute(q)).scalar_one())


# ---------------------------------------------------------------------------
# Persisted rank: written once per finalize, read by every plan list
# ---------------------------------------------------------------------------


async def store_plan_ranks(
    session: AsyncSession, repository_id: str, recommendations: list[Any]
) -> None:
    """Persist rank position, rank facts and change surface for *recommendations*.

    Every other row is cleared first, so a plan the finalizer did not rank
    (resolved, or reopened by hand since) reads as unranked and its list falls
    back to ranking live until the next index. Neither write moves
    ``updated_at``: a rank is derived, not a change to the plan.
    """
    from ....analysis.health.refactoring.recommendations import blast_size, canonical_order

    table = RefactoringSuggestion.__table__
    await session.execute(
        update(table)
        .where(table.c.repository_id == repository_id, table.c.rank_position.is_not(None))
        .values(
            rank_position=None, blast_size=None, rank_json=None, updated_at=table.c.updated_at
        )
    )
    values = [
        {
            "plan_id": item.id,
            "position": position,
            "surface": blast_size(item.suggestion),
            "facts": json.dumps(item.rank_facts(), separators=(",", ":")),
        }
        for position, item in enumerate(canonical_order(recommendations))
    ]
    statement = (
        update(table)
        .where(table.c.id == bindparam("plan_id"))
        .values(
            rank_position=bindparam("position"),
            blast_size=bindparam("surface"),
            rank_json=bindparam("facts"),
            updated_at=table.c.updated_at,
        )
    )
    for index in range(0, len(values), _BATCH_SIZE):
        await session.execute(statement, values[index : index + _BATCH_SIZE])
    _sync_loaded_ranks(session, repository_id, values)


def _sync_loaded_ranks(
    session: AsyncSession, repository_id: str, values: list[dict[str, Any]]
) -> None:
    """Give every plan this session holds the rank just written: the Core writes
    bypass the identity map, and a later select returns a held instance as loaded."""
    # Not an ORM bulk update by id instead: that fires ``updated_at``'s onupdate.
    written = {item["plan_id"]: item for item in values}
    for instance in list(session.sync_session.identity_map.values()):
        if not isinstance(instance, RefactoringSuggestion):
            continue
        loaded = inspect(instance).dict
        if loaded.get("repository_id") != repository_id:
            continue
        # A plan this finalize did not rank was cleared above, so it reads None.
        item = written.get(loaded.get("id"), {})
        set_committed_value(instance, "rank_position", item.get("position"))
        set_committed_value(instance, "blast_size", item.get("surface"))
        set_committed_value(instance, "rank_json", item.get("facts"))


def _base_order(view: str, predicates: list[Any]) -> tuple[Select[Any], tuple[Any, ...]]:
    """The plan query and its view order, before any narrowing filter.

    ``file_spread`` deals one plan per file per round over the whole base set,
    files in order of their best plan, as :func:`apply_view` does in memory.
    """
    if view != "file_spread":
        query = select(RefactoringSuggestion).where(*predicates)
        return query, (RefactoringSuggestion.rank_position,)
    position = RefactoringSuggestion.rank_position
    spread = (
        select(
            RefactoringSuggestion.id.label("plan_id"),
            func.row_number()
            .over(partition_by=RefactoringSuggestion.file_path, order_by=position)
            .label("spread_round"),
            func.min(position).over(partition_by=RefactoringSuggestion.file_path).label("first"),
        )
        .where(*predicates)
        .subquery()
    )
    query = select(RefactoringSuggestion).join(spread, spread.c.plan_id == RefactoringSuggestion.id)
    return query, (spread.c.spread_round, spread.c.first)


async def ranked_refactoring_suggestions(
    session: AsyncSession,
    repository_id: str,
    *,
    min_confidence: str | None = None,
    filters: Mapping[str, Any] | None = None,
    view: str = "canonical",
    include_unverified: bool = False,
) -> list[RefactoringSuggestion] | None:
    """The open plans in persisted rank order.

    *filters* are the params of the shared ``PLAN_FILTERS``. They narrow after
    the view is dealt, as the in-memory path does, so a filtered
    ``file_spread`` keeps the order the unfiltered one had. ``None`` when an
    open plan was not ranked by the last finalize: the caller ranks live.
    """
    from ....analysis.health.queue_rules import active_filters
    from ....analysis.health.refactoring.recommendations import PLAN_FILTERS

    base = _suggestion_filters(
        repository_id,
        refactoring_type=None,
        file_paths=None,
        min_confidence=min_confidence,
        status="open",
        include_unverified=include_unverified,
    )
    unranked = await session.execute(
        select(RefactoringSuggestion.id)
        .where(*base, RefactoringSuggestion.rank_position.is_(None))
        .limit(1)
    )
    if unranked.first() is not None:
        return None
    query, order = _base_order(view, base)
    narrowing = [
        rule_predicate(RefactoringSuggestion, rule, value)
        for rule, value in active_filters(PLAN_FILTERS, filters or {})
    ]
    query = query.where(*narrowing).order_by(*order)
    return list((await session.execute(query)).scalars().all())


async def summarize_open_plans(
    session: AsyncSession,
    repository_id: str,
    *,
    min_confidence: str | None = None,
    include_unverified: bool = False,
) -> dict[str, Any]:
    """The plan board's chip counts over the open plans, from narrow columns.

    The plan payload is read only for the grouping types whose design count
    needs it.
    """
    from ....analysis.health.refactoring_summary import GROUPING_TYPES, summarize_plans

    kind = RefactoringSuggestion.refactoring_type
    rows = await session.execute(
        select(
            kind,
            RefactoringSuggestion.file_path,
            RefactoringSuggestion.effort_bucket,
            RefactoringSuggestion.impact_delta,
            case(
                (kind.in_(sorted(GROUPING_TYPES)), RefactoringSuggestion.plan_json), else_=None
            ).label("plan_json"),
        ).where(
            *_suggestion_filters(
                repository_id,
                refactoring_type=None,
                file_paths=None,
                min_confidence=min_confidence,
                status="open",
                include_unverified=include_unverified,
            )
        )
    )
    return summarize_plans(rows.all())


async def refactoring_suggestions_by_public_id(
    session: AsyncSession, repository_id: str, public_ids: list[str]
) -> list[RefactoringSuggestion]:
    """The plans named by *public_ids*, in that order, in one indexed query."""
    if not public_ids:
        return []
    rows = (
        await session.execute(
            select(RefactoringSuggestion).where(
                RefactoringSuggestion.repository_id == repository_id,
                RefactoringSuggestion.public_id.in_(public_ids),
            )
        )
    ).scalars()
    by_id = {row.public_id: row for row in rows}
    return [by_id[pid] for pid in public_ids if pid in by_id]
