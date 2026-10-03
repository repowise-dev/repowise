"""``/api/platform`` endpoints: who is using this install, and publishing to repowise.dev.

``/identity`` lets the web UI decide whether to show repowise.dev tips and
whether its publish button can run, from local files only (no network).
``/publish`` runs ``repowise publish --format json`` for one repo, so the button
and the CLI share one decision and one set of messages. The server never
imports ``repowise.cli``; it runs the CLI in a child process instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.platform import account, telemetry
from repowise.server.deps import resolve_session_factory, verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/platform", tags=["platform"], dependencies=[Depends(verify_api_key)]
)

#: Credits the links in the CLI's answer to this button.
_SRC = "local_web_publish"

#: The CLI waits up to 60 s on repowise.dev; leave room for start-up on top.
_TIMEOUT_SECONDS = 90.0

_CANT_RUN = "Couldn't run repowise publish here. Run it in a terminal: repowise publish"


class IdentityResponse(BaseModel):
    #: Only while telemetry is on, so the site's links carry it only by consent.
    anon_id: str | None
    signed_in: bool
    hints_enabled: bool


class PublishRequest(BaseModel):
    repo_id: str = Field(..., min_length=1)


@router.get("/identity", response_model=IdentityResponse)
async def get_identity() -> IdentityResponse:
    """Anonymous id, sign-in state and the tips switch, read from ``~/.repowise``."""
    return IdentityResponse(
        anon_id=telemetry.get_anonymous_id() if telemetry.is_enabled() else None,
        signed_in=account.is_signed_in(),
        hints_enabled=account.hints_enabled(),
    )


def _run_publish(local_path: str) -> dict:
    """Run the CLI and return its JSON answer, or an ``error`` outcome."""
    argv = [
        sys.executable,
        "-m",
        "repowise.cli.main",
        "publish",
        local_path,
        "--format",
        "json",
        "--no-open",
        "--src",
        _SRC,
    ]
    try:
        # Exit code 1 still prints the answer (a refusal, signed out, ...), so
        # the code is ignored and stdout decides.
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False
        )
        result = json.loads(proc.stdout)
        if isinstance(result, dict) and isinstance(result.get("outcome"), str):
            return result
        logger.warning("repowise publish printed no outcome: %r", proc.stdout[:200])
    except Exception as exc:
        logger.warning("repowise publish could not run: %s", exc)
    return {
        "outcome": "error",
        "message": _CANT_RUN,
        "url": None,
        "details": [],
        "open_url": None,
        "repo": None,
    }


@router.post("/publish")
async def publish(body: PublishRequest, request: Request) -> dict:
    """Publish one indexed repo on repowise.dev, answering with the CLI's result."""
    factory = resolve_session_factory(request.app.state, body.repo_id)
    async with get_session(factory) as session:
        repo = await crud.get_repository(session, body.repo_id)
    if repo is None or not repo.local_path:
        raise HTTPException(status_code=404, detail="Repository not found")
    return await asyncio.to_thread(_run_publish, repo.local_path)
