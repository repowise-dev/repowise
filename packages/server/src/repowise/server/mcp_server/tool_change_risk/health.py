"""Health delta: the comparison service, stored-finding links and evidence."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.change_health.service import (
    ChangeHealthDeltaService,
    DeltaRequest,
)
from repowise.core.analysis.change_risk.features import revspec_head

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._failure_shield import (
    is_stale_server_import,
    stale_server_notice,
)

# Named for the tool module so log records keep their source.
log = structlog.get_logger("repowise.server.mcp_server.tool_change_risk")


async def _evidence_block(
    ctx: Any,
    revspec: str | None,
    result: Any,
    delta: Any,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    *,
    scoped: bool,
) -> dict[str, Any]:
    """The change's evidence checklist, reusing the score and delta already computed.

    A revspec naming a stored change, asked with no filters of the caller's
    own, returns that change's kept record (building and keeping it when
    there is none), so this and ``repowise changes show`` serve the same one.
    """
    from repowise.core.changes.collect import (
        NO_INDEX,
        change_evidence,
        evidence_for_revspec,
        stored_change_at,
    )
    from repowise.core.persistence.database import get_session

    kwargs: dict[str, Any] = {
        "risk": result,
        "health": delta,
        "extensions": extensions,
        "exclude_patterns": exclude_patterns,
    }
    no_index = NO_INDEX
    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is not None:
        try:
            async with get_session(session_factory) as session:
                repo = await _pkg._get_repo(session)
                row = await stored_change_at(session, repo.id, ctx.path, revspec)
                if row is not None and not scoped and not result.riskignore_excludes:
                    evidence = await change_evidence(
                        session, repo.id, ctx.path, row, risk=result, health=delta
                    )
                else:
                    evidence = await evidence_for_revspec(
                        session, repo.id, ctx.path, revspec, row=row, **kwargs
                    )
                return evidence.to_dict()
        except (LookupError, SQLAlchemyError) as exc:
            no_index = f"the index could not be read: {type(exc).__name__}: {exc}"
    evidence = await evidence_for_revspec(None, "", ctx.path, revspec, no_index=no_index, **kwargs)
    return evidence.to_dict()


#: One service per repository, so its comparison cache and stampede guard
#: outlive a single call. LRU-bounded: a long-lived server can see many
#: worktrees and workspace members over its lifetime.
_DELTA_SERVICES: OrderedDict[str, ChangeHealthDeltaService] = OrderedDict()
_DELTA_SERVICE_CAPACITY = 8
_DELTA_SERVICES_LOCK = threading.Lock()


def _delta_service(repo_path: str) -> ChangeHealthDeltaService:
    with _DELTA_SERVICES_LOCK:
        service = _DELTA_SERVICES.get(repo_path)
        if service is not None:
            _DELTA_SERVICES.move_to_end(repo_path)
            return service
    # Built outside the lock: the fingerprint reads config off disk.
    from repowise.core.repo_config import health_rules_fingerprint

    service = ChangeHealthDeltaService(
        repo_path=repo_path, rules_fingerprint=health_rules_fingerprint(repo_path)
    )
    with _DELTA_SERVICES_LOCK:
        existing = _DELTA_SERVICES.get(repo_path)
        if existing is not None:
            _DELTA_SERVICES.move_to_end(repo_path)
            return existing
        _DELTA_SERVICES[repo_path] = service
        while len(_DELTA_SERVICES) > _DELTA_SERVICE_CAPACITY:
            _DELTA_SERVICES.popitem(last=False)
    return service


async def _index_dependents(ctx: Any) -> dict[str, int] | None:
    """The index's dependents count per file, or ``None`` with no readable index."""
    from repowise.core.analysis.change_health.index_context import index_dependents
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            repository = await _pkg._get_repo(session)
            return await index_dependents(session, repository.id)
    except Exception as exc:
        # An input that sharpens the delta, never a reason to fail the call.
        log.warning("change_health_index_dependents_failed", error=str(exc))
        return None


async def _delta_with_index(
    ctx: Any,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_paths: tuple[str, ...],
) -> Any:
    """Read the index's dependents, then compare off the event loop."""
    dependents = await _index_dependents(ctx)
    return await asyncio.to_thread(
        _pkg._compare_health,
        str(ctx.path),
        revspec,
        extensions,
        exclude_patterns,
        include_paths,
        dependents,
    )


def _compare_health(
    repo_path: str,
    revspec: str | None,
    extensions: tuple[str, ...],
    exclude_patterns: tuple[str, ...],
    include_paths: tuple[str, ...] = (),
    dependents: dict[str, int] | None = None,
) -> Any:
    """Run the comparison, degrading to an explicit unavailable state."""
    from repowise.core.analysis.change_health.models import ChangeHealthDelta

    try:
        return _pkg._delta_service(repo_path).compare(
            DeltaRequest(
                repo_path, revspec, extensions, exclude_patterns, include_paths, dependents
            )
        )
    except Exception as exc:
        log.warning("change_health_comparison_failed", revspec=revspec, error=str(exc))
        explanation = (
            stale_server_notice(exc)
            if is_stale_server_import(exc)
            else f"Health comparison failed: {exc}"
        )
        return ChangeHealthDelta(
            status="unavailable",
            explanation=explanation,
            base=None,
            head=None,
            comparison_basis="not_compared",
            fingerprint=None,
        )


async def _attach_health_references(ctx: Any, delta: Any) -> None:
    """Point a change finding at its stored twin, when it has one exactly.

    Only an exact match earns the canonical reference: same file, marker,
    symbol, and span. Anything looser would hand an agent a pointer to a
    different finding, and most change findings have no stored twin at all
    because uncommitted and historical work is never persisted.
    """
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import HealthFinding

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None or not delta.findings:
        return
    paths = sorted({f.path for f in delta.findings})
    try:
        async with get_session(session_factory) as session:
            repository = await _pkg._get_repo(session)
            rows = (
                (
                    await session.execute(
                        select(HealthFinding).where(
                            HealthFinding.repository_id == repository.id,
                            HealthFinding.file_path.in_(paths),
                        )
                    )
                )
                .scalars()
                .all()
            )
    except (LookupError, SQLAlchemyError):
        # No repository row is "no index", as in the sibling enrichments.
        return
    if not rows:
        return
    from repowise.server.mcp_server.tool_health import _health_finding_id

    alias = getattr(ctx, "alias", None) or repository.name
    stored = {
        (r.file_path, r.biomarker_type, r.function_name or "", r.line_start, r.line_end): r
        for r in rows
    }
    for finding in delta.findings:
        row = stored.get(
            (
                finding.path,
                finding.biomarker_type,
                finding.symbol or "",
                finding.line_start,
                finding.line_end,
            )
        )
        if row is None:
            continue
        finding.health_reference = {
            "tool": "get_health",
            "arguments": {"finding_id": _health_finding_id(row, alias)},
        }


def _scored_repo(path: str, revspec: str | None, working_tree: bool) -> dict[str, Any]:
    """The checkout the change was read in and the commit its head resolved to.

    ``HEAD`` or a branch name resolves in the served checkout, not in the
    caller's worktree, so naming both makes a review of the wrong tree visible.
    Uncommitted work is marked, so its ``head`` does not read as the change.
    """
    from repowise.core import git_refs

    head = git_refs.resolve(path, revspec_head(revspec))[:12] or None
    return {"root": path, "head": head, **({"working_tree": True} if working_tree else {})}


async def _repository(ctx: Any) -> Any | None:
    """The indexed repository row, or ``None`` without an index.

    Only ``_meta`` needs it: the per-file blocks below resolve their own repo id
    inside the session they query in.
    """
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            return await _pkg._get_repo(session)
    except (LookupError, SQLAlchemyError):
        return None
