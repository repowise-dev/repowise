"""Savings ledger — SQL for recording and summarizing distillation events.

Operates on the omissions sidecar connection (see ``store.py``); kept as free
functions so future surfaces (hook script, MCP budgeter) can record savings
without instantiating a full :class:`~repowise.core.distill.store.OmissionStore`.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any


def record_saving(
    conn: sqlite3.Connection,
    *,
    filter_name: str,
    source: str,
    command: str | None,
    raw_tokens: int,
    distilled_tokens: int,
) -> None:
    """Append one distillation event to the savings ledger."""
    conn.execute(
        """
        INSERT INTO savings
            (created_at, filter, source, command, raw_tokens, distilled_tokens)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (time.time(), filter_name, source, command, raw_tokens, distilled_tokens),
    )
    conn.commit()


def savings_summary(
    conn: sqlite3.Connection, *, since: float | None = None
) -> dict[str, Any]:
    """Aggregate ledger totals, overall and per filter.

    *since* is a Unix timestamp; only events at or after it are counted.
    """
    where = " WHERE created_at >= ?" if since is not None else ""
    params: tuple[float, ...] = (since,) if since is not None else ()
    total_raw, total_distilled, events = conn.execute(
        "SELECT COALESCE(SUM(raw_tokens),0), COALESCE(SUM(distilled_tokens),0),"
        f" COUNT(*) FROM savings{where}",
        params,
    ).fetchone()
    per_filter = {
        row[0]: {
            "events": row[1],
            "raw_tokens": row[2],
            "distilled_tokens": row[3],
            "saved_tokens": row[2] - row[3],
        }
        for row in conn.execute(
            "SELECT filter, COUNT(*), SUM(raw_tokens), SUM(distilled_tokens)"
            f" FROM savings{where} GROUP BY filter ORDER BY SUM(raw_tokens) DESC",
            params,
        )
    }
    return {
        "events": events,
        "raw_tokens": total_raw,
        "distilled_tokens": total_distilled,
        "saved_tokens": total_raw - total_distilled,
        "per_filter": per_filter,
    }


#: Grouping dimensions accepted by :func:`savings_rollup`. ``day`` buckets by
#: the event's local calendar date; ``filter``/``source`` group on the raw
#: ledger columns.
ROLLUP_DIMENSIONS: tuple[str, ...] = ("filter", "day", "source")

_ROLLUP_COLUMNS = {
    "filter": "filter",
    "source": "source",
    "day": "date(created_at, 'unixepoch', 'localtime')",
}


def savings_rollup(
    conn: sqlite3.Connection,
    *,
    by: str = "filter",
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Grouped ledger totals — one row per *by* bucket.

    *by* is one of :data:`ROLLUP_DIMENSIONS`. Rows carry ``group``,
    ``events``, ``raw_tokens``, ``distilled_tokens``, ``saved_tokens``.
    ``day`` rollups are ordered chronologically; the rest by tokens saved,
    descending. *since* is a Unix timestamp lower bound.
    """
    if by not in _ROLLUP_COLUMNS:
        raise ValueError(f"Unknown rollup dimension {by!r}; expected one of {ROLLUP_DIMENSIONS}")
    group_col = _ROLLUP_COLUMNS[by]
    where = " WHERE created_at >= ?" if since is not None else ""
    params: tuple[float, ...] = (since,) if since is not None else ()
    order = "1 ASC" if by == "day" else "SUM(raw_tokens - distilled_tokens) DESC"
    rows = conn.execute(
        f"SELECT {group_col}, COUNT(*), SUM(raw_tokens), SUM(distilled_tokens)"
        f" FROM savings{where} GROUP BY 1 ORDER BY {order}",
        params,
    ).fetchall()
    return [
        {
            "group": row[0],
            "events": row[1],
            "raw_tokens": row[2],
            "distilled_tokens": row[3],
            "saved_tokens": row[2] - row[3],
        }
        for row in rows
    ]
