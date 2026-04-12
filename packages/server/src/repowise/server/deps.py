"""FastAPI dependency injection for repowise server.

Provides Depends() callables for:
- Database sessions (async, auto-commit/rollback)
- Vector store access
- Full-text search access
- Optional API key authentication
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import AsyncGenerator

from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException, Request, Security
from repowise.core.persistence.database import get_session

logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("REPOWISE_API_KEY")
_REPOWISE_HOST = os.environ.get("REPOWISE_HOST", "127.0.0.1")
_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)

# Warn at import time if server is network-exposed without authentication
if _API_KEY is None and _REPOWISE_HOST in ("0.0.0.0", "::"):
    logger.warning(
        "SECURITY WARNING: Server is binding to %s without REPOWISE_API_KEY set. "
        "All endpoints are unauthenticated and network-accessible. "
        "Set REPOWISE_API_KEY or bind to 127.0.0.1.",
        _REPOWISE_HOST,
    )


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


async def get_workspace_config(request: Request):
    """Return WorkspaceConfig from app state, or None in single-repo mode."""
    return getattr(request.app.state, "workspace_config", None)


async def get_cross_repo_enricher(request: Request):
    """Return CrossRepoEnricher from app state, or None."""
    return getattr(request.app.state, "cross_repo_enricher", None)


async def verify_api_key(
    auth: str | None = Security(_header_scheme),
) -> None:
    """API key verification.

    When REPOWISE_API_KEY is not set and server binds to loopback, this is a
    no-op (local-only access). When binding to a non-loopback address without
    a key, requests are rejected (fail-closed for network-exposed deployments).
    When set, requests must include ``Authorization: Bearer <key>``.
    """
    if _API_KEY is None:
        if _REPOWISE_HOST in ("0.0.0.0", "::"):
            raise HTTPException(
                status_code=403,
                detail="Server is network-exposed but REPOWISE_API_KEY is not set. "
                "Set REPOWISE_API_KEY or bind to 127.0.0.1.",
            )
        return
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key")
    if not hmac.compare_digest(auth[7:], _API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")
