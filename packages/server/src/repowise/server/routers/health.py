"""Health and metrics endpoints.

These endpoints are NOT protected by API key auth.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.sql import text

from fastapi import APIRouter, Request
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GenerationJob, Page
from repowise.server import __version__
from repowise.server.schemas import HealthResponse

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
