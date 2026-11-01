"""Point at get_why where an accepted decision governs what a call touches.

One line, only when a decision binds a named file, so a response about a file
nobody ruled on stays byte for byte the same. The server instructions carry the
repository-level half: where the decisions sit, or what get_why reads when
there are none.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
from collections import Counter
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from repowise.core.analysis.next_call import render_call
from repowise.core.persistence.crud.authority import governing_decisions_by_file
from repowise.core.persistence.database import (
    create_engine,
    get_configured_db_url,
    get_repo_db_path,
    get_session,
    resolve_db_url,
)
from repowise.core.persistence.models import DecisionAcceptance, DecisionRecord, Repository
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _get_repo,
    _is_workspace_mode,
    escape_like,
)

#: Governed files a pointer names in its call. More are counted, not listed.
_MAX_POINTER_FILES = 3
#: Areas the server instructions name, and the line's length: it shares the
#: instructions' cap with the static lines and the workspace tool lines.
_MAX_LINE_CHARS = 100
_MAX_AREAS = 3


async def why_pointer(ctx: Any, paths: Iterable[str]) -> str | None:
    """``Decision "<title>" governs <file>: get_why(targets=[...])``, or ``None``.

    *ctx* is the served repository's context; *paths* are the call's targets,
    a ``path::Symbol`` target counting as its file. In a workspace the call
    names the repository. Best-effort: a store that cannot answer adds nothing.
    One query when no accepted record names a target, the usual case; the
    governing read (currency per record) runs only when one does.
    """
    files = list(dict.fromkeys(str(p).split("::", 1)[0] for p in paths if p))
    session_factory = getattr(ctx, "session_factory", None)
    if not files or session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            if not await _named_by_an_accepted_record(session, files):
                return None
            repository = await _get_repo(session)
            by_file = await governing_decisions_by_file(session, repository.id)
    except (SQLAlchemyError, LookupError):
        return None
    governed = [f for f in files if by_file.get(f)]
    if not governed:
        return None
    return _pointer_line(by_file, governed, getattr(ctx, "alias", None))


async def _named_by_an_accepted_record(session: Any, files: list[str]) -> bool:
    """Whether any accepted record's file list spells one of *files*.

    A superset of "governs" (currency and scope basis are not read here), so a
    False is final and a True is checked by the full governing read.
    """
    named = or_(
        *(
            DecisionRecord.affected_files_json.like(
                f"%{escape_like(json.dumps(f))}%", escape=LIKE_ESCAPE
            )
            for f in files
        )
    )
    accepted = select(DecisionAcceptance.decision_id).where(
        DecisionAcceptance.decision_id == DecisionRecord.id
    )
    query = select(DecisionRecord.id).where(named, accepted.exists()).limit(1)
    return (await session.execute(query)).first() is not None


async def attach_why_pointer(response: dict[str, Any], ctx: Any, paths: Iterable[str]) -> None:
    """Set ``response["governing_decision"]`` when a decision governs one of *paths*."""
    pointer = await why_pointer(ctx, paths)
    if pointer:
        response["governing_decision"] = pointer


def _pointer_line(
    by_file: dict[str, list[tuple[str, str]]], governed: list[str], alias: str | None
) -> str:
    first_id, title = by_file[governed[0]][0]
    others = {did for f in governed for did, _ in by_file[f]} - {first_id}
    more = f" (+{len(others)} more)" if others else ""
    args: dict[str, Any] = {"targets": governed[:_MAX_POINTER_FILES]}
    if alias and _is_workspace_mode():
        args["repo"] = alias
    return f'Decision "{title}"{more} governs {governed[0]}: {render_call("get_why", args)}'


def decisions_line(repo_path: str | None) -> str | None:
    """The server-instructions line for *repo_path*'s index, or ``None``.

    Read before the transport starts: the handshake's instructions are fixed
    once the server runs. ``None`` when there is no index or it cannot be read,
    so a store error never reads as "no decisions".
    """
    if not repo_path or get_configured_db_url() is not None:
        return None
    if not get_repo_db_path(repo_path).exists():
        return None
    if _loop_running():
        return None  # built from async code: no blocking read on its loop
    try:
        return asyncio.run(_read_line(resolve_db_url(repo_path)))
    except Exception:  # an optional line must never stop the server starting
        return None


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


async def _read_line(db_url: str) -> str | None:
    engine = create_engine(db_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with get_session(session_factory) as session:
            # A store holding several repositories (a workspace root) has no
            # single answer to give.
            if (await session.execute(select(func.count(Repository.id)))).scalar_one() != 1:
                return None
        return await governed_areas_line(session_factory)
    finally:
        await engine.dispose()


async def governed_areas_line(session_factory: Any) -> str | None:
    """The server-instructions line on what get_why holds for this repository."""
    try:
        async with get_session(session_factory) as session:
            repository = await _get_repo(session)
            by_file = await governing_decisions_by_file(session, repository.id)
    except (SQLAlchemyError, LookupError, OSError):
        return None
    decisions = {did for rows in by_file.values() for did, _ in rows}
    if not decisions:
        return "- No decisions recorded yet: get_why reads git history and code comments."
    per_dir: Counter[str] = Counter()
    for path, rows in by_file.items():
        per_dir[posixpath.dirname(path) or "."] += len(rows)
    names = [f"{posixpath.basename(d) or d}/" for d, _ in per_dir.most_common(_MAX_AREAS)]
    # Fewer areas until the line fits; none when one name alone is too long.
    for n in range(len(names), 0, -1):
        line = (
            f"- {len(decisions)} decisions govern files, most in {', '.join(names[:n])}: "
            "get_why(targets=[file]) first."
        )
        if len(line) <= _MAX_LINE_CHARS:
            return line
    return f"- {len(decisions)} decisions govern files here: get_why(targets=[file]) first."
