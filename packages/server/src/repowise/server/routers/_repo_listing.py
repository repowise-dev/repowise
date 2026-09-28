"""Row assembly for ``GET /api/repos``: merge primary and workspace databases,
flag unindexed repos, and in workspace mode attach aliases and placeholders.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.docs_mode import resolve_docs_mode
from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.mcp_server._meta import resolve_indexed_commit
from repowise.server.routers._sorting import repository_sort_key
from repowise.server.schemas import RepoResponse


def _select_repos_with_index_flag():
    """``(Repository, has file nodes)`` rows: the honest "was it indexed" test."""
    has_files_subq = (
        select(GraphNode.repository_id)
        .where(GraphNode.node_type == "file")
        .group_by(GraphNode.repository_id)
        .subquery()
    )
    return select(Repository, has_files_subq.c.repository_id.is_not(None)).outerjoin(
        has_files_subq, Repository.id == has_files_subq.c.repository_id
    )


async def load_repositories(
    session: AsyncSession, ws_sessions: Mapping[str, Callable[[], Any]]
) -> tuple[list[Repository], set[str]]:
    """Every registered repository, newest first, and the ids that are indexed."""
    result = await session.execute(
        _select_repos_with_index_flag().order_by(Repository.updated_at.desc())
    )
    repos: list[Repository] = []
    indexed_repo_ids: set[str] = set()
    for r, is_indexed in result.all():
        repos.append(r)
        if is_indexed:
            indexed_repo_ids.add(r.id)
    seen_ids = {r.id for r in repos}

    # In workspace mode, also fetch repos from other workspace DBs
    for repo_id, ws_factory in ws_sessions.items():
        if repo_id in seen_ids:
            continue
        try:
            async with ws_factory() as ws_session:
                ws_result = await ws_session.execute(
                    _select_repos_with_index_flag().where(Repository.id == repo_id)
                )
                row = ws_result.first()
                if row:
                    ws_repo, ws_is_indexed = row
                    repos.append(ws_repo)
                    seen_ids.add(ws_repo.id)
                    if ws_is_indexed:
                        indexed_repo_ids.add(ws_repo.id)
        except Exception:
            pass

    # Rows from different backends mix aware and naive timestamps; the key normalizes them.
    repos.sort(key=repository_sort_key, reverse=True)
    return repos, indexed_repo_ids


def heal_indexed_commits(responses: list[RepoResponse]) -> None:
    # Prefer state.json's last_sync_commit over a possibly stale DB head_commit.
    for resp in responses:
        if resp.local_path:
            resp.head_commit = resolve_indexed_commit(resp.head_commit, resp.local_path)


def flag_unindexed(responses: list[RepoResponse], indexed_repo_ids: set[str]) -> None:
    # head_commit is stamped at registration, so "indexed" means file nodes exist.
    for resp in responses:
        if resp.workspace_status is None and resp.local_path and resp.id not in indexed_repo_ids:
            try:
                if not Path(resp.local_path).is_dir():
                    resp.workspace_status = "missing_dir"
                else:
                    resp.workspace_status = "needs_index"
            except OSError:
                resp.workspace_status = "needs_index"


def _apply_state_file(resp: RepoResponse) -> None:
    # The docs mode and index tier are recorded per-repo in state.json.
    # Read it once per response: cheap, and never failing.
    state_path = Path(resp.local_path) / ".repowise" / "state.json"
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        resp.docs_mode = resolve_docs_mode(state)
        # A state file with no docs fields at all keeps the legacy docs_enabled=True.
        if not any(k in state for k in ("docs_mode", "docs_enabled", "provider", "model")):
            resp.docs_enabled = True
        else:
            resp.docs_enabled = resp.docs_mode != "none"
        resp.docs_skip_reason = state.get("docs_skip_reason")
        resp.run_mode = state.get("run_mode")
        resp.git_tier = state.get("git_tier")
    except Exception:
        pass


def attach_workspace_metadata(
    responses: list[RepoResponse],
    ws_config: Any,
    ws_root_path: Path,
    indexed_repo_ids: set[str],
) -> set[str]:
    """Attach alias + identity + docs status to registered rows.

    Returns the aliases that matched a registered row.
    """
    # Map local_path → alias entry for quick attach on registered rows.
    by_path: dict[str, object] = {
        str((ws_root_path / e.path).resolve()): e for e in ws_config.repos
    }
    matched_aliases: set[str] = set()
    for resp in responses:
        if not resp.local_path:
            continue
        entry = by_path.get(str(Path(resp.local_path).resolve()))
        if entry is None:
            continue
        resp.workspace_alias = entry.alias
        resp.is_primary = bool(entry.is_primary)
        if resp.id in indexed_repo_ids:
            resp.workspace_status = "indexed"
        matched_aliases.add(entry.alias)
        _apply_state_file(resp)
    return matched_aliases


def unregistered_workspace_rows(
    ws_config: Any, ws_root_path: Path, matched_aliases: set[str]
) -> list[RepoResponse]:
    """Synthesize entries for repos in the workspace that aren't registered yet."""
    now = datetime.now(UTC)
    rows: list[RepoResponse] = []
    for entry in ws_config.repos:
        if entry.alias in matched_aliases:
            continue
        abs_path = (ws_root_path / entry.path).resolve()
        status = "needs_index" if abs_path.is_dir() else "missing_dir"
        # Synthetic, stable, prefixed ID so the frontend can route to a
        # CTA card without colliding with real repo UUIDs.
        synthetic_id = f"ws:{entry.alias}"
        rows.append(
            RepoResponse(
                id=synthetic_id,
                name=entry.alias,
                url="",
                local_path=str(abs_path),
                default_branch="main",
                head_commit=None,
                settings={},
                created_at=now,
                updated_at=now,
                workspace_alias=entry.alias,
                workspace_status=status,
                is_primary=bool(entry.is_primary),
                docs_enabled=False,
                docs_mode="none",
                docs_skip_reason="not indexed yet",
            )
        )
    return rows
