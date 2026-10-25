"""The Setup page's one payload: index, agents, hooks, coverage, model, checks.

Agent, hook and doctor state come from the CLI's own JSON (see
:mod:`.setup_cli`); the rest is read here from the index, the savings ledger,
the coverage discovery and the provider settings the other routes already use.
The payload is cached for a few seconds per repo, because doctor starts the MCP
server once and a page load must not do that on every request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.services import setup_cli

logger = logging.getLogger(__name__)

#: How long one payload answers repeat requests for the same repo.
SETUP_CACHE_SECONDS = 5.0

#: Bounds on the lists the payload carries.
_PARSE_ERROR_FILES_LIMIT = 50
_DISCOVERED_REPORTS_LIMIT = 10

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}
#: Bumped by every write, so a build that started before it is not cached.
_generation = 0


def invalidate(repo_id: str | None = None) -> None:
    """Drop the cached payload for *repo_id*, or every one when ``None``."""
    global _generation
    _generation += 1
    if repo_id is None:
        _cache.clear()
    else:
        _cache.pop(repo_id, None)


async def setup_payload(session: AsyncSession, repo: Repository) -> dict[str, Any]:
    """The cached payload for *repo*, built once per window even under concurrent loads."""
    lock = _locks.setdefault(repo.id, asyncio.Lock())
    async with lock:
        hit = _cache.get(repo.id)
        if hit is not None and time.monotonic() - hit[0] < SETUP_CACHE_SECONDS:
            return hit[1]
        started = _generation
        payload = await _build(session, repo)
        if started == _generation:
            _cache[repo.id] = (time.monotonic(), payload)
        return payload


async def _build(session: AsyncSession, repo: Repository) -> dict[str, Any]:
    repo_path = Path(repo.local_path)
    index = await _index_section(session, repo)
    ingest_ok, coverage_ingested_at = await _coverage_ingested_at(session, repo.id)
    doctor, agents, local = await asyncio.gather(
        asyncio.to_thread(setup_cli.doctor_json, repo_path),
        asyncio.to_thread(setup_cli.agents_json, repo_path),
        asyncio.to_thread(_local_sections, repo.id, repo_path),
    )
    checks = _checks(doctor)
    agent_calls, coverage_files, (model_ok, model) = local
    agent_rows, calls = _agents(agents, agent_calls or {})
    return {
        "repo_id": repo.id,
        "generated_at": datetime.now(UTC).isoformat(),
        "index": index,
        "agents_available": agents is not None,
        "agents": agent_rows,
        "calls_available": agent_calls is not None,
        "calls": calls,
        "sync": _sync(checks),
        "rewrite_hook": _rewrite_hook(checks, agent_rows),
        "coverage_available": ingest_ok and coverage_files is not None,
        "coverage": {
            "ingested_at": coverage_ingested_at,
            **(coverage_files or {"configured_paths": [], "discovered": []}),
        },
        "model_available": model_ok,
        "model": model,
        "checks_available": doctor is not None,
        "checks": checks,
    }


# -- index -------------------------------------------------------------------


async def _index_section(session: AsyncSession, repo: Repository) -> dict[str, Any]:
    from repowise.core.git_head import read_head_commit
    from repowise.core.persistence.crud import index_synced_at
    from repowise.core.workspace.update import resolve_indexed_commit

    is_file = (GraphNode.repository_id == repo.id) & (GraphNode.node_type == "file")
    file_count = (await session.execute(select(func.count()).where(is_file))).scalar_one()
    error_rows = await session.execute(
        select(GraphNode.node_id).where(is_file, GraphNode.has_error.is_(True)).order_by(
            GraphNode.node_id
        )
    )
    error_files = [row[0] for row in error_rows]

    indexed = resolve_indexed_commit(Path(repo.local_path), repo.head_commit).sha
    live = read_head_commit(repo.local_path)
    synced_at = index_synced_at(repo)
    return {
        "state": _index_state(file_count, indexed, live),
        "indexed_commit": indexed,
        "live_head": live,
        "indexed_at": synced_at.isoformat() if synced_at else None,
        "file_count": file_count,
        "parse_error_count": len(error_files),
        "parse_error_files": error_files[:_PARSE_ERROR_FILES_LIMIT],
    }


def _index_state(file_count: int, indexed: str | None, live: str | None) -> str:
    """``incomplete`` without files or a resolvable commit; ``behind`` when HEAD moved."""
    if not file_count or not indexed:
        return "incomplete"
    if live and live != indexed:
        return "behind"
    return "up_to_date"


async def _coverage_ingested_at(session: AsyncSession, repo_id: str) -> tuple[bool, str | None]:
    """``(read ok, newest ingest time)``; an unreadable table is not "never ingested"."""
    from repowise.core.persistence.models import CoverageFile

    try:
        stamp = (
            await session.execute(
                select(func.max(CoverageFile.ingested_at)).where(
                    CoverageFile.repository_id == repo_id
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        logger.warning("setup: could not read stored coverage for %s", repo_id, exc_info=True)
        return False, None
    return True, stamp.isoformat() if isinstance(stamp, datetime) else None


# -- local files: ledger, coverage reports, model -----------------------------


def _local_sections(
    repo_id: str, repo_path: Path
) -> tuple[dict[str, tuple[str, str]] | None, dict[str, Any] | None, tuple[bool, dict[str, Any]]]:
    """The blocking reads that need no CLI, run together in one thread.

    Each comes back ``None`` (or flagged) when it could not be read, so the
    page can say so instead of showing an empty section.
    """
    return _agent_calls(repo_path), _coverage_files(repo_path), _model(repo_id, repo_path)


def _agent_calls(repo_path: Path) -> dict[str, tuple[str, str]] | None:
    """Per-agent call times; ``{}`` with no ledger yet, ``None`` when it could not be read."""
    from repowise.core.savings.service import load_agent_calls

    try:
        return load_agent_calls(repo_path) or {}
    except (sqlite3.Error, OSError):
        logger.warning("setup: could not read the savings ledger in %s", repo_path, exc_info=True)
        return None


def _coverage_files(repo_path: Path) -> dict[str, Any] | None:
    """Configured report paths and the reports discovery finds; ``None`` when unreadable."""
    from repowise.core.analysis.health.coverage import CoverageConfig, discover_artifacts
    from repowise.core.repo_config import load_repo_config

    try:
        cfg = CoverageConfig.from_repo_config(load_repo_config(repo_path))
        found = discover_artifacts(repo_path, globs=cfg.artifacts or None)
    except (OSError, ValueError):
        # ValueError covers an unparsable .repowise/config.yaml (RepoConfigError).
        logger.warning("setup: could not look for coverage in %s", repo_path, exc_info=True)
        return None
    now = time.time()
    discovered = []
    for report in found[:_DISCOVERED_REPORTS_LIMIT]:
        try:
            mtime = report.stat().st_mtime
        except OSError:
            continue
        discovered.append(
            {
                "path": report.relative_to(repo_path).as_posix(),
                "modified_at": datetime.fromtimestamp(mtime, UTC).isoformat(),
                "age_days": max(0, int((now - mtime) // 86_400)),
            }
        )
    return {"configured_paths": list(cfg.paths), "discovered": discovered}


def _model(repo_id: str, repo_path: Path) -> tuple[bool, dict[str, Any]]:
    """Docs mode from state.json, and the provider settings ``/api/providers`` reports.

    ``(False, ...)`` when the provider settings could not be read; docs mode
    is still filled in.
    """
    from repowise.core.docs_mode import resolve_docs_mode
    from repowise.server.provider_config import list_provider_status

    try:
        state = json.loads((repo_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    model: dict[str, Any] = {
        "docs_mode": resolve_docs_mode(state if isinstance(state, dict) else {}),
        "active_provider": None,
        "active_model": None,
        "configured_providers": [],
    }
    try:
        status = list_provider_status(repo_id=repo_id, repo_path=repo_path)
    except (OSError, ValueError):
        # ValueError covers an unparsable config.yaml or .env (RepoConfigError).
        logger.warning("setup: could not read provider settings for %s", repo_path, exc_info=True)
        return False, model
    model["active_provider"] = status["active"]["provider"]
    model["active_model"] = status["active"]["model"]
    model["configured_providers"] = [p["id"] for p in status["providers"] if p["configured"]]
    return True, model


# -- CLI answers --------------------------------------------------------------


def _checks(doctor: dict | None) -> list[dict[str, Any]]:
    """Doctor rows, keeping only the fields the page reads."""
    rows = doctor.get("checks") if doctor else None
    return [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "level": row.get("level") if row.get("level") in ("ok", "warn", "fail") else "warn",
            "detail": str(row.get("detail") or ""),
            "fix": row.get("fix") if isinstance(row.get("fix"), str) else None,
        }
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]


def _agents(
    listing: dict | None, calls: dict[str, tuple[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Agent rows with their own call times, and the repo-level call summary."""
    from repowise.core.agents.identity import UNKNOWN_AGENT, identity_for_target_id

    rows = []
    for row in _listing_rows(listing):
        identity = identity_for_target_id(str(row.get("id", "")))
        first, last = calls.get(identity.slug, (None, None)) if identity else (None, None)
        rows.append(_agent_row(row, first, last))
    times = list(calls.values())
    summary = {
        "first_call_at": min((t[0] for t in times), default=None),
        "last_call_at": max((t[1] for t in times), default=None),
        # Calls whose client named no known agent count here and on no row.
        "unattributed": UNKNOWN_AGENT in calls,
    }
    return rows, summary


def _listing_rows(listing: dict | None) -> list[dict]:
    rows = listing.get("agents") if listing else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _agent_row(row: dict, first: str | None, last: str | None) -> dict[str, Any]:
    registrations = [r for r in row.get("registrations") or [] if isinstance(r, dict)]
    files = row.get("config_files")
    return {
        "id": str(row.get("id", "")),
        "display_name": str(row.get("display_name", "")),
        "tier": row.get("tier"),
        "docs_url": row.get("docs_url"),
        "installed": bool(row.get("present")),
        "connected": bool(registrations),
        "registrations": [
            {
                "method": str(r.get("method", "")),
                "scope": str(r.get("scope", "")),
                "config_path": str(r.get("config_path", "")),
            }
            for r in registrations
        ],
        "config_files": files if isinstance(files, dict) else {},
        "first_call_at": first,
        "last_call_at": last,
    }


def _check(checks: list[dict[str, Any]], check_id: str) -> dict[str, Any] | None:
    return next((c for c in checks if c["id"] == check_id), None)


def _sync(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """The post-commit hook, from doctor's ``post-commit-hook`` row."""
    row = _check(checks, "post-commit-hook")
    if row is None:
        return {"installed": None, "detail": ""}
    detail = row["detail"]
    installed: bool | None = None
    if detail.startswith("installed"):
        installed = True
    elif detail.startswith("not installed"):
        installed = False
    return {"installed": installed, "detail": detail}


def _rewrite_hook(checks: list[dict[str, Any]], agents: list[dict[str, Any]]) -> dict[str, Any]:
    """The distill rewrite hook; ``not_applicable`` when Claude Code is not on this machine."""
    row = _check(checks, "distill-rewrite-hook")
    detail = row["detail"] if row else ""
    claude = next((a for a in agents if a["id"] == "claude-code"), None)
    if detail.startswith("installed"):
        state = "installed"
    elif claude is not None and not (claude["installed"] or claude["connected"]):
        state = "not_applicable"
    elif row is not None:
        state = "not_installed"
    else:
        state = "unknown"
    return {"state": state, "detail": detail}
