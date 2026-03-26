"""FastAPI dependency injection for repowise server.

Provides Depends() callables for:
- Database sessions (async, auto-commit/rollback)
- Vector store access
- Full-text search access
- Optional API key authentication
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.database import get_session

_API_KEY = os.environ.get("REPOWISE_API_KEY")
_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session with auto-commit on success, rollback on error."""
    factory = request.app.state.session_factory
    async with get_session(factory) as session:
        yield session


async def get_vector_store(request: Request):
    """Return the vector store from app state."""
    return request.app.state.vector_store


async def get_fts(request: Request):
    """Return the full-text search engine from app state."""
    return request.app.state.fts


async def verify_api_key(
    auth: str | None = Security(_header_scheme),
) -> None:
    """Optional API key verification.

    When REPOWISE_API_KEY is not set, this is a no-op (fully open).
    When set, requests must include ``Authorization: Bearer <key>``.
    """
    if _API_KEY is None:
        return
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    if auth[7:] != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
