"""Provider management endpoints — list, activate, manage API keys."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.providers.llm.resolve import validate_base_url
from repowise.server.deps import resolve_session_factory, verify_api_key
from repowise.server.provider_config import (
    PROVIDER_CATALOG,
    _redact_key,
    list_provider_status,
    set_active_provider,
    set_api_key,
)
from repowise.server.provider_models import list_provider_models
from repowise.server.schemas import (
    ProviderModelsResponse,
    ProviderStatusResponse,
    ProviderValidationResponse,
    SetActiveProviderRequest,
    SetApiKeyRequest,
)

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

    Routes by ``repo_id`` directly (not the request's path/query), so it reaches
    a workspace repo's own ``wiki.db`` even when ``repo_id`` arrived in the body
    (the ``POST .../key`` case). The primary DB does not hold the canonical row
    for a non-primary workspace repo, so a request-scoped lookup would miss it.
    """
    if not repo_id:
        return None
    try:
        factory = resolve_session_factory(request.app.state, repo_id)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
            return repo.local_path if repo else None
    except Exception:
        # Best-effort: fall back to server-global resolution rather than 500.
        return None


@router.get("", response_model=ProviderStatusResponse)
async def get_providers(request: Request, repo_id: str | None = None, detect: bool = False):
    """List all providers with their status and active selection.

    Pass ``?repo_id=`` so the active selection reflects that repo's own
    ``.repowise/config.yaml`` (and any per-repo UI override) instead of the
    server-global default. ``?detect=true`` also asks each agent CLI whether
    it is signed in (a short subprocess each), so it runs off the event loop.
    """
    repo_path = await _repo_path_for(request, repo_id)
    if detect:
        return await asyncio.to_thread(
            list_provider_status, repo_id=repo_id, repo_path=repo_path, detect=True
        )
    return list_provider_status(repo_id=repo_id, repo_path=repo_path)


@router.patch("/active", response_model=ProviderStatusResponse)
async def set_active(body: SetActiveProviderRequest, request: Request):
    """Set the active provider, model and base URL.

    With ``repo_id`` the choice is written to that repo's
    ``.repowise/config.yaml``, which the CLI reads too; without it, the
    server-wide default is set. ``base_url`` left out of the body keeps the
    current endpoint; ``""`` or ``null`` clears it.
    """
    repo_path = await _repo_path_for(request, body.repo_id)
    if body.repo_id and repo_path is None:
        raise HTTPException(404, f"Repository {body.repo_id} has no local checkout.")
    extra = {"base_url": body.base_url} if "base_url" in body.model_fields_set else {}
    try:
        set_active_provider(
            body.provider, body.model, repo_id=body.repo_id, repo_path=repo_path, **extra
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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


@router.post("/{provider_id}/validate", response_model=ProviderValidationResponse)
async def validate_provider(
    provider_id: str,
    request: Request,
    repo_id: str | None = None,
):
    """Live smoke test for a single provider's configured key.

    Instantiates *this* provider (not the active one) with the key resolved for
    the repo and does a tiny ``generate`` round-trip — the same probe the CLI
    and pre-index preflight use, scoped to one provider so the settings UI can
    confirm a key it just added actually works. A keyless provider (Ollama,
    mock) still exercises reachability. Returns ``{ok, provider, model, error}``
    rather than raising, so the UI renders a clean error state.
    """
    repo_path = await _repo_path_for(request, repo_id)

    provider_name: str | None = None
    model_name: str | None = None
    llm_client = None
    try:
        from repowise.server.provider_config import get_chat_provider_instance

        llm_client = get_chat_provider_instance(
            repo_path=repo_path, repo_id=repo_id, provider_override=provider_id
        )
        provider_name = getattr(llm_client, "provider_name", None)
        model_name = getattr(llm_client, "model_name", None)
    except Exception as exc:
        return {"ok": False, "provider": provider_id, "model": None, "error": _redact_key(str(exc))}

    try:
        await llm_client.generate("You are a test.", "Reply with OK.", max_tokens=50)
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider_name,
            "model": model_name,
            "error": _redact_key(str(exc)),
        }

    return {"ok": True, "provider": provider_name, "model": model_name, "error": None}


@router.get("/{provider_id}/models", response_model=ProviderModelsResponse)
async def get_provider_models(
    provider_id: str,
    request: Request,
    repo_id: str | None = None,
    base_url: str | None = None,
    refresh: bool = False,
):
    """Models the provider offers, listed live where it can.

    Uses the key and endpoint resolved for ``repo_id``. ``base_url`` tests an
    endpoint before it is saved (the "Test connection" button); the stored key
    is never sent to a URL it was not saved for. On any failure the built-in
    list comes back with ``source: "catalog"`` and ``error`` saying why.
    """
    if provider_id not in {p["id"] for p in PROVIDER_CATALOG}:
        raise HTTPException(404, f"Unknown provider: {provider_id}")
    try:
        probe_url = validate_base_url(base_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    repo_path = await _repo_path_for(request, repo_id)
    return await list_provider_models(
        provider_id, repo_path, base_url=probe_url, refresh=refresh
    )
