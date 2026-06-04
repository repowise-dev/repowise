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


def savings_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate ledger totals, overall and per filter."""
    total_raw, total_distilled, events = conn.execute(
        "SELECT COALESCE(SUM(raw_tokens),0), COALESCE(SUM(distilled_tokens),0),"
        " COUNT(*) FROM savings"
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
            " FROM savings GROUP BY filter ORDER BY SUM(raw_tokens) DESC"
        )
    }
    return {
        "events": events,
        "raw_tokens": total_raw,
        "distilled_tokens": total_distilled,
        "saved_tokens": total_raw - total_distilled,
        "per_filter": per_filter,
    }
