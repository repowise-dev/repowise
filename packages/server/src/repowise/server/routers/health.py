"""Health and metrics endpoints.

These endpoints are NOT protected by API key auth.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from fastapi import APIRouter, Depends, Request
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
    vector_count: int | None = result.get("vector_count")
    graph_nodes: int | None = result.get("graph_nodes")
    drift: float | None = result.get("drift")

    # Normalise vector_count: -1 means unsupported (return None)
    if vector_count == -1:
        vector_count = None

    # Drift percentage (0–100)
    drift_pct: float | None = round(drift * 100, 2) if drift is not None else None

    if drift_pct is None:
        status = "ok"
    elif drift_pct <= 1.0:
        status = "ok"
    elif drift_pct <= 5.0:
        status = "warning"
    else:
        status = "critical"

    return CoordinatorHealthResponse(
        sql_pages=sql_pages,
        vector_count=vector_count,
        graph_nodes=graph_nodes,
        drift_pct=drift_pct,
        status=status,
    )


# Merge repo-scoped routes into the main router so they are registered together.
router.include_router(_repo_health_router)
