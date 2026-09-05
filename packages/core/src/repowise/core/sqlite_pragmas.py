"""Connection setup shared by the synchronous SQLite sidecar stores.

The distill omissions, precedent and staged-decision stores, and the CLI hook
ledger, all open plain :mod:`sqlite3` connections. This module holds the one
pragma sequence they apply, in the order ``persistence/database.py`` applies
it to the async engine. It stays on stdlib only because two of those stores
sit on command paths where importing the engine module costs more than a
second.
"""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)


def apply_sqlite_pragmas(conn: sqlite3.Connection, busy_timeout_ms: int) -> bool:
    """Apply ``busy_timeout``, WAL and ``synchronous=NORMAL``. Never raises on WAL.

    The stores used to issue the WAL switch bare, before any busy timeout.
    The switch needs a short exclusive lock, so a concurrent reader or writer
    on the same file made the constructor raise ``database is locked`` and
    took the whole store down, at exactly the moment a post-commit update and
    an editor hook were most likely to overlap. The timeout goes first so the
    switch inherits its retry window; a switch that still fails is logged and
    left alone. The store keeps the journal mode it has and every statement
    still runs, so only readers concurrent with a writer lose out until a
    later open succeeds. Returns whether the switch took.
    """
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    switched = True
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        switched = False
        log.debug("sqlite: could not switch to WAL, keeping the current journal mode: %s", exc)
    conn.execute("PRAGMA synchronous=NORMAL")
    return switched
