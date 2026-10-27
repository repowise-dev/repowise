"""/api/webhooks — GitHub, GitLab and Gitea/Forgejo webhook handlers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.forges import canonical_key
from repowise.core.persistence import crud
from repowise.core.persistence.models import Repository, WebhookEvent
from repowise.server.auth import LOCAL_PRINCIPAL, Principal, get_authenticator, get_authorizer
from repowise.server.deps import auth_is_open, get_db_session
from repowise.server.schemas import WebhookResponse

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
# Note: no Depends(verify_api_key) here. A forge delivery carries its own
# credential (HMAC signature / shared token), never a bearer, so the rule is:
# - secret configured: the verified signature is the principal (all repos);
# - no secret, open local server: the local principal, as before;
# - no secret otherwise: a valid bearer (API key or scoped token) is accepted,
#   and without one the 403 is unchanged.
# The principal must have the matched repo in scope before a sync job is
# created. Secrets are read per-request (not cached at import) so env changes
# are picked up without a server restart.

_GITHUB_PRINCIPAL = Principal(id="webhook:github", kind="webhook")
_GITLAB_PRINCIPAL = Principal(id="webhook:gitlab", kind="webhook")
# One principal for Gitea and Forgejo: they share a secret and an endpoint, so
# they are one credential here even though the event records which sent it.
_GITEA_PRINCIPAL = Principal(id="webhook:gitea", kind="webhook")


async def _unsigned_principal(request: Request) -> Principal | None:
    """The principal for a delivery when no forge secret is configured."""
    if auth_is_open(request):
        return LOCAL_PRINCIPAL  # local caller, dev mode
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return await get_authenticator().verify_token(auth[7:])
    return None


def _require_repo_in_scope(principal: Principal, repo: Repository) -> None:
    if not get_authorizer().can_access_repo(principal, (repo.id, repo.name)):
        raise HTTPException(status_code=403, detail="Repository is outside this credential's scope")


def _normalize_scm_url(url: str) -> str:
    """Collapse clone / web URL variants to a comparable host/path key.

    ``canonical_key`` lowercases the **whole** key (host and path): GitHub and
    GitLab resolve repo paths case-insensitively, and the previous ``contains``
    match was also case-insensitive under SQLite, so preserving path case would
    silently stop syncing when a hand-registered URL differed only in casing
    from the webhook's ``clone_url``. ``""`` when the URL names no remote.
    """
    return canonical_key(url) or ""


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


async def _verify_github_signature(
    request: Request, body: bytes, signature_header: str
) -> Principal:
    """Verify GitHub HMAC-SHA256 webhook signature.

    Reads REPOWISE_GITHUB_WEBHOOK_SECRET per-call so env changes (e.g.
    from dotenv or test fixtures) are always picked up.

    When the secret is not configured:
    - Local callers (loopback / no network exposure) are accepted — dev
      convenience, matching the posture of ``verify_api_key`` in deps.py.
    - A caller with a valid bearer credential is accepted as that principal.
    - Anyone else is rejected with 403 (fail-closed).
    """
    secret = os.environ.get("REPOWISE_GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        principal = await _unsigned_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook secret not configured. "
                    "Set REPOWISE_GITHUB_WEBHOOK_SECRET or restrict the server "
                    "to a loopback interface."
                ),
            )
        return principal

    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing signature prefix")

    expected = "sha256=" + hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return _GITHUB_PRINCIPAL


async def _verify_gitlab_token(request: Request, token_header: str) -> Principal:
    """Verify GitLab webhook token.

    Reads REPOWISE_GITLAB_WEBHOOK_TOKEN per-call so env changes are always
    picked up.

    When the token is not configured:
    - Local callers are accepted (dev convenience).
    - A caller with a valid bearer credential is accepted as that principal.
    - Anyone else is rejected with 403 (fail-closed).
    """
    configured = os.environ.get("REPOWISE_GITLAB_WEBHOOK_TOKEN", "")
    if not configured:
        principal = await _unsigned_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook token not configured. "
                    "Set REPOWISE_GITLAB_WEBHOOK_TOKEN or restrict the server "
                    "to a loopback interface."
                ),
            )
        return principal

    if not hmac.compare_digest(token_header, configured):
        raise HTTPException(status_code=401, detail="Invalid token")
    return _GITLAB_PRINCIPAL


async def _verify_gitea_signature(
    request: Request, body: bytes, signature_header: str
) -> Principal:
    """Verify a Gitea or Forgejo HMAC-SHA256 webhook signature.

    Both sign the raw body with HMAC-SHA256 and send the digest as **bare
    lowercase hex with no ``sha256=`` prefix** (``X-Gitea-Signature`` /
    ``X-Forgejo-Signature``), which is the one way their delivery differs from
    GitHub's. They also send GitHub's prefixed ``X-Hub-Signature-256`` for
    compatibility, so a Gitea hook pointed at ``/api/webhooks/github`` verifies
    there too — it is just recorded as a GitHub event. Only the native header
    is read here; accepting the GitHub form on this route as well would mean two
    signature grammars per endpoint and no stricter a check.

    Reads REPOWISE_GITEA_WEBHOOK_SECRET per-call so env changes are always
    picked up.

    When the secret is not configured:
    - Local callers are accepted (dev convenience).
    - A caller with a valid bearer credential is accepted as that principal.
    - Anyone else is rejected with 403 (fail-closed).
    """
    secret = os.environ.get("REPOWISE_GITEA_WEBHOOK_SECRET", "")
    if not secret:
        principal = await _unsigned_principal(request)
        if principal is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Webhook secret not configured. "
                    "Set REPOWISE_GITEA_WEBHOOK_SECRET or restrict the server "
                    "to a loopback interface."
                ),
            )
        return principal

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header.strip().lower()):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return _GITEA_PRINCIPAL


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


async def _sync_on_push(
    request: Request,
    session: AsyncSession,
    *,
    event: WebhookEvent,
    principal: Principal,
    payload: dict,
    repo_url: str,
) -> None:
    """Enqueue an incremental sync for a push to a registered repo's default branch.

    Every forge route ends here. They differ in how a delivery names its
    repository and spells its push event, never in what a push means to us, so
    the branch check, the scope check and the job are shared: a third copy of
    them is a third place for the scope check to be forgotten.

    A push to any other branch, or to a repo that is not registered, is stored
    as an event and does nothing else.
    """
    ref = payload.get("ref", "")
    if not ref.startswith("refs/heads/"):
        return
    branch = ref[len("refs/heads/") :]
    repo = await _find_repo_by_scm_url(session, repo_url)
    if repo is None or branch != repo.default_branch:
        return

    _require_repo_in_scope(principal, repo)
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
    principal = await _verify_github_signature(request, body, sig)

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
        await _sync_on_push(
            request,
            session,
            event=event,
            principal=principal,
            payload=payload,
            repo_url=repo_url,
        )

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
    principal = await _verify_gitlab_token(request, token)

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
        # isinstance, not `.get("project", {})`: an explicit null project would
        # otherwise raise here, where before it only did so for a branch push.
        project = payload.get("project")
        repo_url = project.get("web_url", "") if isinstance(project, dict) else ""
        await _sync_on_push(
            request,
            session,
            event=event,
            principal=principal,
            payload=payload,
            repo_url=repo_url,
        )

    return WebhookResponse(event_id=event.id)


@router.post("/gitea", response_model=WebhookResponse)
async def gitea_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """Receive and process Gitea and Forgejo webhook events.

    One endpoint serves both. Forgejo sends its own ``X-Forgejo-*`` headers
    *and* the ``X-Gitea-*`` set beside them, so either name is read and a
    Forgejo instance needs no separate route; the stored ``provider`` still
    records which of the two sent the delivery.

    Verifies the HMAC-SHA256 signature, stores the event, and enqueues a sync
    job for push events on the default branch.
    """
    body = await request.body()
    headers = request.headers
    # Forgejo sends both families; its own header is the more specific, so it
    # wins where both are present. The digest itself is identical in each.
    sig = headers.get("X-Forgejo-Signature") or headers.get("X-Gitea-Signature", "")
    principal = await _verify_gitea_signature(request, body, sig)

    # Forgejo is a Gitea fork and still sends X-Gitea-*, so presence of the
    # Forgejo header is what distinguishes the two.
    provider = "forgejo" if "X-Forgejo-Event" in headers else "gitea"
    event_type = headers.get("X-Forgejo-Event") or headers.get("X-Gitea-Event") or "unknown"
    delivery_id = headers.get("X-Forgejo-Delivery") or headers.get("X-Gitea-Delivery", "")
    payload = json.loads(body)

    # Gitea's push payload follows GitHub's shape, clone_url included.
    repo_url = ""
    if "repository" in payload:
        repo_url = payload["repository"].get("clone_url", "")
        if not repo_url:
            repo_url = payload["repository"].get("html_url", "")

    event = await crud.store_webhook_event(
        session,
        provider=provider,
        event_type=event_type,
        payload=payload,
        delivery_id=delivery_id,
    )

    # Gitea and Forgejo name the push event "push", as GitHub does — not
    # GitLab's "Push Hook".
    if event_type == "push":
        await _sync_on_push(
            request,
            session,
            event=event,
            principal=principal,
            payload=payload,
            repo_url=repo_url,
        )

    return WebhookResponse(event_id=event.id)
