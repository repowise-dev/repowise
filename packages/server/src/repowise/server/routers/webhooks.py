"""/api/webhooks — GitHub and GitLab webhook handlers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository
from repowise.server.deps import auth_is_open, get_db_session
from repowise.server.schemas import WebhookResponse

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
# Note: no Depends(verify_api_key) here — webhook routes carry their own
# credential (HMAC signature / shared token) issued by the SCM provider.
# Secrets are read per-request (not cached at import) so env changes are
# picked up without a server restart.


def _normalize_scm_url(url: str) -> str:
    """Collapse clone / web URL variants to a comparable host/path key.

    Strips scheme, userinfo, ``.git`` suffix, trailing slashes, and lowercases
    the **whole** key (host and path). GitHub and GitLab resolve repo paths
    case-insensitively; the previous ``contains`` match was also case-insensitive
    under SQLite, so preserving path case would silently stop syncing when a
    hand-registered URL differed only in casing from the webhook's ``clone_url``.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    # git@host:path → host/path
    scp = re.match(r"^git@([^:]+):(.+)$", raw)
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        host = parsed.hostname or ""
        path = parsed.path or ""
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return f"{host}/{path}".rstrip("/").lower()


async def _find_repo_by_scm_url(session: AsyncSession, url: str) -> Repository | None:
    """Return the registered repo whose URL matches *url*, or None.

    Exact normalized match only — never a truncated ``contains`` prefix.
    """
    needle = _normalize_scm_url(url)
    if not needle:
        return None
    result = await session.execute(select(Repository))
    for repo in result.scalars().all():
        if _normalize_scm_url(repo.url) == needle:
            return repo
    return None


def _verify_github_signature(request: Request, body: bytes, signature_header: str) -> None:
    """Verify GitHub HMAC-SHA256 webhook signature.

    Reads REPOWISE_GITHUB_WEBHOOK_SECRET per-call so env changes (e.g.
    from dotenv or test fixtures) are always picked up.

    When the secret is not configured:
    - Local callers (loopback / no network exposure) are accepted — dev
      convenience, matching the posture of ``verify_api_key`` in deps.py.
    - Non-local callers are rejected with 403 (fail-closed).
    """
    secret = os.environ.get("REPOWISE_GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        if not auth_is_open(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook secret not configured. "
                    "Set REPOWISE_GITHUB_WEBHOOK_SECRET or restrict the server "
                    "to a loopback interface."
                ),
            )
        return  # local caller — dev mode

    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing signature prefix")

    expected = "sha256=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _verify_gitlab_token(request: Request, token_header: str) -> None:
    """Verify GitLab webhook token.

    Reads REPOWISE_GITLAB_WEBHOOK_TOKEN per-call so env changes are always
    picked up.

    When the token is not configured:
    - Local callers are accepted (dev convenience).
    - Non-local callers are rejected with 403 (fail-closed).
    """
    configured = os.environ.get("REPOWISE_GITLAB_WEBHOOK_TOKEN", "")
    if not configured:
        if not auth_is_open(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook token not configured. "
                    "Set REPOWISE_GITLAB_WEBHOOK_TOKEN or restrict the server "
                    "to a loopback interface."
                ),
            )
        return  # local caller — dev mode

    if not hmac.compare_digest(token_header, configured):
        raise HTTPException(status_code=401, detail="Invalid token")


def _launch_webhook_job(request: Request, job_id: str) -> None:
    """Launch a webhook-triggered job as a background task."""
    import asyncio

    from repowise.server.job_executor import execute_job

    task = asyncio.create_task(
        execute_job(job_id, request.app.state),
        name=f"webhook-job-{job_id}",
    )
    bg_tasks: set = request.app.state.background_tasks
    bg_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        bg_tasks.discard(t)

    task.add_done_callback(_on_done)


@router.post("/github", response_model=WebhookResponse)
async def github_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """Receive and process GitHub webhook events.

    Verifies HMAC-SHA256 signature, stores the event, and enqueues a sync
    job for push events on the default branch.
    """
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    _verify_github_signature(request, body, sig)

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

    # For push events: create a sync job
    if event_type == "push":
        ref = payload.get("ref", "")
        # Only sync pushes to the default branch
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/") :]
            repo = await _find_repo_by_scm_url(session, repo_url)
            if repo and branch == repo.default_branch:
                job = await crud.upsert_generation_job(
                    session,
                    repository_id=repo.id,
                    status="pending",
                    config={
                        "mode": "sync",
                        "trigger": "webhook",
                        "before": payload.get("before", ""),
                        "after": payload.get("after", ""),
                    },
                )
                await crud.mark_webhook_processed(session, event.id, job_id=job.id)
                await session.commit()
                _launch_webhook_job(request, job.id)

    return WebhookResponse(event_id=event.id)


@router.post("/gitlab", response_model=WebhookResponse)
async def gitlab_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """Receive and process GitLab webhook events.

    Verifies X-Gitlab-Token header, stores the event, and enqueues a sync
    job for push events on the default branch.
    """
    token = request.headers.get("X-Gitlab-Token", "")
    _verify_gitlab_token(request, token)

    body = await request.body()
    payload = json.loads(body)
    event_type = request.headers.get("X-Gitlab-Event", "unknown")

    event = await crud.store_webhook_event(
        session,
        provider="gitlab",
        event_type=event_type,
        payload=payload,
    )

    # For push events: create a sync job
    if event_type == "Push Hook":
        ref = payload.get("ref", "")
        if ref.startswith("refs/heads/"):
            branch = ref[len("refs/heads/") :]
            project_url = payload.get("project", {}).get("web_url", "")

            repo = await _find_repo_by_scm_url(session, project_url)
            if repo and branch == repo.default_branch:
                job = await crud.upsert_generation_job(
                    session,
                    repository_id=repo.id,
                    status="pending",
                    config={
                        "mode": "sync",
                        "trigger": "webhook",
                        "before": payload.get("before", ""),
                        "after": payload.get("after", ""),
                    },
                )
                await crud.mark_webhook_processed(session, event.id, job_id=job.id)
                await session.commit()
                _launch_webhook_job(request, job.id)

    return WebhookResponse(event_id=event.id)
