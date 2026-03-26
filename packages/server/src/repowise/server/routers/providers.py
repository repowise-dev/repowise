"""Provider management endpoints — list, activate, manage API keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from repowise.server.deps import verify_api_key
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


@router.get("")
async def get_providers():
    """List all providers with their status and active selection."""
    return list_provider_status()


@router.patch("/active")
async def set_active(body: SetActiveProviderRequest):
    """Set the active provider and model."""
    try:
        set_active_provider(body.provider, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return list_provider_status()


@router.post("/{provider_id}/key", status_code=204)
async def add_provider_key(provider_id: str, body: SetApiKeyRequest):
    """Store an API key for a provider."""
    try:
        set_api_key(provider_id, body.api_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{provider_id}/key", status_code=204)
async def remove_provider_key(provider_id: str):
    """Remove a provider's API key."""
    try:
        set_api_key(provider_id, None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
