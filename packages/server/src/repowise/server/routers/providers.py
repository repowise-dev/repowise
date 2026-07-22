"""Provider management endpoints — list, activate, manage API keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server.deps import resolve_request_session_factory, verify_api_key
from repowise.server.provider_config import (
    list_provider_status,
    set_active_provider,
    set_api_key,
)
from repowise.server.schemas import SetActiveProviderRequest, SetApiKeyRequest

router = APIRouter(
    prefix="/api/providers",
    tags=["providers"],
    dependencies=[Depends(verify_api_key)],
)


async def _repo_path_for(request: Request, repo_id: str | None) -> str | None:
    """Best-effort lookup of a repo's on-disk path from its id.

    Lets the providers endpoint report the active provider/model that *this*
    repo's chat will actually use. Returns ``None`` (server-global resolution)
    when no ``repo_id`` is given or the repo can't be found.
    """
    if not repo_id:
        return None
    try:
        factory = resolve_request_session_factory(request)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
            return repo.local_path if repo else None
    except Exception:
        # Best-effort: fall back to server-global resolution rather than 500.
        return None


@router.get("")
async def get_providers(request: Request, repo_id: str | None = None):
    """List all providers with their status and active selection.

    Pass ``?repo_id=`` so the active selection reflects that repo's own
    ``.repowise/config.yaml`` (and any per-repo UI override) instead of the
    server-global default.
    """
    repo_path = await _repo_path_for(request, repo_id)
    return list_provider_status(repo_id=repo_id, repo_path=repo_path)


@router.patch("/active")
async def set_active(body: SetActiveProviderRequest, request: Request):
    """Set the active provider and model (per-repo when ``repo_id`` is given)."""
    try:
        set_active_provider(body.provider, body.model, repo_id=body.repo_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    repo_path = await _repo_path_for(request, body.repo_id)
    return list_provider_status(repo_id=body.repo_id, repo_path=repo_path)


@router.post("/{provider_id}/key", status_code=204)
async def add_provider_key(provider_id: str, body: SetApiKeyRequest, request: Request):
    """Store an API key for a provider.

    With ``repo_id`` in the body, the key is also written to that repo's
    ``.repowise/.env`` so a subsequent CLI run in the repo uses it.
    """
    repo_path = await _repo_path_for(request, body.repo_id)
    try:
        set_api_key(provider_id, body.api_key, repo_path=repo_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{provider_id}/key", status_code=204)
async def remove_provider_key(
    provider_id: str,
    request: Request,
    repo_id: str | None = None,
):
    """Remove a provider's API key.

    With ``?repo_id=``, the key is also cleared from that repo's
    ``.repowise/.env`` (the mirror written when it was added).
    """
    repo_path = await _repo_path_for(request, repo_id)
    try:
        set_api_key(provider_id, None, repo_path=repo_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
