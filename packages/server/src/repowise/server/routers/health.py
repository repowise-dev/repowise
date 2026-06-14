"""Health and metrics endpoints.

These endpoints are NOT protected by API key auth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from repowise.core.persistence.coordinator import AtomicStorageCoordinator
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob, Page
from repowise.server import __version__
from repowise.server.deps import get_db_session, get_vector_store
from repowise.server.schemas import CoordinatorHealthResponse, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Liveness and readiness check."""
    db_status = "ok"
    try:
        factory = request.app.state.session_factory
        async with get_session(factory) as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    status = "healthy" if db_status == "ok" else "degraded"
    return HealthResponse(status=status, db=db_status, version=__version__)


@router.get("/metrics")
async def metrics(request: Request) -> str:
    """Prometheus-compatible metrics endpoint."""
    factory = request.app.state.session_factory
    lines: list[str] = []

    try:
        async with get_session(factory) as session:
            # Page counts by freshness
            for status_val in ("fresh", "stale", "expired"):
                result = await session.execute(
                    select(func.count())
                    .select_from(Page)
                    .where(Page.freshness_status == status_val)
                )
                count = result.scalar() or 0
                lines.append(f'repowise_pages_total{{status="{status_val}"}} {count}')

            # Job counts by status
            for job_status in ("pending", "running", "completed", "failed"):
                result = await session.execute(
                    select(func.count())
                    .select_from(GenerationJob)
                    .where(GenerationJob.status == job_status)
                )
                count = result.scalar() or 0
                lines.append(f'repowise_jobs_total{{status="{job_status}"}} {count}')

            # Aggregate token usage from completed jobs
            for token_type, col in [
                ("input", func.sum(Page.input_tokens)),
                ("output", func.sum(Page.output_tokens)),
            ]:
                result = await session.execute(select(col))
                total = result.scalar() or 0
                lines.append(f'repowise_tokens_total{{type="{token_type}"}} {total}')
    except Exception:
        lines.append("repowise_health 0")

    from starlette.responses import Response

    return Response(content="\n".join(lines) + "\n", media_type="text/plain")


_repo_health_router = APIRouter(prefix="/api/repos", tags=["health"])


@_repo_health_router.get("/{repo_id}/health/coordinator", response_model=CoordinatorHealthResponse)
async def coordinator_health(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    vector_store=Depends(get_vector_store),  # noqa: B008
) -> CoordinatorHealthResponse:
    """Return coordinator drift health for a repository."""
    coord = AtomicStorageCoordinator(session, graph_builder=None, vector_store=vector_store)
    result = await coord.health_check()

    sql_pages: int | None = result.get("sql_pages")
    sql_decisions: int | None = result.get("sql_decisions")
    vector_count: int | None = result.get("vector_count")
    vector_page_count: int | None = result.get("vector_page_count")
    vector_decision_count: int | None = result.get("vector_decision_count")
    graph_nodes: int | None = result.get("graph_nodes")
    page_drift: float | None = result.get("page_drift")
    decision_drift: float | None = result.get("decision_drift")

    # Normalise vector counts: -1 means the store can't be counted (return None).
    if vector_count == -1:
        vector_count = None

    # Drift percentages (0-100), compared like-with-like per population.
    page_drift_pct = round(page_drift * 100, 2) if page_drift is not None else None
    decision_drift_pct = round(decision_drift * 100, 2) if decision_drift is not None else None

    page_status = _classify_drift(page_drift_pct)
    decision_status = _classify_drift(decision_drift_pct)
    status = _worst_status(page_status, decision_status)
    detail = _build_detail(
        page_drift_pct=page_drift_pct,
        decision_drift_pct=decision_drift_pct,
        sql_pages=sql_pages,
        vector_page_count=vector_page_count,
        sql_decisions=sql_decisions,
        vector_decision_count=vector_decision_count,
    )

    return CoordinatorHealthResponse(
        sql_pages=sql_pages,
        sql_decisions=sql_decisions,
        vector_count=vector_count,
        vector_page_count=vector_page_count,
        vector_decision_count=vector_decision_count,
        graph_nodes=graph_nodes,
        drift_pct=page_drift_pct,  # alias of page_drift_pct (backwards compat)
        page_drift_pct=page_drift_pct,
        decision_drift_pct=decision_drift_pct,
        status=status,
        detail=detail,
    )


_STATUS_RANK = {"ok": 0, "warning": 1, "critical": 2}


def _classify_drift(drift_pct: float | None) -> str:
    """Map a drift percentage to an ok/warning/critical status."""
    if drift_pct is None:
        return "ok"  # population not comparable (e.g. store can't enumerate ids)
    if drift_pct <= 1.0:
        return "ok"
    if drift_pct <= 5.0:
        return "warning"
    return "critical"


def _worst_status(*statuses: str) -> str:
    """Return the most severe of the given statuses."""
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))


def _build_detail(
    *,
    page_drift_pct: float | None,
    decision_drift_pct: float | None,
    sql_pages: int | None,
    vector_page_count: int | None,
    sql_decisions: int | None,
    vector_decision_count: int | None,
) -> str | None:
    """Compose a human-readable explanation, reporting each population separately."""
    parts: list[str] = []

    # Pages
    if sql_pages and vector_page_count == 0:
        parts.append(f"No page vectors for {sql_pages} wiki pages; run a sync to embed pages.")
    elif page_drift_pct is not None and page_drift_pct > 1.0:
        parts.append(
            f"Page drift {page_drift_pct:.1f}% "
            f"({sql_pages} pages vs {vector_page_count} page vectors)."
        )

    # Decisions
    if sql_decisions and vector_decision_count == 0:
        parts.append(
            f"No decision vectors for {sql_decisions} decision records; "
            f"run a sync to embed decisions."
        )
    elif decision_drift_pct is not None and decision_drift_pct > 1.0:
        parts.append(
            f"Decision drift {decision_drift_pct:.1f}% "
            f"({sql_decisions} decisions vs {vector_decision_count} decision vectors)."
        )

    if not parts:
        return "All populations in sync." if page_drift_pct is not None else None
    return " ".join(parts)


# Merge repo-scoped routes into the main router so they are registered together.
router.include_router(_repo_health_router)
