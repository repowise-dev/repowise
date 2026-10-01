"""/api/repos/{repo_id}/actions: the short list of things worth doing next."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.actions import load_actions_view, set_action_state
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.actions import (
    ActionsResponse,
    ActionStateRequest,
    ActionStateResponse,
)

router = APIRouter(tags=["actions"], dependencies=[Depends(verify_api_key)])


@router.get("/api/repos/{repo_id}/actions", response_model=ActionsResponse)
async def get_actions(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Both horizons in one response, so switching between them costs nothing."""
    await _require_repo(session, repo_id)
    return await load_actions_view(session, repo_id)


async def _require_repo(session: AsyncSession, repo_id: str) -> None:
    if await crud.get_repository(session, repo_id) is None:
        raise HTTPException(status_code=404, detail="Repository not found")


@router.put(
    "/api/repos/{repo_id}/actions/{action_id}/state",
    response_model=ActionStateResponse,
)
async def put_action_state(
    repo_id: str,
    body: ActionStateRequest,
    action_id: str = Path(..., max_length=32),
    session: AsyncSession = Depends(get_db_session),
) -> ActionStateResponse:
    await _require_repo(session, repo_id)
    until = (
        datetime.now(UTC) + timedelta(days=body.snooze_days) if body.state == "snoozed" else None
    )
    await set_action_state(
        session,
        repo_id,
        action_id,
        state=body.state,
        fingerprint=body.fingerprint,
        until=until,
    )
    return ActionStateResponse(action_id=action_id, state=body.state, until=until)
