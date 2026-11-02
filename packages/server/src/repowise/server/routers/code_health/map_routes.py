"""The bounded field the Code Health map draws.

A thin adapter: the cap, the guaranteed paths, and the deterministic selection
order live in the shared map service, so the rendered set and the counts that
describe it come back from one place and cannot disagree.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.areas import normalize_area
from repowise.server.deps import get_db_session
from repowise.server.routers.held_origins import HOLD_QUERY, INCLUDE_QUERY
from repowise.server.services.areas import held_origins
from repowise.server.services.health_map import (
    DEFAULT_MAP_CAP,
    MAX_MAP_CAP,
    HealthMapService,
    parse_active,
)

from ._router import router
from .counts import CountsQuery
from .scope import ScopeQuery


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
    include_held: bool = Query(
        False,
        description="Also draw vendored and generated files, which are held out by default.",
    ),
    q: str | None = Query(
        None,
        max_length=200,
        description="Pin the files whose path contains this substring, so search finds undrawn files.",
    ),
    area: str | None = Query(
        None,
        max_length=1024,
        description=(
            "Narrow the field to this directory (any depth). Ranking and the cap "
            "apply within it, and every count describes the area."
        ),
    ),
    scope: str = ScopeQuery,
    counts: str = CountsQuery,
    hold: str = HOLD_QUERY,
    include: str = INCLUDE_QUERY,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One bounded field plus the exact scope of what the cap left out."""
    feed = await HealthMapService(session, repo_id).feed(
        cap=cap,
        active=parse_active(active),
        scope=scope,
        counts=counts,
        include_held=include_held,
        query=q,
        area=normalize_area(area) or None,
        held=held_origins(hold, include) if hold == "map" else None,
    )
    return feed.payload()
