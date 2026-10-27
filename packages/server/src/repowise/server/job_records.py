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


#: Shown when a provider is configured but could not be loaded. Fixed text:
#: the load error itself can be long and is in the server log.
DOCS_SKIPPED_MESSAGE = (
    "A model provider is configured but could not be loaded, so documentation "
    "was not updated. Check it in Settings."
)


def _provider_issue(pages: list, *, docs_skipped: bool = False) -> dict | None:
    """What the UI shows when a completed run still went wrong with the provider:
    the hint its failed pages share, else that docs were skipped."""
    from repowise.core.generation.models import dominant_stub_fallback_hint

    hint = dominant_stub_fallback_hint(pages)
    if hint is not None:
        return hint
    if docs_skipped:
        return {"kind": "docs_skipped", "message": DOCS_SKIPPED_MESSAGE}
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


def _failure_fields(exc: BaseException) -> dict[str, Any]:
    """The failed job's ``error_message`` and ``provider_issue``: the hint for a
    recognised provider failure (the raw error is already logged), else the
    error's own text."""
    from repowise.core.providers.llm.error_hints import describe_provider_error

    hint = describe_provider_error(exc)
    if hint is None:
        return {"error_message": str(exc)[:500], "provider_issue": None}
    return {"error_message": hint.message, "provider_issue": hint.as_dict()}


async def _record_provider_issue(session: Any, job_id: str, provider_issue: dict) -> None:
    """Add ``provider_issue`` to the job's config, beside its run summary."""
    job = await get_generation_job(session, job_id)
    if job is None:
        return
    config = json.loads(job.config_json or "{}")
    config["provider_issue"] = provider_issue
    job.config_json = json.dumps(config)


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
                await _record_provider_issue(session, job_id, provider_issue)
            await update_job_status(
                session,
                job_id,
                status,
                error_message=error_message,
            )
    except Exception:
        logger.exception("job_status_update_failed", job_id=job_id)
