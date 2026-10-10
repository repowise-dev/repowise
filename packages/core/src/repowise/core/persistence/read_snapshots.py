"""Read views built at index or update time, kept only while their inputs hold.

A view the read path would otherwise rebuild in every new process (the Fix
first queue, the next-actions view) is written once, by the writer that last
touched its stores, under a key naming the code that built it (repowise
version and the view's model version).

Freshness is kept on the write side rather than measured on read: any session
that writes a store a view could read deletes every stored view in the same
transaction (:func:`_invalidate`), so a stored row is current by
construction. Measuring it instead (newest write and row count per store) cost
about a second per read on a 21k-file repository, mostly over the graph
tables, whose upserts carry no write time, and would still miss an in-place
update. The rule is conservative on purpose: a table it does not know is
treated as an input (:data:`UNRELATED_TABLES` lists the ones that are not),
a write in a savepoint that rolled back still invalidates, and a write to one
repository drops every repository's rows in a shared store. A view missing
for any of these reasons is built live, exactly as before. Readers never write.

The builders read only the stores. Settings that shape what is stored
(``.repowise/health-rules.json``, the exclude spec, scope) are applied when
the stores are written, so a change to them reaches a view through that
write. Ceiling: a write made by a process that never imported this module (an
older repowise against the same store) does not invalidate; the version in the
key covers an upgrade, not a downgrade followed by a return.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import fields, is_dataclass
from functools import cache
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from repowise.core import __version__

from .models import ReadSnapshot

logger = logging.getLogger(__name__)

#: Tables no stored view reads: writing them leaves the views current. Any
#: other table, including one added later, counts as an input.
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

_TEXT_WRITE = re.compile(
    r"^\s*(?:insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update(?:\s+or\s+\w+)?|delete\s+from)"
    r"\s+[\"`\[]?(\w+)",
    re.IGNORECASE,
)
_TEXT_VERB = re.compile(r"^\s*(?:insert|replace|update|delete)\b", re.IGNORECASE)


def _written_table(statement: Any) -> str | None:
    """The table a write statement targets; ``""`` for a write whose table
    cannot be read (counted as an input); ``None`` for a read."""
    if getattr(statement, "is_dml", False):
        return getattr(getattr(statement, "table", None), "name", "")
    sql = getattr(statement, "text", None)
    if isinstance(sql, str) and _TEXT_VERB.match(sql):
        found = _TEXT_WRITE.match(sql)
        return found.group(1).lower() if found else ""
    return None


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
    if not (session.info.pop(_STALE, False) or _pending_input(session)):
        return
    try:
        with session.begin_nested():
            session.execute(delete(ReadSnapshot))
    except Exception as exc:  # a store from before the table holds no views
        logger.debug("read snapshots not cleared: %s", exc)


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
    this table, or a row another build wrote. The savepoint keeps that
    failure off the caller's session.
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
    except Exception as exc:  # build live instead
        logger.debug("read_snapshot %s unavailable: %s", kind, exc)
        return None
    if row is None or row.key != key:
        return None
    return json.loads(row.payload_json)


async def refresh_snapshot(
    session: AsyncSession,
    repo_id: str,
    kind: str,
    key: str,
    build: Callable[[], Awaitable[Any]],
) -> bool:
    """Store ``await build()`` (JSON-ready) as the ``kind`` row under ``key``,
    unless the row already holds that key. Returns whether it wrote.

    Two writers racing on one row (two updates of one store) can fail on the
    primary key; the caller treats any failure as "no snapshot", which a
    reader answers by building live.
    """
    row = await session.get(ReadSnapshot, (repo_id, kind))
    if row is not None and row.key == key:
        return False
    text = json.dumps(await build(), ensure_ascii=False)
    if row is None:
        session.add(ReadSnapshot(repository_id=repo_id, kind=kind, key=key, payload_json=text))
    else:
        row.key, row.payload_json = key, text
    await session.flush()
    return True


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def decode(tp: Any, value: Any) -> Any:
    """``value`` (``dataclasses.asdict`` through JSON) back as type ``tp``.

    Frozen dataclasses, tuples (``tuple[X, ...]`` and fixed ``tuple[A, B]``),
    optionals and unions (a mapping goes to the union's dataclass member when
    it has one), and plain JSON values (``dict`` / ``Mapping`` / ``Literal`` /
    ``Any``), which come back as they are. A payload that does not fit the
    type raises; :func:`decode_or_none` turns that into a miss.
    """
    if value is None:
        return None
    if is_dataclass(tp):
        hints = _hints(tp)
        return tp(**{f.name: decode(hints[f.name], value[f.name]) for f in fields(tp) if f.init})
    origin = get_origin(tp)
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(args[0], v) for v in value)
        if len(args) != len(value):
            raise ValueError(f"expected {len(args)} values, got {len(value)}")
        return tuple(decode(a, v) for a, v in zip(args, value, strict=True))
    if origin in (Union, UnionType):
        members = [a for a in get_args(tp) if a is not type(None)]
        classes = [m for m in members if is_dataclass(m)]
        if isinstance(value, dict) and classes:
            return decode(classes[0], value)
        return decode(members[0], value) if len(members) == 1 else value
    return value


def decode_or_none(tp: Any, value: Any) -> Any | None:
    """:func:`decode`, or ``None`` for a payload an older or newer build wrote."""
    try:
        return decode(tp, value)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.info("stored %s no longer fits the model: %s", getattr(tp, "__name__", tp), exc)
        return None


__all__ = [
    "UNRELATED_TABLES",
    "decode",
    "decode_or_none",
    "mark_current",
    "read_snapshot",
    "refresh_snapshot",
    "snapshot_key",
]
