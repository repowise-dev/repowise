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


def distill_summary(
    conn: sqlite3.Connection, *, since: float | None = None
) -> dict[str, Any]:
    """Ledger totals for the **distill** surface only (excludes ``mcp:*``).

    Identical in shape to :func:`savings_summary` but scoped to non-MCP
    sources, so the hero card can report a clean ``repowise distill`` figure now
    that Phase 2 also writes counterfactual ``mcp:<tool>`` rows into the same
    ``savings`` ledger. Per-filter buckets likewise drop MCP tool rows.

    *since* is a Unix timestamp lower bound on ``created_at``.
    """
    where = "source NOT LIKE 'mcp:%'"
    params: tuple[float, ...] = ()
    if since is not None:
        where += " AND created_at >= ?"
        params = (since,)
    total_raw, total_distilled, events = conn.execute(
        "SELECT COALESCE(SUM(raw_tokens),0), COALESCE(SUM(distilled_tokens),0),"
        f" COUNT(*) FROM savings WHERE {where}",
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
            f" FROM savings WHERE {where} GROUP BY filter ORDER BY SUM(raw_tokens) DESC",
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


def mcp_savings_summary(
    conn: sqlite3.Connection, *, since: float | None = None
) -> dict[str, Any]:
    """Unified MCP savings view — counterfactual ledger, truncation as fallback.

    Two MCP signals live in the sidecar:

    * **Counterfactual** rows in the ``savings`` ledger (``source='mcp:<tool>'``)
      written by the Phase 2 instrumentation: ``saved = replaced - delivered``.
      Because ``delivered`` is measured *after* response-budget truncation, the
      truncation saving is already folded into this delta.
    * **Truncation drops** in the ``omissions`` table (also ``source='mcp:<tool>'``)
      from :func:`mcp_drops_summary` — the only signal for tools that have no
      counterfactual estimator yet.

    Merging per tool with **counterfactual precedence** avoids double counting:
    a tool with counterfactual rows reports its ledger ``saved_tokens`` (which
    subsumes truncation); a tool with only drops reports its dropped tokens.
    Each ``per_tool`` row is tagged ``kind`` = ``"counterfactual"`` | ``"truncation"``.

    Returns ``{events, tokens, queries, per_tool}`` where ``tokens`` is total
    saved, ``queries`` counts counterfactual tool calls (the "N MCP queries
    answered" headline), and ``events`` counts every contributing event.
    """
    ledger = {
        _strip_mcp_prefix(row["group"]): row
        for row in savings_rollup(conn, by="source", since=since)
        if row["group"].startswith("mcp:")
    }
    drops = mcp_drops_summary(conn, since=since)["per_tool"]

    per_tool: list[dict[str, Any]] = []
    queries = 0
    for tool, row in ledger.items():
        per_tool.append(
            {
                "tool": tool,
                "events": row["events"],
                "tokens": row["saved_tokens"],
                "kind": "counterfactual",
            }
        )
        queries += row["events"]
    for tool, stats in drops.items():
        if tool in ledger:
            continue  # counterfactual already subsumes this tool's truncation
        per_tool.append(
            {
                "tool": tool,
                "events": stats["events"],
                "tokens": stats["tokens"],
                "kind": "truncation",
            }
        )

    per_tool.sort(key=lambda r: r["tokens"], reverse=True)
    return {
        "events": sum(r["events"] for r in per_tool),
        "tokens": sum(r["tokens"] for r in per_tool),
        "queries": queries,
        "per_tool": per_tool,
    }


def mcp_drops_summary(
    conn: sqlite3.Connection, *, since: float | None = None
) -> dict[str, Any]:
    """Truncation savings the MCP server already wrote to the omissions store.

    MCP tools drop content past their response budget into the ``omissions``
    table under ``source='mcp:<tool>'`` but never call
    :func:`record_saving`, so these savings are invisible to
    :func:`savings_summary`. This reads them straight from ``omissions``:
    total dropped tokens plus a per-tool rollup (with the ``mcp:`` prefix
    stripped). It is the *truncation-only* view — Phase 2 additionally records
    counterfactual ``mcp:*`` rows into the ``savings`` ledger.

    *since* is a Unix timestamp lower bound on ``created_at``.
    """
    where = "source LIKE 'mcp:%'"
    params: tuple[float, ...] = ()
    if since is not None:
        where += " AND created_at >= ?"
        params = (since,)
    events, tokens = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(original_tokens), 0) FROM omissions WHERE {where}",
        params,
    ).fetchone()
    per_tool = {
        _strip_mcp_prefix(row[0]): {"events": row[1], "tokens": row[2]}
        for row in conn.execute(
            "SELECT source, COUNT(*), COALESCE(SUM(original_tokens), 0)"
            f" FROM omissions WHERE {where} GROUP BY source ORDER BY SUM(original_tokens) DESC",
            params,
        )
    }
    return {"events": events, "tokens": tokens, "per_tool": per_tool}


def _strip_mcp_prefix(source: str) -> str:
    """``mcp:get_risk`` → ``get_risk`` (passthrough for anything else)."""
    prefix = "mcp:"
    return source[len(prefix):] if source.startswith(prefix) else source


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
