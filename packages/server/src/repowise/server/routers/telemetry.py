"""``/api/telemetry``: anonymous usage events the web UI reports through its server.

The browser never talks to the telemetry backend. It names the view it opened,
the server checks the name against a fixed list and records the event through
the same consent path as every other event, so an opt-out (stored or by
environment) silences it here too.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from repowise.core.platform import telemetry
from repowise.server.deps import verify_api_key

router = APIRouter(
    prefix="/api/telemetry", tags=["telemetry"], dependencies=[Depends(verify_api_key)]
)

#: The web UI's navigation entries, by the message key each carries in
#: ``packages/web/src/components/layout/nav-items.ts``, scoped by the nav list
#: they come from. A test keeps the two in step.
_GLOBAL_VIEWS = ("dashboard", "settings")
_WORKSPACE_VIEWS = (
    "overview",
    "systemMap",
    "conformance",
    "contracts",
    "coChanges",
    "estateReport",
    "setup",
)
_REPO_VIEWS = (
    "overview",
    "chat",
    "review",
    "docs",
    "architecture",
    "knowledgeGraph",
    "files",
    "codeHealth",
    "refactoring",
    "tests",
    "security",
    "contributors",
    "commits",
    "decisions",
    "stats",
    "usageAndSavings",
    "settings",
)

#: Every value a ``view_opened`` event may carry.
VIEWS = frozenset(
    {
        *(f"global.{v}" for v in _GLOBAL_VIEWS),
        *(f"workspace.{v}" for v in _WORKSPACE_VIEWS),
        *(f"repo.{v}" for v in _REPO_VIEWS),
    }
)


class ViewOpenedRequest(BaseModel):
    view: str = Field(..., max_length=64, description="A view id, e.g. repo.codeHealth")


@router.post("/view", status_code=204)
async def view_opened(body: ViewOpenedRequest) -> Response:
    """Record that a web view was opened. An unknown view is refused, never recorded."""
    if body.view not in VIEWS:
        raise HTTPException(status_code=422, detail="Unknown view.")
    telemetry.record_event("view_opened", {"view": body.view})
    return Response(status_code=204)
