"""How often each decision was put in front of an agent, and when last.

The local twin of a hosted review's applied count, read from the hook ledger
(``injections`` in ``sessions.db``). A delivery is any row that showed a
decision to an agent:

* surface ``decision`` (or the pre-column ``''``): the session-start block and
  the edit notice, keyed on the record id;
* surface ``why``: a ``get_why`` reply that carried the record, keyed
  ``why:<id>`` with the id in ``node_id`` (the ledger keeps one row per
  session and key, and the decision surface already holds the plain id);
* surface ``upkeep``, category ``commit_notice``: the commit-time line, which
  names its records by id in ``node_id``.

Each counts once per session per moment: the ledger's own once-per-session gate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.sessions.staging import default_store_path

WHY_SURFACE = "why"
WHY_CATEGORY = "served"


def why_key(decision_id: str) -> str:
    """The ledger key of one ``get_why`` delivery of *decision_id*."""
    return f"{WHY_SURFACE}:{decision_id}"


def record_why_deliveries(
    repo_path: Path | str, session_id: str, decision_ids: list[str], *, now: float | None = None
) -> int:
    """Ledger one ``get_why`` delivery per record, once per session. Returns rows added.

    Settled on write (``evaluated = 1``): a reply the agent asked for carries no
    recommended action, so there is no outcome for a later pass to judge.
    """
    if not session_id or not decision_ids:
        return 0
    import time

    from repowise.core.sessions.staging import SessionStagingStore

    shown_at = time.time() if now is None else now
    store = SessionStagingStore.open_default(Path(repo_path))
    try:
        added = sum(
            store.add_settled_row(
                session_id=session_id,
                key=why_key(did),
                node_id=did,
                surface=WHY_SURFACE,
                category=WHY_CATEGORY,
                shown_at=shown_at,
            )
            for did in dict.fromkeys(decision_ids)
        )
        store.commit()
        return added
    finally:
        store.close()


@dataclass(frozen=True)
class Delivery:
    count: int
    last_at: datetime | None


def ledger_deliveries(repo_path: Path | str) -> list[tuple[str, float]] | None:
    """``(named id, shown_at)`` for every delivery row, or None when unreadable.

    None, never an empty list, when there is no ledger: nothing recorded is
    not the same as nothing delivered. A commit line contributes one entry per
    id it names; those may be the eight-character prefix a transcript replay
    recovers, so callers match by prefix as well.
    """
    path = default_store_path(Path(repo_path))
    if not path.is_file():
        return None
    try:
        # Read-only: a display path must not create, migrate or lock the ledger.
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT surface, decision_id, node_id, shown_at FROM injections "
            "WHERE surface IN ('', 'decision', ?) "
            "OR (surface = 'upkeep' AND category = 'commit_notice')",
            (WHY_SURFACE,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    out: list[tuple[str, float]] = []
    for surface, decision_id, node_id, shown_at in rows:
        if surface in ("", "decision"):
            out.append((decision_id, shown_at))
        else:
            out.extend((named, shown_at) for named in (node_id or "").split())
    return out


def fold_deliveries(
    rows: list[tuple[str, float]], decision_ids: list[str], aliases: dict[str, str]
) -> dict[str, Delivery]:
    """Count *rows* onto *decision_ids*, following *aliases* (retired id -> live id).

    A record's id moves when its scope does and the ledger is not rewritten, so
    rows under a retired id belong to the record it now resolves to.
    """
    wanted = set(decision_ids)
    by_prefix: dict[str, str] = {}
    for did in decision_ids:
        by_prefix.setdefault(did[:8], did)
    counts: dict[str, int] = dict.fromkeys(decision_ids, 0)
    last: dict[str, float] = {}
    for named, shown_at in rows:
        target = aliases.get(named, named)
        if target not in wanted and len(named) == 8:
            target = by_prefix.get(named, "")
        if target not in wanted:
            continue
        counts[target] += 1
        last[target] = max(last.get(target, 0.0), shown_at or 0.0)
    return {
        did: Delivery(
            count=counts[did],
            last_at=datetime.fromtimestamp(last[did], tz=UTC) if last.get(did) else None,
        )
        for did in decision_ids
    }


async def decision_deliveries(
    session: Any, repo_path: Path | str | None, decision_ids: list[str]
) -> dict[str, Delivery] | None:
    """Deliveries per record in *decision_ids*, or None when no ledger can be read."""
    if repo_path is None or not decision_ids:
        return None
    rows = ledger_deliveries(repo_path)
    if rows is None:
        return None
    from sqlalchemy import select

    from repowise.core.persistence.models import SUPERSEDED_REASON, DecisionAlias

    # A supersede pointer names the successor, not the same record.
    result = await session.execute(
        select(DecisionAlias.alias_id, DecisionAlias.decision_id).where(
            DecisionAlias.decision_id.in_(decision_ids),
            DecisionAlias.reason != SUPERSEDED_REASON,
        )
    )
    return fold_deliveries(rows, decision_ids, dict(result.all()))
