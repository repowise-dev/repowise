"""The bounded field the Code Health map draws.

A thin adapter: the cap, the guaranteed paths, and the deterministic selection
order live in the shared map service, so the rendered set and the counts that
describe it come back from one place and cannot disagree.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.server.deps import get_db_session
from repowise.server.services.health_map import (
    DEFAULT_MAP_CAP,
    MAX_MAP_CAP,
    HealthMapService,
    parse_active,
)

from ._router import router


@router.get("/api/repos/{repo_id}/health/map")
async def get_health_map(
    repo_id: str,
    cap: int = Query(DEFAULT_MAP_CAP, ge=1, le=MAX_MAP_CAP),
    active: str | None = Query(
        None,
        description=(
            "Comma-separated file paths guaranteed a node, admitted before any "
            "other band. A deep link into a selected file or opportunity passes "
            "them so the cap can never push the subject off its own map."
        ),
    ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One bounded field plus the exact scope of what the cap left out."""
    feed = await HealthMapService(session, repo_id).feed(
        cap=cap, active=parse_active(active)
    )
    return feed.payload()
