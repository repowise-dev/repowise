"""The ``measured`` block: changed functions a measurement speaks for, and how to check each."""

from __future__ import annotations

import asyncio
import subprocess
from functools import partial
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

# Named for the tool module so log records keep their source.
log = structlog.get_logger("repowise.server.mcp_server.tool_change_risk")


async def measured_block(
    session_factory: Any, changed: dict[str, set[int]], repo: str | None = None
) -> dict[str, Any] | None:
    """Change risk's ``measured`` block for *changed* (``{path: new-side lines}``),
    or ``None``: nothing measured changed, no index, or an index the read fails
    on (one that predates the evidence tables). Absent rather than empty, so a
    change that touches nothing measured costs no bytes. Never moves the score.
    """
    from repowise.core.evidence.change_measured import measured_block as fold
    from repowise.core.persistence.crud import measured_in_change
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    if session_factory is None or not changed:
        return None
    try:
        async with get_session(session_factory) as session:
            repo_id = (await _get_repo(session, repo)).id
            found = await measured_in_change(session, repo_id, changed)
    except (LookupError, SQLAlchemyError) as exc:
        log.debug("measured_block_failed", error=f"{type(exc).__name__}: {exc}")
        return None
    return fold(found)


async def scored_change_measured(
    repo_path: Any, session_factory: Any, revspec: str | None, scored: Any, repo: str | None = None
) -> dict[str, Any] | None:
    """:func:`measured_block` for the change *scored* (a ``ChangeRiskResult``) holds,
    over the files its score counted, so ``repowise risk`` names the functions
    ``get_change_risk`` names for the same change."""
    from repowise.core.analysis.changed_lines import changed_lines

    counted = {path for path, _ in scored.features.file_churn}
    if session_factory is None or not counted:
        return None
    try:
        changed, _ = await asyncio.to_thread(
            partial(
                changed_lines,
                str(repo_path),
                revspec or "HEAD",
                working_tree=scored.working_tree,
                skip_untracked=scored.riskignore_excludes + scored.request_excludes,
            )
        )
    except (ValueError, subprocess.SubprocessError, OSError):  # git could not read it
        return None
    return await measured_block(
        session_factory, {p: lines for p, lines in changed.items() if p in counted}, repo
    )
