"""/api/repos/{repo_id}/actions: the short list of things worth doing next."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.agent_prompts import Flavor, render_action
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.actions import (
    load_action,
    load_actions_view,
    set_action_state,
)
from repowise.core.persistence.models import Repository
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas.actions import (
    ActionsResponse,
    ActionStateRequest,
    ActionStateResponse,
)
from repowise.server.schemas.agent_prompts import AgentPromptResponse

router = APIRouter(tags=["actions"], dependencies=[Depends(verify_api_key)])


@router.get("/api/repos/{repo_id}/actions", response_model=ActionsResponse)
async def get_actions(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Both horizons in one response, so switching between them costs nothing."""
    await _require_repo(session, repo_id)
    return await load_actions_view(session, repo_id)


async def _require_repo(session: AsyncSession, repo_id: str) -> Repository:
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get(
    "/api/repos/{repo_id}/actions/{action_id}/prompt",
    response_model=AgentPromptResponse,
)
async def get_action_prompt(
    repo_id: str,
    action_id: str = Path(..., max_length=32),
    flavor: Flavor = Query("generic"),
    session: AsyncSession = Depends(get_db_session),
) -> AgentPromptResponse:
    """One action as the prompt an agent starts from, worded for its harness."""
    repo = await _require_repo(session, repo_id)
    action = await load_action(session, repo_id, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="No action with that id")
    return AgentPromptResponse(flavor=flavor, text=render_action(action.as_dict(), flavor, repo.name))


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
