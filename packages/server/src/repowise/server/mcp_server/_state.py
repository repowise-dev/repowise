"""Shared mutable state for the MCP server — set during lifespan."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_session_factory: async_sessionmaker[AsyncSession] | None = None
_vector_store: Any = None
_decision_store: Any = None
_fts: Any = None
_repo_path: str | None = None
# Set to an asyncio.Event by _lifespan; signals that vector stores are loaded.
# tool_search awaits this before searching to avoid racing a background load.
_vector_store_ready: asyncio.Event | None = None

# Workspace mode — set by _lifespan when a workspace is detected.
_registry: Any = None          # RepoRegistry | None
_workspace_root: str | None = None
_cross_repo_enricher: Any = None  # CrossRepoEnricher | None

# Embedder health — set by _resolve_embedder() in _server.py. ``None`` until an
# embedder is resolved. When an explicitly-configured embedder fails to
# initialise we still fall back to MockEmbedder (so non-RAG tools stay up) but
# record the degradation here so build_meta() can surface it in every tool's
# `_meta` envelope — otherwise broken semantic search looks perfectly healthy
# (issue #306). Shape: {"active": str, "requested": str | None,
# "degraded": bool, "reason": str (only when degraded)}.
_embedder_status: dict[str, Any] | None = None
