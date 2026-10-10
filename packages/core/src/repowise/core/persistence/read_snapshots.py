"""Read views built at index or update time, kept only while their inputs hold.

A view the read path would otherwise rebuild in every new process (the Fix
first queue, the next-actions view) is written once, by the writer that last
touched its stores, under a key naming the code that built it (repowise
version and the view's model version).

Freshness is kept on the write side: any session that writes a store a view
could read deletes every stored view in the same transaction
(:func:`_invalidate`), so a stored row is current by construction and a
missing one is built live. The rule fails closed: a table it does not know
is an input (:data:`UNRELATED_TABLES` lists the ones that are not), a write
in a savepoint that rolled back still invalidates, and a write to one
repository drops every repository's rows in a shared store. Readers never
write. The builders read only the stores; settings that shape them
(``.repowise/health-rules.json``, the exclude spec, scope) reach a view
through the store writes they cause.

Ceilings: SQLite serialises writers, so it is safe; on Postgres under READ
COMMITTED a writer building while another session commits an input write
can store one stale build, which the next input write drops. Writes that
bypass a ``Session`` (``engine.connect()`` / ``engine.begin()`` callers, today
only FTS and schema setup) and processes that never import
``persistence.database`` (an older repowise against the same store) do not
invalidate; the version in the key covers an upgrade, not a downgrade
followed by a return.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from repowise.core import __version__

from .models import ReadSnapshot

logger = logging.getLogger(__name__)

#: Tables no stored view reads: writing them leaves the views current. Any
#: other table, including one added later, counts as an input. Person-level
#: answers (``action_states``, ``set_action_state``) are applied on every
#: read, so they are not inputs.
UNRELATED_TABLES = frozenset(
    {
        "action_states",
        "answer_cache",
        "chat_messages",
        "conversations",
        "doc_drift_references",
        "external_systems",
        "generation_jobs",
        "git_commit_health_deltas",
        "git_function_blame",
        "graph_node_membership",
        "health_snapshots",
        "kg_node_meta",
        "kg_project_meta",
        "knowledge_graph_layers",
        "knowledge_graph_tour_steps",
        "llm_costs",
        "performance_summaries",
        "pipeline_jobs",
        "read_snapshots",
        "refactoring_summaries",
        "repositories",
        "webhook_events",
        "wiki_page_versions",
        "wiki_pages",
        "wiki_symbols",
    }
)

#: ``session.info`` flag: this transaction wrote a store a view reads.
_STALE = "read_snapshots_stale"

# Fail-safe: any text statement that is not a plain read counts as a write,
# and one whose target cannot be named counts as a write to an input.
_COMMENTS = re.compile(r"^(?:\s+|--[^\n]*\n?|/\*.*?\*/)+", re.DOTALL)
_TEXT_READ = re.compile(r"(?:select|pragma|explain)\b", re.IGNORECASE)
_TEXT_TARGET = re.compile(
    r"(?:insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from)"
    r"\s+(?:[\"`\[]?\w+[\"`\]]?\.)?[\"`\[]?(\w+)",
    re.IGNORECASE,
)


def _written_table(statement: Any) -> str | None:
    """The table a write statement targets; ``""`` for a write whose table
    cannot be read (counted as an input); ``None`` for a read."""
    if getattr(statement, "is_dml", False):
        return getattr(getattr(statement, "table", None), "name", "")
    sql = getattr(statement, "text", None)
    if not isinstance(sql, str):
        return None
    sql = _COMMENTS.sub("", sql, count=1)
    if _TEXT_READ.match(sql):
        return None
    found = _TEXT_TARGET.match(sql)
    return found.group(1).lower() if found else ""


@event.listens_for(Session, "do_orm_execute")
def _note_statement(state: Any) -> None:
    table = _written_table(state.statement)
    if table is not None and table not in UNRELATED_TABLES:
        state.session.info[_STALE] = True


def _pending_input(session: Session) -> bool:
    return any(
        getattr(obj, "__tablename__", "") not in UNRELATED_TABLES
        for obj in (*session.new, *session.dirty, *session.deleted)
    )


@event.listens_for(Session, "before_flush")
def _note_flush(session: Session, _context: Any, _instances: Any) -> None:
    if _pending_input(session):
        session.info[_STALE] = True


# ``commit`` runs this before its own final flush, so objects still pending
# then are checked here rather than by the flush hook.
@event.listens_for(Session, "before_commit")
def _invalidate(session: Session) -> None:
    """Drop the stored views in the transaction that changed their inputs."""
    if session.in_nested_transaction():
        return  # a savepoint release is not the commit; the outer one decides
    if not stale(session):
        return
    try:
        with session.begin_nested():
            session.execute(delete(ReadSnapshot))
    except Exception as exc:  # a store from before the table holds no views
        logger.debug("read snapshots not cleared: %s", exc)
    # Popped after the delete: the savepoint's autoflush can set it again.
    session.info.pop(_STALE, None)


def stale(session: Session) -> bool:
    """Whether this transaction wrote, or is about to write, a view's input."""
    return bool(session.info.get(_STALE)) or _pending_input(session)


async def mark_current(session: AsyncSession) -> None:
    """The views just written reflect every write this transaction made."""
    await session.flush()
    session.info.pop(_STALE, None)


def snapshot_key(*parts: Any) -> str:
    """The stored form of a key: the repowise version first, then ``parts``.

    Ceiling: a development checkout keeps one version across commits, so a
    builder change there is served stale until a store write or an update
    drops the row; a release changes the version.
    """
    return json.dumps([__version__, *parts], default=str)


async def read_snapshot(session: AsyncSession, repo_id: str, kind: str, key: str) -> Any | None:
    """The stored payload for ``kind`` when it was built under ``key``.

    Any failure is a miss and the caller builds live: a store from before
    this table, a row another build wrote, a payload that does not parse.
    The savepoint keeps that failure off the caller's session.
    """
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    select(ReadSnapshot.key, ReadSnapshot.payload_json).where(
                        ReadSnapshot.repository_id == repo_id, ReadSnapshot.kind == kind
                    )
                )
            ).first()
        if row is None or row.key != key:
            return None
        return json.loads(row.payload_json)
    except Exception as exc:  # build live instead
        logger.debug("read_snapshot %s unavailable: %s", kind, exc)
        return None


async def refresh_snapshot(
    session: AsyncSession,
    repo_id: str,
    kind: str,
    key: str,
    build: Callable[[], Awaitable[Any]],
) -> bool:
    """Store ``await build()`` (JSON-ready) as the ``kind`` row under ``key``,
    unless the row already holds that key and this transaction has written
    no input (such a row would be dropped at commit). Returns whether it
    wrote.

    Two writers racing on one row (two updates of one store) can fail on the
    primary key; the caller treats any failure as "no snapshot", which a
    reader answers by building live.
    """
    row = await session.get(ReadSnapshot, (repo_id, kind))
    if row is not None and row.key == key and not stale(session.sync_session):
        return False
    text = json.dumps(await build(), ensure_ascii=False)
    if row is None:
        session.add(ReadSnapshot(repository_id=repo_id, kind=kind, key=key, payload_json=text))
    else:
        row.key, row.payload_json = key, text
    await session.flush()
    return True


@cache
def _adapter(tp: type) -> Any:
    # pydantic ships with repowise; imported here so opening a store stays cheap.
    from pydantic import TypeAdapter

    return TypeAdapter(tp)


def decode_or_none(tp: type, value: Any) -> Any | None:
    """``value`` (``dataclasses.asdict`` through JSON) back as a ``tp``, or
    ``None`` for a payload that does not fit (an older or newer build's)."""
    from pydantic import ValidationError

    try:
        return _adapter(tp).validate_python(value)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.info("stored %s no longer fits the model: %s", getattr(tp, "__name__", tp), exc)
        return None


__all__ = [
    "UNRELATED_TABLES",
    "decode_or_none",
    "mark_current",
    "read_snapshot",
    "refresh_snapshot",
    "snapshot_key",
    "stale",
]
