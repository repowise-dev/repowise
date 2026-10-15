"""Terminal writes to a GenerationJob row, shared by every kind of server job."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from repowise.core.persistence.crud import (
    get_generation_job,
    update_job_status,
)
from repowise.core.persistence.database import get_session

if TYPE_CHECKING:
    from repowise.server.job_executor import JobProgressCallback

logger = structlog.get_logger(__name__)


def _provider_issue(pages: list, docs_skip_reason: str | None = None) -> dict | None:
    """What the UI shows when a completed run still went wrong with the provider:
    the hint its failed pages share, else why docs were skipped."""
    from repowise.core.generation.models import dominant_stub_fallback_hint

    hint = dominant_stub_fallback_hint(pages)
    if hint is not None:
        return hint
    if docs_skip_reason:
        return {"kind": "docs_skipped", "message": docs_skip_reason[:500]}
    return None


async def _complete_job_row(
    session: Any,
    job_id: str,
    config: dict,
    summary: dict,
    *,
    completed_pages: int,
    failed_pages: int,
    total_pages: int,
) -> None:
    """Store the run summary in the job's config (the frontend displays it)
    and mark the job completed. Shared by index and scoped-generation jobs."""
    job = await get_generation_job(session, job_id)
    final_config = dict(config)
    final_config.update(summary)
    if job is not None:
        job.config_json = json.dumps(final_config)
    await update_job_status(
        session,
        job_id,
        "completed",
        completed_pages=completed_pages,
        failed_pages=failed_pages,
        total_pages=total_pages,
    )


async def _finalize_job_status(
    app_state: Any,
    session_factory: Any,
    progress: JobProgressCallback | None,
    job_id: str,
    *,
    status: str,
    error_message: str,
    provider_issue: dict | None = None,
) -> None:
    """Best-effort terminal status write shared by the failure and cancel paths.

    Drains in-flight progress updates first so a late fire-and-forget write
    can't overwrite the terminal status with "running". Falls back to the
    app-level session factory when the job's own factory was never resolved,
    so the row never stays stuck in pending.
    """
    if progress is not None:
        try:
            await progress.drain_and_stop()
        except Exception:
            logger.debug("drain_failed_on_error_path", job_id=job_id, exc_info=True)
    recovery_factory = session_factory or getattr(app_state, "session_factory", None)
    if recovery_factory is None:
        logger.error("job_status_update_skipped_no_session", job_id=job_id)
        return
    try:
        async with get_session(recovery_factory) as session:
            if provider_issue is not None:
                job = await get_generation_job(session, job_id)
                if job is not None:
                    config = json.loads(job.config_json or "{}")
                    config["provider_issue"] = provider_issue
                    job.config_json = json.dumps(config)
            await update_job_status(
                session,
                job_id,
                status,
                error_message=error_message,
            )
    except Exception:
        logger.exception("job_status_update_failed", job_id=job_id)
