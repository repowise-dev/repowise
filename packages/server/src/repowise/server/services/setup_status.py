"""The Setup page's one payload: index, agents, hooks, coverage, model, checks.

Agent, hook and doctor state come from the CLI's own JSON (see
:mod:`.setup_cli`); the rest is read here from the index, the savings ledger,
the coverage discovery and the provider settings the other routes already use.

The index and stored-coverage reads hit the database on every request. The
rest (the CLI runs and the file reads, a few seconds) is a cached *probe* per
repo: a fresh one is served as is, an older one is served at once while a new
one builds in the background, and only a repo with none waits for the build.
The probe runs ``doctor --quick``; the full doctor (it starts the MCP server and
reconciles the stores, tens of seconds) runs in the background after it, and
its extra rows join the checks once it lands. Every write drops both.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphNode, Repository
from repowise.server.services import setup_cli

logger = logging.getLogger(__name__)

#: A probe this young is served without a rebuild; an older one is served
#: while a rebuild runs, up to the stale limit. The page fetches once and does
#: not refetch, so past that limit a load waits for the rebuild (seconds).
SETUP_CACHE_SECONDS = 60.0
SETUP_STALE_SECONDS = 300.0
#: How often the full doctor runs again for the same repo.
FULL_CHECK_SECONDS = 300.0

#: Bounds on the lists the payload carries.
_PARSE_ERROR_FILES_LIMIT = 50
_DISCOVERED_REPORTS_LIMIT = 10


@dataclass(frozen=True)
class _Probe:
    """The CLI answers and file reads behind one payload."""

    built_at: float
    generated_at: str
    doctor: dict | None
    agents: dict | None
    local: tuple[Any, Any, tuple[bool, dict[str, Any]]]


_probes: dict[str, _Probe] = {}
#: Rows from the last full doctor, by repo: ``(monotonic time, rows)``. A
#: failed run keeps the old rows with a new time, so it is not retried on
#: every rebuild.
_full_checks: dict[str, tuple[float, list[dict[str, Any]]]] = {}
#: Builds in flight, by ``(kind, repo_id)``, so concurrent loads share one.
_inflight: dict[tuple[str, str], asyncio.Task] = {}
#: Strong references until each task ends; the loop keeps only weak ones.
_running: set[asyncio.Task] = set()
#: Bumped by every write, so a build that started before it is not cached.
_generation = 0


def invalidate(repo_id: str | None = None) -> None:
    """Drop the cached answers for *repo_id*, or every one when ``None``.

    A build already running started before the write: it finishes uncached,
    and the next request starts a new one. The full doctor's rows stay until
    the next full run replaces them, so a failing row does not blink out for
    the tens of seconds that run takes; they are only marked due.
    """
    global _generation
    _generation += 1
    if repo_id is None:
        _probes.clear()
    else:
        _probes.pop(repo_id, None)
    for key, (_at, rows) in list(_full_checks.items()):
        if repo_id is None or key == repo_id:
            _full_checks[key] = (float("-inf"), rows)
    for key in [k for k in _inflight if repo_id is None or k[1] == repo_id]:
        del _inflight[key]


async def setup_payload(session: AsyncSession, repo: Repository) -> dict[str, Any]:
    """The payload for *repo*; only a repo with no probe yet waits for one."""
    probe = await _current_probe(repo.id, Path(repo.local_path))
    index = await _index_section(session, repo)
    coverage_stored = await _coverage_ingested_at(session, repo.id)
    full = _full_checks.get(repo.id)
    return _assemble(repo.id, probe, index, coverage_stored, full[1] if full else None)


def _start(kind: str, repo_id: str, make: Callable[[], Coroutine[Any, Any, Any]]) -> asyncio.Task:
    """The build of *kind* for *repo_id* in flight, started if there is none."""
    key = (kind, repo_id)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(make())
        _inflight[key] = task
        _running.add(task)
        task.add_done_callback(lambda t: _finished(key, t))
    return task


def _finished(key: tuple[str, str], task: asyncio.Task) -> None:
    _running.discard(task)
    if _inflight.get(key) is task:
        del _inflight[key]
    if not task.cancelled() and task.exception() is not None:
        logger.warning("setup: %s build for %s failed", *key, exc_info=task.exception())


async def _current_probe(repo_id: str, repo_path: Path) -> _Probe:
    def build() -> Coroutine[Any, Any, _Probe]:
        return _build_probe(repo_id, repo_path)

    hit = _probes.get(repo_id)
    age = time.monotonic() - hit.built_at if hit else None
    if age is None or age >= SETUP_STALE_SECONDS:
        # Shielded: a request that gives up must not cancel the build that
        # the other loads are waiting on.
        return await asyncio.shield(_start("probe", repo_id, build))
    if age >= SETUP_CACHE_SECONDS:
        _start("probe", repo_id, build)
    return hit


async def _build_probe(repo_id: str, repo_path: Path) -> _Probe:
    started = _generation
    doctor, agents, local = await asyncio.gather(
        asyncio.to_thread(setup_cli.doctor_json, repo_path, quick=True),
        asyncio.to_thread(setup_cli.agents_json, repo_path),
        asyncio.to_thread(_local_sections, repo_id, repo_path),
    )
    probe = _Probe(time.monotonic(), datetime.now(UTC).isoformat(), doctor, agents, local)
    if started == _generation:
        _probes[repo_id] = probe
        # After the probe, not beside it: on a cold load the two would
        # compete for the same CPU and the page would wait longer.
        full = _full_checks.get(repo_id)
        if full is None or time.monotonic() - full[0] >= FULL_CHECK_SECONDS:
            _start("full", repo_id, lambda: _build_full_checks(repo_id, repo_path))
    return probe


async def _build_full_checks(repo_id: str, repo_path: Path) -> None:
    started = _generation
    doctor = await asyncio.to_thread(setup_cli.doctor_json, repo_path)
    if started != _generation:
        return
    if doctor is None:
        logger.warning("setup: the full doctor run for %s gave no answer", repo_path)
        previous = _full_checks.get(repo_id)
        _full_checks[repo_id] = (time.monotonic(), previous[1] if previous else [])
        return
    _full_checks[repo_id] = (time.monotonic(), _checks(doctor))


def _assemble(
    repo_id: str,
    probe: _Probe,
    index: dict[str, Any],
    coverage_stored: tuple[bool, str | None],
    full_checks: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    checks = _checks(probe.doctor)
    if full_checks:
        # Only the rows the quick run skips; the quick rows are as new or newer.
        quick_ids = {c["id"] for c in checks}
        checks += [c for c in full_checks if c["id"] not in quick_ids]
    agent_calls, coverage_files, (model_ok, model) = probe.local
    agent_rows, calls = _agents(probe.agents, agent_calls or {})
    ingest_ok, coverage_ingested_at = coverage_stored
    return {
        "repo_id": repo_id,
        "generated_at": probe.generated_at,
        "index": index,
        "agents_available": probe.agents is not None,
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
        "checks_available": probe.doctor is not None or bool(full_checks),
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
