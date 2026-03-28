"""/api/search — Semantic and full-text search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from repowise.server.deps import get_fts, get_vector_store, verify_api_key
from repowise.server.schemas import SearchResultResponse

router = APIRouter(
    prefix="/api/search",
    tags=["search"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=list[SearchResultResponse])
async def search(
    query: str = Query(..., min_length=1, description="Search query"),
    search_type: str = Query("semantic", description="semantic or fulltext"),
    limit: int = Query(10, ge=1, le=100),
    vector_store=Depends(get_vector_store),  # noqa: B008
    fts=Depends(get_fts),  # noqa: B008
) -> list[SearchResultResponse]:
    """Search wiki pages by semantic similarity or full-text match."""
    if search_type == "fulltext":
        results = await fts.search(query, limit=limit)
    else:
        results = await vector_store.search(query, limit=limit)

    return [
        SearchResultResponse(
            page_id=r.page_id,
            title=r.title,
            page_type=r.page_type,
            target_path=r.target_path,
            score=r.score,
            snippet=r.snippet,
            search_type=r.search_type,
        )
        for r in results
    ]
