"""/api/webhooks — GitHub and GitLab webhook handlers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Request
from repowise.core.persistence import crud
from repowise.core.persistence.models import GenerationJob
from repowise.server.deps import get_db_session
from repowise.server.job_executor import execute_job
from repowise.server.schemas import WebhookResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_GITHUB_SECRET = os.environ.get("REPOWISE_GITHUB_WEBHOOK_SECRET", "")
_GITLAB_TOKEN = os.environ.get("REPOWISE_GITLAB_WEBHOOK_TOKEN", "")


def _verify_github_signature(body: bytes, signature_header: str) -> None:
    """Verify GitHub HMAC-SHA256 webhook signature."""
    if not _GITHUB_SECRET:
        return  # No secret configured — skip verification (dev mode)

    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing signature prefix")

    expected = hmac.new(
        _GITHUB_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(f"sha256={expected}", signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _verify_gitlab_token(token_header: str) -> None:
    """Verify GitLab webhook token."""
    if not _GITLAB_TOKEN:
        return  # No token configured — skip verification (dev mode)

    if not hmac.compare_digest(token_header, _GITLAB_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid token")


def _launch_job_task(request: Request, job_id: str) -> None:
    """Launch a background job task from a webhook handler."""
    task = asyncio.create_task(execute_job(job_id, request.app.state), name=f"job-{job_id}")
    bg_tasks: set[asyncio.Task] = request.app.state.background_tasks
    bg_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        bg_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("background_job_failed", exc_info=t.exception())

    task.add_done_callback(_on_done)


@router.post("/github", response_model=WebhookResponse)
async def github_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> WebhookResponse:
    """Receive and process GitHub webhook events.

    Verifies HMAC-SHA256 signature, stores the event, and enqueues a sync
    job for push events on the default branch.
    """
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    _verify_github_signature(body, sig)

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    payload = json.loads(body)

    # Try to find the repository by matching the clone URL
    repo_url = ""
    if "repository" in payload:
        repo_url = payload["repository"].get("clone_url", "")
        if not repo_url:
            repo_url = payload["repository"].get("html_url", "")

    # Store the webhook event
    event = await crud.store_webhook_event(
        session,
        provider="github",
        event_type=event_type,
        payload=payload,
        delivery_id=delivery_id,
    )

    # For push events: create a sync job and launch it
    if event_type == "push":
        ref = payload.get("ref", "")
        # Only sync pushes to the default branch
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/"):]
            # Find matching repo by URL
            from sqlalchemy import select

            from repowise.core.persistence.models import Repository

            result = await session.execute(
                select(Repository).where(Repository.url.contains(repo_url[:50]))
            )
            repo = result.scalar_one_or_none()
            if repo and branch == repo.default_branch:
                # Prevent concurrent pipeline runs on the same repo
                active = await session.execute(
                    select(GenerationJob.id)
                    .where(GenerationJob.repository_id == repo.id)
                    .where(GenerationJob.status.in_(["pending", "running"]))
                    .limit(1)
                )
                if active.scalar_one_or_none() is not None:
                    logger.info("webhook_skip_sync_already_running", extra={"repo": repo.name})
                else:
                    job = await crud.upsert_generation_job(
                        session,
                        repository_id=repo.id,
                        status="pending",
                        config={
                            "mode": "incremental",
                            "trigger": "webhook",
                            "before": payload.get("before", ""),
                            "after": payload.get("after", ""),
                        },
                    )
                    await crud.mark_webhook_processed(session, event.id, job_id=job.id)
                    await session.commit()
                    _launch_job_task(request, job.id)

    return WebhookResponse(event_id=event.id)


@router.post("/gitlab", response_model=WebhookResponse)
async def gitlab_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> WebhookResponse:
    """Receive and process GitLab webhook events.

    Verifies X-Gitlab-Token header, stores the event, and enqueues a sync
    job for push events on the default branch.
    """
    token = request.headers.get("X-Gitlab-Token", "")
    _verify_gitlab_token(token)

    body = await request.body()
    payload = json.loads(body)
    event_type = request.headers.get("X-Gitlab-Event", "unknown")

    event = await crud.store_webhook_event(
        session,
        provider="gitlab",
        event_type=event_type,
        payload=payload,
    )

    # For push events: create a sync job and launch it
    if event_type == "Push Hook":
        ref = payload.get("ref", "")
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/"):]
            project_url = payload.get("project", {}).get("web_url", "")

            from sqlalchemy import select

            from repowise.core.persistence.models import Repository

            result = await session.execute(
                select(Repository).where(Repository.url.contains(project_url[:50]))
            )
            repo = result.scalar_one_or_none()
            if repo and branch == repo.default_branch:
                active = await session.execute(
                    select(GenerationJob.id)
                    .where(GenerationJob.repository_id == repo.id)
                    .where(GenerationJob.status.in_(["pending", "running"]))
                    .limit(1)
                )
                if active.scalar_one_or_none() is not None:
                    logger.info("webhook_skip_sync_already_running", extra={"repo": repo.name})
                else:
                    job = await crud.upsert_generation_job(
                        session,
                        repository_id=repo.id,
                        status="pending",
                        config={
                            "mode": "incremental",
                            "trigger": "webhook",
                            "before": payload.get("before", ""),
                            "after": payload.get("after", ""),
                        },
                    )
                    await crud.mark_webhook_processed(session, event.id, job_id=job.id)
                    await session.commit()
                    _launch_job_task(request, job.id)

    return WebhookResponse(event_id=event.id)
