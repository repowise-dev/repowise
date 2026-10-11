"""/api/graph — Dependency graph export in D3-compatible format.

This package splits the former monolithic ``graph.py`` into per-concern
sub-routers. The public surface is unchanged: ``graph.router`` is assembled
here from the sub-routers and mounted by ``app.py`` exactly as before.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from repowise.server.deps import verify_api_key

from . import (
    community,
    full_graph,
    intelligence,
    module_graph,
    neighborhoods,
    path,
)

router = APIRouter(
    prefix="/api/graph",
    tags=["graph"],
    dependencies=[Depends(verify_api_key)],
)

# Include order keeps the single-segment catch-all (`/{repo_id}` in full_graph)
# registered after the more-specific multi-segment routes.
router.include_router(module_graph.router)
router.include_router(neighborhoods.router)
router.include_router(community.router)
router.include_router(intelligence.router)
router.include_router(path.router)
router.include_router(full_graph.router)

__all__ = ["router"]
