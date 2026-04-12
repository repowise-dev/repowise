"""``annotate_file`` — attach human notes to a wiki page.

Notes survive LLM-driven re-generation and appear in ``get_context`` responses.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server._helpers import (
    _get_repo,
    _resolve_repo_context,
    _unsupported_repo_all,
)
from repowise.server.mcp_server._server import mcp


@mcp.tool()
async def annotate_file(
    target: str,
    notes: str,
    repo: str | None = None,
) -> dict[str, Any]:
    """Attach human-authored notes to a file's wiki page.

    Notes persist across re-indexing — use them for rationale, known issues,
    or context that the LLM shouldn't overwrite.  Pass an empty string to
    clear existing notes.

    Args:
        target: file path (e.g. "src/auth/service.py").
        notes: the note text to attach (empty string to clear).
        repo: repository identifier; usually omitted.
    """
    if repo == "all":
        return _unsupported_repo_all("annotate_file")
    ctx = await _resolve_repo_context(repo)

    from repowise.core.persistence.models import Page

    async with get_session(ctx.session_factory) as session:
        await _get_repo(session)  # validates repo exists

        # Try file_page first, then module_page
        page_id = f"file_page:{target}"
        result = await session.execute(select(Page).where(Page.id == page_id))
        page = result.scalar_one_or_none()

        if page is None:
            page_id = f"module_page:{target}"
            result = await session.execute(select(Page).where(Page.id == page_id))
            page = result.scalar_one_or_none()

        if page is None:
            return {
                "status": "error",
                "message": f"No wiki page found for '{target}'. Run 'repowise init' first.",
            }

        page.human_notes = notes if notes else None
        await session.flush()

        action = "cleared" if not notes else "updated"
        return {
            "status": "ok",
            "target": target,
            "action": action,
            "human_notes": page.human_notes,
        }
