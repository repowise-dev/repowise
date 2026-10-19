"""The committed-range change-risk cache: one scoring per range and index state.

A revspec names commits, so the score, the delta and every block are fixed
once the revisions, the request's filters, the index and the few live inputs
the blocks read (riskignore, repo config, coverage ingests, branch tips) are.
A repeat call or a ``finding_id`` drill-down is then served from here instead
of rerunning the comparison. Uncommitted work is never cached: its revspec is
``None`` and its content moves without any of those changing.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Scored ranges kept per process; each holds its delta and served payloads.
_CAPACITY = 16

_TRANSIENT = frozenset({"unavailable", "timeout"})


@dataclass
class Scored:
    """One scored range: its ref and delta, and each payload served for it.

    Callers only read ``delta``; it is shared, not copied.
    """

    ref: str
    delta: Any
    payloads: dict[str, tuple[dict, list[str] | None]] = field(default_factory=dict)


_SCORED: OrderedDict[str, Scored] = OrderedDict()


def lookup(key: str | None) -> Scored | None:
    if key is None:
        return None
    hit = _SCORED.get(key)
    if hit is not None:
        _SCORED.move_to_end(key)
    return hit


def remember(key: str | None, ref: str, delta: Any) -> Scored | None:
    """Keep *delta* for *key*, or ``None`` when the call is not cacheable."""
    # A failed or timed-out comparison may succeed next time, so it is not kept.
    if key is None or getattr(delta, "status", None) in _TRANSIENT:
        return None
    entry = _SCORED.get(key)
    if entry is None:
        entry = _SCORED[key] = Scored(ref, delta)
    _SCORED.move_to_end(key)
    while len(_SCORED) > _CAPACITY:
        _SCORED.popitem(last=False)
    return entry


def payload_key(include: list[str] | None) -> str:
    return repr(sorted(include or ()))


async def cache_key(
    ctx: Any,
    revspec: str | None,
    extensions: list[str] | None,
    exclude_patterns: list[str] | None,
    include_paths: tuple[str, ...],
    baseline: int,
) -> str | None:
    """The range's cache identity, or ``None`` when this call must not be cached."""
    from repowise.server.mcp_server import _state

    # Workspace mode joins other repositories' indexes into the tests block.
    # Ceiling: those members' index states are not in the key, so the cache
    # stays off there; adding each member's state key is the upgrade.
    if not revspec or _state._registry is not None:
        return None
    root = str(Path(ctx.path).resolve())
    live = await asyncio.to_thread(_live_inputs, root, revspec)
    if live is None:
        return None
    parts = (
        root,
        revspec,
        *live,
        repr(tuple(extensions or ())),
        repr(tuple(exclude_patterns or ())),
        repr(include_paths),
        str(baseline),
        await _index_inputs(ctx),
    )
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _live_inputs(root: str, revspec: str) -> tuple[str, ...] | None:
    """The resolved revisions and the checkout inputs the blocks read, or ``None``."""
    from repowise.core import git_refs
    from repowise.core.analysis.change_risk.service import riskignore_patterns
    from repowise.core.analysis.git_cli import split_revspec
    from repowise.core.repo_config import health_rules_fingerprint

    parts = split_revspec(revspec)
    sides = (parts[0], parts[2]) if parts else (revspec, f"{revspec}^1")
    shas = [git_refs.resolve(root, side) for side in sides]
    head = git_refs.resolve(root, "HEAD") or ""
    # A root commit has no parent; any other unresolvable side is git's error to report.
    if not shas[0] or (parts and not shas[1]):
        return None
    try:
        tips = subprocess.run(
            ["git", "for-each-ref", "--format=%(objectname) %(refname)"],
            cwd=root,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return (
        *shas,
        head,
        hashlib.sha256(tips).hexdigest(),
        repr(riskignore_patterns(root)),
        health_rules_fingerprint(root),
    )


async def _index_inputs(ctx: Any) -> str:
    """The index state and the newest coverage ingest; ``""`` without an index."""
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import CoverageIngest
    from repowise.server.mcp_server import tool_change_risk as _pkg
    from repowise.server.mcp_server._index_state import index_state_key

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return ""
    try:
        async with get_session(session_factory) as session:
            repository = await _pkg._get_repo(session)
            # An ingest writes no index stamp, so its own row is the signal.
            newest, count = (
                await session.execute(
                    select(func.max(CoverageIngest.ingested_at), func.count()).where(
                        CoverageIngest.repository_id == repository.id
                    )
                )
            ).one()
    except (LookupError, SQLAlchemyError):
        return ""
    return f"{index_state_key(repository)}|{newest or ''}|{count}"
