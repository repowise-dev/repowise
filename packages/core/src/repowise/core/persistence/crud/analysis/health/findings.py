"""Health findings: the writers that keep a person's triage, and the reads
every findings surface narrows and tiers through."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .....analysis.finding_registry import excluded_types
from .....analysis.health.biomarker_names import canonical_biomarker
from .....analysis.health.finding_identity import (
    finding_public_id,
    legacy_finding_public_id,
)
from .....analysis.health.governance import GOVERNANCE_BIOMARKERS
from .....analysis.health.rows import detail_map
from .....analysis.health.scoring import ADVISORY_DIMENSION
from .....analysis.health.worth import LowPriority, finding_priorities
from ....batches import chunked
from ....models import HealthFinding, _new_uuid, _now_utc
from ..._shared import _BATCH_SIZE
from ...graph import get_test_file_paths
from ..shown import gated_counts, shown
from .exclude import _filter_excluded_paths, health_exclude_spec


def _health_finding_row_kwargs(finding: Any, repository_id: str) -> dict:
    """Normalize an analyzer dataclass or a plain dict into ORM row kwargs.

    All three writers land here, so a column added to the row is written by
    every path or by none. ``public_id`` is stamped here because this is the
    one place every persisted finding passes through.
    """
    if hasattr(finding, "biomarker_type"):
        severity = finding.severity
        data = {
            "file_path": finding.file_path,
            "biomarker_type": finding.biomarker_type,
            "severity": str(severity.value) if hasattr(severity, "value") else str(severity),
            "function_name": finding.function_name,
            "line_start": finding.line_start,
            "line_end": finding.line_end,
            "details_json": json.dumps(finding.details or {}),
            "health_impact": float(finding.health_impact),
            "reason": finding.reason or "",
            "dimension": getattr(finding, "dimension", None) or "defect",
        }
    else:
        data = dict(finding)
        if "details" in data:
            data["details_json"] = json.dumps(data.pop("details") or {})
    data.setdefault("public_id", finding_public_id(finding))
    if data.get("dimension") == "performance":
        # Present before the finalizer runs because the analyzer stamps it into
        # ``details``; the finalizer restates it from authoritative stored rows.
        data.setdefault("opportunity_id", detail_map(finding).get("opportunity_id"))
    return {
        "id": _new_uuid(),
        "repository_id": repository_id,
        **{
            k: v
            for k, v in data.items()
            if k not in ("id", "repository_id") and hasattr(HealthFinding, k)
        },
    }


_NOT_REFRESHED = frozenset({"id", "repository_id", "status", "created_at"})


async def _triaged_by_public_id(
    session: AsyncSession, scope: list[Any]
) -> dict[str, list[HealthFinding]]:
    """The triaged rows in *scope* by public id, most recently updated first."""
    triaged: dict[str, list[HealthFinding]] = {}
    rows = await session.execute(
        select(HealthFinding)
        .where(*scope, HealthFinding.status != "open", HealthFinding.public_id.is_not(None))
        .order_by(HealthFinding.updated_at.desc())
    )
    for row in rows.scalars().all():
        triaged.setdefault(row.public_id, []).append(row)
    return triaged


def _triaged_rows_for(
    triaged: dict[str, list[HealthFinding]], values: dict, finding: Any
) -> list[HealthFinding]:
    """The triaged rows *finding* folds into: by its public id, else its legacy one."""
    if not triaged:
        return []
    return (
        triaged.get(values["public_id"]) or triaged.get(legacy_finding_public_id(finding)) or []
    )


async def _fold_into_triaged(
    session: AsyncSession,
    kept: HealthFinding,
    extra: list[HealthFinding],
    values: dict,
    now: datetime,
) -> None:
    """Refresh *kept* from *values*, keeping its triage; drop the *extra* rows."""
    for row in extra:
        await session.delete(row)
    for name, value in values.items():
        if name not in _NOT_REFRESHED:
            setattr(kept, name, value)
    if kept.status == "resolved":
        kept.status = "open"
    kept.updated_at = now


async def _delete_open(session: AsyncSession, scope: list[Any]) -> None:
    """Delete the open rows in *scope* in one statement; triaged rows stay."""
    await session.execute(delete(HealthFinding).where(*scope, HealthFinding.status == "open"))


async def _insert_keeping_triage(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    scope: list[Any],
) -> None:
    """Insert *findings*, keeping the triage a person recorded on any of them.

    Callers delete the open rows in *scope* first. A finding whose public id
    matches a triaged row in *scope* is folded into that row: its detection
    fields are refreshed and ``acknowledged`` / ``false_positive`` stand, while
    ``resolved`` reopens because the finding said to be fixed is still there.
    Extra triaged rows sharing that id are deleted. Every other finding, a
    second one on an already claimed id included, is inserted open.
    """
    triaged = await _triaged_by_public_id(session, scope)
    now = _now_utc()
    claimed: set[str] = set()
    for i in range(0, len(findings), _BATCH_SIZE):
        for f in findings[i : i + _BATCH_SIZE]:
            values = _health_finding_row_kwargs(f, repository_id)
            kept, *extra = _triaged_rows_for(triaged, values, f) or [None]
            if kept is None or kept.id in claimed:
                # A second finding on a claimed id is inserted open: two
                # findings colliding on one id must not lose either.
                session.add(HealthFinding(**values))
                continue
            claimed.add(kept.id)
            await _fold_into_triaged(session, kept, extra, values, now)
        await session.flush()


async def save_health_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
) -> None:
    """Replace open health findings for *repository_id* with *findings*.

    Delete-then-insert over the open rows; triaged rows carry over by public id
    (``_insert_keeping_triage``). Accepts either ``HealthFindingData``
    dataclasses or plain dicts.
    """
    scope = [HealthFinding.repository_id == repository_id]
    await _delete_open(session, scope)
    await _insert_keeping_triage(session, repository_id, findings, scope)


async def replace_governance_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
) -> None:
    """Idempotent additive write of governance-layer health findings.

    Deletes the open ``health_findings`` rows whose ``biomarker_type``
    is one of ``ungoverned_hotspot``, ``stale_governance``, or
    ``contradictory_decision`` for *repository_id*, then inserts the new
    *findings* in batches, keeping triage by public id like the other writers.

    This function deliberately does **not** recompute ``HealthFileMetric.score``
    — that pass has already completed in the upstream health-analysis phase.
    Governance findings surface through the findings layer (``get_risk``
    ``top_biomarkers``, ``get_context`` health block) rather than the numeric
    score.  A second score-recomputation pass would require re-loading the full
    per-file results table; the conservative choice is to leave scores as-is
    and let findings carry the governance signal.

    Composable with ``save_health_findings``: the delete is scoped to only
    the three governance biomarker types, so structural findings written by
    ``save_health_findings`` are untouched.

    Accepts ``HealthFindingData`` dataclasses or plain dicts (same protocol
    as ``save_health_findings``).
    """
    # Delete existing open governance findings for this repo only.
    scope = [
        HealthFinding.repository_id == repository_id,
        HealthFinding.biomarker_type.in_(list(GOVERNANCE_BIOMARKERS)),
    ]
    await _delete_open(session, scope)
    await _insert_keeping_triage(session, repository_id, findings, scope)


async def upsert_health_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[Any],
    *,
    file_paths: list[str],
    dimension: str | None = None,
) -> None:
    """Replace open findings **only for the given file paths**.

    Used by the incremental ``repowise update`` path so unchanged files
    keep their findings instead of being wiped on every partial re-index.
    """
    # One pass per piece of the paths. A finding's public id carries its file,
    # so it only ever folds into a triaged row of its own piece.
    for chunk in chunked(sorted(set(file_paths))):
        predicates = [
            HealthFinding.repository_id == repository_id,
            HealthFinding.file_path.in_(chunk),
        ]
        if dimension is not None:
            predicates.append(HealthFinding.dimension == dimension)
        await _delete_open(session, predicates)

        allowed = set(chunk)
        scoped = [
            finding
            for finding in findings
            if (finding.file_path if hasattr(finding, "file_path") else finding.get("file_path"))
            in allowed
            and (
                dimension is None
                or (
                    getattr(finding, "dimension", None)
                    if hasattr(finding, "dimension")
                    else finding.get("dimension")
                )
                == dimension
            )
        ]
        await _insert_keeping_triage(session, repository_id, scoped, predicates)


async def update_health_finding_status(
    session: AsyncSession,
    finding_id: str,
    status: str,
) -> HealthFinding | None:
    f = await session.get(HealthFinding, finding_id)
    if f is None:
        return None
    f.status = status
    await session.flush()
    return f


def open_finding_count(findings: Iterable[Any]) -> int:
    """The open findings total every health headline prints.

    Advisory findings carry a zero health impact, so counting them would grow
    the headline without anything having got worse.
    """
    return sum(1 for f in findings if getattr(f, "dimension", None) != ADVISORY_DIMENSION)


async def get_health_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    biomarker_type: str | None = None,
    min_severity: str | None = None,
    severity: str | None = None,
    file_path: str | None = None,
    file_paths: Sequence[str] | None = None,
    dimension: str | None = None,
    exclude_dimensions: tuple[str, ...] | None = None,
    status: str = "open",
    include_withheld: bool = False,
    include_unverified: bool = False,
    file_holds: bool = True,
    hold_history: bool = True,
    limit: int | None = None,
) -> list[HealthFinding]:
    """Findings for one repository: production ahead of test files, worth doing
    first ahead of lower-priority ones (``worth.finding_priorities``), each part
    ordered by health impact.

    Finding types the registry withholds (``finding_registry``) are left out,
    so every surface built on this read shows only what has earned a place; a
    provisional type named in ``biomarker_type`` is an explicit request and is
    returned. Files of a language the registry gates for health findings are
    left out too, unless ``include_unverified`` (which also opts into every
    provisional type). ``include_withheld`` is for analysis that must see every
    row. ``file_holds=False`` keeps the findings held back by their file
    (vendored, generated, history on a test) for a reader that sums scores or
    KPIs, which count them. ``hold_history=False`` keeps history markers on
    production files for a view that treats history itself.

    ``exclude_dimensions`` is how a general queue keeps a dimension out of a
    ranking it does not share units with. It is ignored when ``dimension``
    names one explicitly, so asking for a dimension always returns it.

    ``severity`` is an exact comma-separated selection and wins over the
    ``min_severity`` threshold when both are given.

    ``status`` defaults to open work. It also accepts a comma-separated list of
    ``open`` / ``acknowledged`` / ``resolved`` / ``false_positive``, or ``all``
    to drop the filter, so a triage surface can show what it has already
    reviewed without a second read.

    ``file_paths`` scopes to a set of files in one read; an empty sequence
    matches nothing. ``limit`` caps the rows returned, in that order.
    """
    q = select(HealthFinding).where(HealthFinding.repository_id == repository_id)
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    if "all" not in statuses:
        q = q.where(HealthFinding.status.in_(statuses or ["open"]))
    if dimension is None and exclude_dimensions:
        q = q.where(
            or_(
                HealthFinding.dimension.is_(None),
                HealthFinding.dimension.not_in(list(exclude_dimensions)),
            )
        )
    # Accept a comma-separated list so a caller can pull several biomarker
    # types in one request (e.g. the function-level + coupling panels).
    # A single value with no comma still matches exactly (``IN`` of one).
    types = [
        canonical_biomarker(t.strip()) for t in (biomarker_type or "").split(",") if t.strip()
    ]
    if types:
        q = q.where(HealthFinding.biomarker_type.in_(types))
    if not include_withheld:
        q = q.where(
            shown(
                "health",
                HealthFinding.biomarker_type,
                HealthFinding.file_path,
                requested=types,
                include_unverified=include_unverified,
                file_holds=file_holds,
                hold_history=hold_history,
            )
        )
    if file_path is not None:
        q = q.where(HealthFinding.file_path == file_path)
    if file_paths is not None:
        q = q.where(HealthFinding.file_path.in_(list(file_paths)))
    if dimension is not None:
        # Older rows predate the split and carry a NULL dimension that homes
        # under "defect"; fold those in so a defect filter never drops them.
        if dimension == "defect":
            q = q.where(or_(HealthFinding.dimension == "defect", HealthFinding.dimension.is_(None)))
        else:
            q = q.where(HealthFinding.dimension == dimension)
    exact = [v.strip().lower() for v in (severity or "").split(",") if v.strip()]
    if exact:
        # An exact selection answers the same question the queue's chips do, so
        # the two levels of the list agree on which findings a row holds.
        q = q.where(HealthFinding.severity.in_(exact))
    elif min_severity is not None:
        # Severity order: low < medium < high < critical
        order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = order.get(min_severity, 0)
        allowed = [k for k, v in order.items() if v >= threshold]
        q = q.where(HealthFinding.severity.in_(allowed))
    q = q.order_by(HealthFinding.health_impact.desc())
    result = await session.execute(q)
    kept = _filter_excluded_paths(
        list(result.scalars().all()),
        await health_exclude_spec(session, repository_id),
    )
    # A filter that drops some of a function's findings would misread its
    # shape, so the tier then reads every open finding on the kept files.
    narrowed = bool(types or exact or min_severity or dimension) or statuses != ["open"]
    reasons = (
        await health_finding_priorities(session, repository_id, kept)
        if narrowed
        else dict(zip((f.id for f in kept), finding_priorities(kept), strict=True))
    )
    # Test files go below production, then lower-priority below worth doing.
    # Stable: each tier keeps the impact order. The cap applies after the tier.
    tests = await get_test_file_paths(
        session,
        repository_id,
        None if file_path is None and file_paths is None else {f.file_path for f in kept},
    )
    ordered = sorted(kept, key=lambda f: (f.file_path in tests, reasons[f.id] is not None))
    return ordered if limit is None else ordered[:limit]


async def gated_health_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    file_path: str | None = None,
    include_unverified: bool = False,
    hold_history: bool = True,
) -> dict[str, dict]:
    """``{language or file hold: {count, reason}}`` over the open findings
    :func:`get_health_findings` leaves out by default, so a list says why it
    is shorter than the stored scores suggest."""
    where = [
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == "open",
        HealthFinding.biomarker_type.not_in(excluded_types()),
    ]
    if file_path is not None:
        where.append(HealthFinding.file_path == file_path)
    return await gated_counts(
        session,
        "health",
        HealthFinding,
        *where,
        kind_col=HealthFinding.biomarker_type,
        include_unverified=include_unverified,
        hold_history=hold_history,
    )


async def health_finding_priorities(
    session: AsyncSession, repository_id: str, findings: Sequence[HealthFinding]
) -> dict[str, LowPriority | None]:
    """Each finding's ``worth`` reason by id, measured over every open,
    shown finding on its file: a list filtered by marker, severity or status
    still tiers a function by its whole shape."""
    paths = sorted({f.file_path for f in findings})
    peers: list[HealthFinding] = []
    for i in range(0, len(paths), _BATCH_SIZE):
        q = select(HealthFinding).where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
            HealthFinding.file_path.in_(paths[i : i + _BATCH_SIZE]),
            HealthFinding.biomarker_type.not_in(excluded_types()),
        )
        peers.extend((await session.execute(q)).scalars().all())
    known = {f.id for f in peers}
    # A finding outside the open set (resolved, acknowledged) is measured with it.
    peers.extend(f for f in findings if f.id not in known)
    reasons = dict(zip((f.id for f in peers), finding_priorities(peers), strict=True))
    return {f.id: reasons[f.id] for f in findings}


async def get_deduction_by_path(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: list[str] | None = None,
    status: str = "open",
) -> dict[str, float]:
    """``{file_path: summed health_impact}`` over the repo's findings.

    The pre-clamp deduction magnitude, aggregated in SQL rather than by
    hydrating every finding. Each stored ``health_impact`` is already the
    applied (capped) value, so the sum equals the breakdown endpoint's
    ``total_deduction``.

    Files with no findings are simply absent — a caller reading this as a
    ranking key should treat a miss as 0.0 (a clean file has no magnitude).
    """
    totals: dict[str, float] = {}
    base = select(
        HealthFinding.file_path,
        func.sum(HealthFinding.health_impact),
    ).where(
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == status,
    )
    # Batched like the other ``IN`` lookups here: SQLITE_MAX_VARIABLE_NUMBER is
    # 999 before SQLite 3.32 and a module-scoped caller expands without bound.
    chunks: list[list[str] | None] = (
        [file_paths[i : i + _BATCH_SIZE] for i in range(0, len(file_paths), _BATCH_SIZE)]
        if file_paths is not None
        else [None]
    )
    for chunk in chunks:
        q = base if chunk is None else base.where(HealthFinding.file_path.in_(chunk))
        for path, total in (await session.execute(q.group_by(HealthFinding.file_path))).all():
            totals[path] = float(total or 0.0)
    return totals
