"""/api/symbols — Symbol lookup and search."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, HTTPException, Query
from repowise.core.persistence.models import WikiSymbol
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.schemas import SymbolResponse

router = APIRouter(
    prefix="/api/symbols",
    tags=["symbols"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("", response_model=list[SymbolResponse])
async def search_symbols(
    repo_id: str = Query(..., description="Repository ID"),
    q: str = Query("", description="Search query (substring match on name)"),
    kind: str | None = Query(None, description="Filter by symbol kind"),
    language: str | None = Query(None, description="Filter by language"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[SymbolResponse]:
    """Search symbols by name, kind, or language."""
    query = select(WikiSymbol).where(WikiSymbol.repository_id == repo_id)

    if q:
        query = query.where(WikiSymbol.name.ilike(f"%{q}%"))
    if kind:
        query = query.where(WikiSymbol.kind == kind)
    if language:
        query = query.where(WikiSymbol.language == language)

    query = query.order_by(WikiSymbol.name).limit(limit).offset(offset)

    result = await session.execute(query)
    symbols = result.scalars().all()
    return [SymbolResponse.from_orm(s) for s in symbols]


@router.get("/by-name/{name}", response_model=list[SymbolResponse])
async def lookup_by_name(
    name: str,
    repo_id: str = Query(..., description="Repository ID"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[SymbolResponse]:
    """Look up symbols by exact or fuzzy name match.

    Returns exact matches first, then LIKE matches, up to 10 results.
    """
    # Try exact match first
    result = await session.execute(
        select(WikiSymbol).where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name == name,
        )
    )
    exact = list(result.scalars().all())
    if exact:
        return [SymbolResponse.from_orm(s) for s in exact]

    # Fall back to LIKE match
    result = await session.execute(
        select(WikiSymbol)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.name.ilike(f"%{name}%"),
        )
        .limit(10)
    )
    fuzzy = result.scalars().all()
    return [SymbolResponse.from_orm(s) for s in fuzzy]


@router.get("/{symbol_db_id}", response_model=SymbolResponse)
async def get_symbol(
    symbol_db_id: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SymbolResponse:
    """Get a single symbol by its database ID."""
    sym = await session.get(WikiSymbol, symbol_db_id)
    if sym is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return SymbolResponse.from_orm(sym)
