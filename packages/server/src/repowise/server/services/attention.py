"""Fetch side of the Overview attention list.

The ranking lives in :mod:`repowise.core.analysis.attention`; this module reads
each store into a :class:`SourceResult` and hands them to the fold.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.attention import (
    PER_SOURCE_CAP,
    SEVERITY_RANK,
    AttentionSource,
    AttentionView,
    DecisionAttentionInput,
    SourceResult,
    compose_attention,
    decision_source,
    severity_of_drift,
    severity_of_file_score,
    silo_source,
)
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DocDriftFinding,
    HealthFileMetric,
    HealthFinding,
    RefactoringSuggestion,
    SecurityFinding,
)

__all__ = ["PER_SOURCE_CAP", "build_attention"]


def _test_paths(repo_id: str):
    """Sub-select of this repository's test files.

    Production scope, which the Code Health page already offers as a control
    and this list was missing. A test file cannot be the most urgent thing in
    a repository: it has no users, a defect in it fails a build rather than a
    customer, and `tests/` is usually the largest directory in the tree, so
    without this the top of the list fills with test files and the section
    stops being about the product.

    Reads the `is_test` column the health pass already stamps, rather than
    re-deriving it from the path here and disagreeing with the page that shows
    the same files.
    """
    return select(HealthFileMetric.file_path).where(
        HealthFileMetric.repository_id == repo_id,
        HealthFileMetric.is_test.is_(True),
    )


async def _health_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Open code-health findings, aggregated to the file, worst file first.

    A file rather than a finding, because `health_impact` saturates. The cap is
    2.50 and several criticals sit exactly on it, so ranking findings against
    each other decides the top of the list on a tie-break rather than on the
    metric — it surfaced one `nested_complexity` finding ahead of a file
    carrying thirty-eight findings and twelve points of deduction. Summed
    impact is the file's whole score (a file scores `10 - sum`), so this ranks
    on the number the product already stands behind.

    This is also what puts the test-quality work on the page: mock saturation,
    assertion-free tests, untested hotspots and coverage gaps are
    `biomarker_type` values inside `health_findings` rather than stores of
    their own, so they arrive here without a special case.

    `total` stays a count of findings, not of files. The row says how much the
    area holds; the lead says where to start.
    """
    scoped = (
        HealthFinding.repository_id == repo_id,
        HealthFinding.status == "open",
        HealthFinding.file_path.not_in(_test_paths(repo_id)),
    )
    rows = (
        await session.execute(
            select(
                HealthFinding.file_path,
                func.sum(HealthFinding.health_impact).label("impact"),
                func.count(HealthFinding.id).label("findings"),
            )
            .where(*scoped)
            .group_by(HealthFinding.file_path)
            .order_by(func.sum(HealthFinding.health_impact).desc())
            .limit(PER_SOURCE_CAP)
        )
    ).all()
    total = await session.scalar(select(func.count(HealthFinding.id)).where(*scoped)) or 0
    if not rows:
        return [], int(total), ""

    # The biomarker to name each file by: its own heaviest one. Bounded to the
    # handful of files above, so this is a keyed read rather than a scan.
    paths = [r.file_path for r in rows]
    lead_biomarker: dict[str, str] = {}
    for path, biomarker in (
        await session.execute(
            select(HealthFinding.file_path, HealthFinding.biomarker_type)
            .where(*scoped, HealthFinding.file_path.in_(paths))
            .order_by(HealthFinding.health_impact.desc())
        )
    ).all():
        lead_biomarker.setdefault(path, biomarker)

    items = [
        {
            "id": f"health-{row.file_path}",
            "type": "health_finding",
            "title": row.file_path,
            "description": (
                f"{row.findings} finding{'' if row.findings == 1 else 's'}"
                f" · {float(row.impact or 0.0):.1f} deducted"
            ),
            "severity": severity_of_file_score(
                max(1.0, 10.0 - float(row.impact or 0.0))
            ),
            "target_id": row.file_path,
            # The UI resolves this through the biomarker glossary it owns, so
            # this module holds no second copy of that vocabulary.
            "subtype": lead_biomarker.get(row.file_path),
            "weight": float(row.impact or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _security_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Security findings, high severity first.

    ``SecurityFinding`` has no status column: rows are replaced wholesale by
    each scan, so every stored row is open by construction and there is nothing
    to filter on. It also only ever writes ``high`` or ``low`` — the router's
    docstring mentions a ``med`` that the scanner never emits — so the two
    middle bands of the ladder are simply unused here.
    """
    rows = (
        (
            await session.execute(
                select(SecurityFinding)
                .where(SecurityFinding.repository_id == repo_id)
                .order_by(
                    # `high` before anything else, then newest. Expressed as a
                    # sort key rather than two queries.
                    (SecurityFinding.severity != "high"),
                    SecurityFinding.detected_at.desc(),
                )
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(SecurityFinding.id)).where(
                SecurityFinding.repository_id == repo_id
            )
        )
        or 0
    )
    # A secret in the working tree and one only reachable through git history
    # are different jobs — one is a deletion, the other a rotation and a
    # rewrite — and 245 findings reads as an emergency until the split says
    # most of them are historical.
    in_tree = (
        await session.scalar(
            select(func.count(SecurityFinding.id)).where(
                SecurityFinding.repository_id == repo_id,
                SecurityFinding.commit_sha == "",
            )
        )
        or 0
    )
    items = [
        {
            "id": f"security-{row.id}",
            "type": "security_finding",
            "title": row.file_path,
            "description": (
                f"{row.kind}{f' in commit {row.commit_sha[:7]}' if row.commit_sha else ''}"
            ),
            "severity": row.severity if row.severity in SEVERITY_RANK else "medium",
            "target_id": row.file_path,
            "subtype": row.kind,
            # Nothing separates two `high` secrets but recency, and a secret
            # still sitting in the tree outranks one only git remembers.
            "weight": 1.0 if not row.commit_sha else 0.0,
        }
        for row in rows
    ]
    return items, int(total), (
        f"{in_tree} in the working tree, {int(total) - in_tree} in git history"
        if total
        else ""
    )


async def _drift_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Documentation drift, most confident first.

    ``file_path`` on this row is the *document* making the false claim, not the
    code it is wrong about, which is why the row links to the drift tab rather
    than to a file page.
    """
    rows = (
        (
            await session.execute(
                select(DocDriftFinding)
                .where(DocDriftFinding.repository_id == repo_id)
                .order_by(DocDriftFinding.confidence.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(DocDriftFinding.id)).where(
                DocDriftFinding.repository_id == repo_id
            )
        )
        or 0
    )
    items = [
        {
            "id": f"drift-{row.id}",
            "type": "doc_drift",
            "title": row.file_path,
            "description": row.reason or f"{row.kind} no longer matches the tree",
            "severity": severity_of_drift(row.confidence),
            "target_id": row.file_path,
            "subtype": row.kind,
            "weight": float(row.confidence or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _refactoring_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Open refactoring plans, biggest recovered health first.

    The one store on this list that holds *work* rather than observations: a
    row is a plan with an impact estimate and an effort bucket, so it is the
    only source where "what should I do about it" is already answered.

    Severity is derived from the impact it recovers, because the store has no
    severity column and an opportunity is not a defect. It is capped at
    ``medium``: a thing you could improve never outranks a thing that is
    wrong.
    """
    rows = (
        (
            await session.execute(
                select(RefactoringSuggestion)
                .where(
                    RefactoringSuggestion.repository_id == repo_id,
                    RefactoringSuggestion.status == "open",
                    RefactoringSuggestion.file_path.not_in(_test_paths(repo_id)),
                )
                .order_by(RefactoringSuggestion.impact_delta.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(RefactoringSuggestion.id)).where(
                RefactoringSuggestion.repository_id == repo_id,
                RefactoringSuggestion.status == "open",
                RefactoringSuggestion.file_path.not_in(_test_paths(repo_id)),
            )
        )
        or 0
    )
    items = [
        {
            "id": f"refactor-{row.id}",
            "type": "refactoring",
            "title": row.target_symbol or row.file_path,
            "description": (
                f"+{row.impact_delta:.1f} health"
                f"{f', {row.effort_bucket} effort' if row.effort_bucket else ''}"
                f" · {row.file_path}"
            ),
            "severity": "medium" if (row.impact_delta or 0.0) >= 1.0 else "low",
            "target_id": row.file_path,
            "subtype": row.refactoring_type,
            "weight": float(row.impact_delta or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _dead_code_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Safe-to-delete dead code, most confident first.

    Stays in the bottom band whatever its confidence. Confidence here measures
    how sure the graph is that nothing reaches the symbol, not how much it
    costs to leave it there, and a high-confidence unused export is still the
    least urgent thing this list can show.
    """
    rows = (
        (
            await session.execute(
                select(DeadCodeFinding)
                .where(
                    DeadCodeFinding.repository_id == repo_id,
                    DeadCodeFinding.status == "open",
                    DeadCodeFinding.safe_to_delete.is_(True),
                    DeadCodeFinding.file_path.not_in(_test_paths(repo_id)),
                )
                .order_by(DeadCodeFinding.confidence.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(DeadCodeFinding.id)).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.status == "open",
                DeadCodeFinding.safe_to_delete.is_(True),
                DeadCodeFinding.file_path.not_in(_test_paths(repo_id)),
            )
        )
        or 0
    )
    items = [
        {
            "id": f"dead-{row.id}",
            "type": "dead_code",
            "title": row.symbol_name or row.file_path,
            "description": f"Unreachable {row.symbol_kind or 'symbol'} ({row.lines} lines)",
            "severity": "low",
            "target_id": row.file_path,
            "subtype": row.kind,
            "weight": float(row.confidence or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


def _decision_source(decision_health: dict) -> SourceResult:
    """Adapt ``get_decision_health_summary`` rows to the fold's plain records."""

    def lane(key: str) -> list[DecisionAttentionInput]:
        return [
            DecisionAttentionInput(id=d.id, title=d.title)
            for d in decision_health.get(key, []) or []
        ]

    return decision_source(
        lane("stale_decisions"),
        lane("proposed_awaiting_review"),
        decision_health.get("ungoverned_hotspots", []) or [],
    )


_Fetcher = Callable[[AsyncSession, str], Awaitable[tuple[list[dict], int, str]]]
_FETCHERS: tuple[tuple[AttentionSource, _Fetcher], ...] = (
    ("health_finding", _health_items),
    ("security_finding", _security_items),
    ("refactoring", _refactoring_items),
    ("doc_drift", _drift_items),
    ("dead_code", _dead_code_items),
)


async def build_attention(
    session: AsyncSession,
    repo_id: str,
    *,
    decision_health: dict,
    knowledge_silos: list[dict],
) -> AttentionView:
    """Every attention source, merged and ranked worst-first.

    Returns ``{"items": [...], "total": int, "by_source": {type: count},
    "areas": [...]}``; see :func:`compose_attention`.

    ``decision_health`` and ``knowledge_silos`` are passed in rather than
    fetched: the Overview route already has both in hand for other parts of its
    payload, and re-deriving them here would be two more scans of the widest
    table in the schema.

    A source that raises is dropped with its count, not allowed to fail the
    page. Overview is an aggregate over a dozen stores and one absent table (an
    index built before a detector shipped) should cost the row rather than the
    whole page.
    """
    sources: dict[AttentionSource, SourceResult] = {}
    for key, fetch in _FETCHERS:
        try:
            items, total, detail = await fetch(session, repo_id)
        except Exception:
            continue
        sources[key] = SourceResult(tuple(items), total, detail)
    sources["decisions"] = _decision_source(decision_health)
    sources["knowledge_silo"] = silo_source(knowledge_silos)
    return compose_attention(sources)
