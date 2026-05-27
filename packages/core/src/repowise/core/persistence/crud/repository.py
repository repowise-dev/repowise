"""CRUD operations for the repository domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    GenerationJob,
    Page,
    Repository,
    WebhookEvent,
    _new_uuid,
    _now_utc,
)
from ._shared import _VALID_JOB_STATUSES

# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------


async def upsert_repository(
    session: AsyncSession,
    *,
    name: str,
    local_path: str,
    url: str = "",
    default_branch: str = "main",
    settings: dict | None = None,
) -> Repository:
    """Create or update a repository record.

    Lookup is by ``local_path`` (the canonical key for local repositories).
    """
    result = await session.execute(select(Repository).where(Repository.local_path == local_path))
    repo = result.scalar_one_or_none()

    if repo is None:
        repo = Repository(
            id=_new_uuid(),
            name=name,
            local_path=local_path,
            url=url,
            default_branch=default_branch,
            settings_json=json.dumps(settings or {}),
        )
        session.add(repo)
    else:
        repo.name = name
        repo.url = url
        repo.default_branch = default_branch
        if settings is not None:
            repo.settings_json = json.dumps(settings)
        repo.updated_at = _now_utc()

    await session.flush()
    return repo


async def get_repository(session: AsyncSession, repo_id: str) -> Repository | None:
    """Return a Repository by primary key, or None."""
    return await session.get(Repository, repo_id)


async def get_repository_by_path(session: AsyncSession, local_path: str) -> Repository | None:
    """Return a Repository by local_path, or None."""
    result = await session.execute(select(Repository).where(Repository.local_path == local_path))
    return result.scalar_one_or_none()


async def delete_repository(session: AsyncSession, repo_id: str) -> bool:
    """Delete a repository and all cascaded children.

    Returns True if deleted, False if not found.

    NOTE: The caller should clean up the FTS index *before* calling this,
    since the CASCADE will delete Page rows and we lose the page IDs.
    """
    repo = await session.get(Repository, repo_id)
    if repo is None:
        return False
    await session.delete(repo)
    await session.flush()
    return True


async def list_page_ids(session: AsyncSession, repository_id: str) -> list[str]:
    """Return all page IDs for a repository (lightweight, ID-only query)."""
    result = await session.execute(select(Page.id).where(Page.repository_id == repository_id))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GenerationJob CRUD
# ---------------------------------------------------------------------------


async def upsert_generation_job(
    session: AsyncSession,
    *,
    repository_id: str,
    status: str = "pending",
    provider_name: str = "",
    model_name: str = "",
    total_pages: int = 0,
    config: dict | None = None,
    job_id: str | None = None,
) -> GenerationJob:
    """Insert a new GenerationJob (jobs are append-only)."""
    job = GenerationJob(
        id=job_id or _new_uuid(),
        repository_id=repository_id,
        status=status,
        provider_name=provider_name,
        model_name=model_name,
        total_pages=total_pages,
        config_json=json.dumps(config or {}),
    )
    session.add(job)
    await session.flush()
    return job


async def get_generation_job(session: AsyncSession, job_id: str) -> GenerationJob | None:
    """Return a GenerationJob by primary key, or None."""
    return await session.get(GenerationJob, job_id)


async def update_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    *,
    completed_pages: int | None = None,
    failed_pages: int | None = None,
    current_level: int | None = None,
    total_pages: int | None = None,
    error_message: str | None = None,
) -> GenerationJob:
    """Update the mutable fields of a GenerationJob.

    Raises:
        ValueError: If *status* is not a recognised value.
        LookupError: If *job_id* does not exist.
    """
    if status not in _VALID_JOB_STATUSES:
        raise ValueError(
            f"Unknown job status {status!r}. Valid values: {sorted(_VALID_JOB_STATUSES)}"
        )

    job = await session.get(GenerationJob, job_id)
    if job is None:
        raise LookupError(f"No GenerationJob with id={job_id!r}")

    job.status = status
    job.updated_at = _now_utc()

    if completed_pages is not None:
        job.completed_pages = completed_pages
    if failed_pages is not None:
        job.failed_pages = failed_pages
    if current_level is not None:
        job.current_level = current_level
    if total_pages is not None:
        job.total_pages = total_pages
    if error_message is not None:
        job.error_message = error_message

    if status == "running" and job.started_at is None:
        job.started_at = _now_utc()
    if status in ("completed", "failed"):
        job.finished_at = _now_utc()

    await session.flush()
    return job


# ---------------------------------------------------------------------------
# WebhookEvent CRUD
# ---------------------------------------------------------------------------


async def store_webhook_event(
    session: AsyncSession,
    *,
    provider: str,
    event_type: str,
    payload: dict,
    repository_id: str | None = None,
    delivery_id: str = "",
) -> WebhookEvent:
    """Append a new WebhookEvent record."""
    event = WebhookEvent(
        id=_new_uuid(),
        repository_id=repository_id,
        provider=provider,
        event_type=event_type,
        delivery_id=delivery_id,
        payload_json=json.dumps(payload),
        processed=False,
    )
    session.add(event)
    await session.flush()
    return event


async def mark_webhook_processed(
    session: AsyncSession, event_id: str, *, job_id: str | None = None
) -> None:
    """Mark a WebhookEvent as processed and optionally link it to a job."""
    event = await session.get(WebhookEvent, event_id)
    if event is None:
        raise LookupError(f"No WebhookEvent with id={event_id!r}")
    event.processed = True
    if job_id is not None:
        event.job_id = job_id
    await session.flush()
