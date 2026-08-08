"""The hook path's write side of the ``sessions.db`` efficacy ledger.

Every augment surface records what it showed and what it cost here, so the
update-time miner and ``repowise hook stats`` can judge one corpus rather than
one per surface. These helpers grew inside :mod:`decision_inject`, which owns
only the *decision* surface, so the read, search and served-read surfaces were
importing the shared ledger from a peer that has nothing to do with them. This
module is that shared thing, named.

Import it lazily, inside the function that writes, the way every caller here
does. Two reasons and both are load-bearing: a hook invocation that says
nothing must not pay for a database module it never opens, and a module-scope
``from .ledger import x`` binds a copy that a test patching ``ledger.x`` will
not reach — so a spy would silently miss whichever surfaces bound early.

Two constraints shape everything here, and neither is negotiable:

* **No ``repowise.core``.** The hook budget is 155 ms and importing the ORM
  costs more than that on its own, so this is raw stdlib :mod:`sqlite3` and the
  ``injections`` migration is duplicated verbatim from
  ``core.sessions.staging`` rather than imported.
  ``tests/unit/cli/test_augment_hook_perf.py`` asserts on the import graph.
* **Silence over failure.** A hook must never surface an error into the agent's
  transcript, so every function here swallows :class:`sqlite3.Error` and the
  claim helpers fail *closed* — an unusable sidecar degrades to saying nothing,
  never to saying it twice.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_INJECTIONS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS injections ("
    "session_id TEXT NOT NULL, decision_id TEXT NOT NULL, "
    "node_id TEXT NOT NULL DEFAULT '', shown_at REAL NOT NULL, "
    "evaluated INTEGER NOT NULL DEFAULT 0, "
    "surface TEXT NOT NULL DEFAULT '', "
    "category TEXT NOT NULL DEFAULT '', "
    "chars INTEGER NOT NULL DEFAULT 0, "
    "duration_ms INTEGER NOT NULL DEFAULT 0, "
    "acted INTEGER NOT NULL DEFAULT 0, "
    "verdict TEXT NOT NULL DEFAULT '', "
    "PRIMARY KEY (session_id, decision_id))"
)

_HOOK_RUNS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS hook_runs ("
    "session_id TEXT NOT NULL, event TEXT NOT NULL, tool TEXT NOT NULL, "
    "calls INTEGER NOT NULL DEFAULT 0, emitted INTEGER NOT NULL DEFAULT 0, "
    "total_ms INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, event, tool))"
)

#: Mirror of core.sessions.staging.INJECTIONS_LEDGER_COLUMNS — the hook path
#: must not import repowise.core, so the migration is duplicated verbatim.
_LEDGER_COLUMNS = (
    ("surface", "TEXT NOT NULL DEFAULT ''"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("chars", "INTEGER NOT NULL DEFAULT 0"),
    ("duration_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("acted", "INTEGER NOT NULL DEFAULT 0"),
    ("verdict", "TEXT NOT NULL DEFAULT ''"),
)

#: One ledger connection per hook process, opened lazily. A single invocation
#: can write several rows (two notices plus the run counter), and connect +
#: WAL pragma + the CREATE/PRAGMA/ALTER migration is the expensive part, not
#: the INSERT. The process is short-lived and single-threaded, so caching is
#: safe; every writer commits, and process exit closes the handle.
#: Keyed by repo path, not global: a hook process only ever sees one repo, but
#: the test suite drives many through one interpreter, and a cache that
#: ignored the path would hand the second repo the first one's database.
_CONN: dict[Path, sqlite3.Connection | None] = {}


def _open_injections(repo_path: Path) -> sqlite3.Connection | None:
    """The shared ledger connection, migrated and ready. None if unusable.

    Callers must NOT close the returned connection — see :data:`_CONN`.
    """
    if repo_path in _CONN:
        return _CONN[repo_path]
    db_path = repo_path / ".repowise" / "sessions" / "sessions.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(_INJECTIONS_TABLE_SQL)
        conn.execute(_HOOK_RUNS_TABLE_SQL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(injections)")}
        for name, decl in _LEDGER_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE injections ADD COLUMN {name} {decl}")
        _CONN[repo_path] = conn
        return conn
    except (sqlite3.Error, OSError):
        _CONN[repo_path] = None
        return None


def _record_hook_run(
    repo_path: Path, session_id: str, event: str, tool: str, *, emitted: bool
) -> None:
    """Count one hook invocation and what it cost. Best-effort, never raises.

    Every invocation lands here, including the large majority that return
    nothing: the matcher covers Read/Edit/Write/Grep/Glob/Bash/PowerShell and
    the repowise MCP tools, and a silent run still pays the import cost. The
    harness only writes a transcript record for hooks that *emitted*, so
    without this counter the silent invocations — the bulk of the bill — are
    invisible. Aggregated per (session, event, tool) rather than one row per
    call, so the table stays small and the write stays a single upsert.
    """
    if not session_id:
        return
    from ._shared import _elapsed_ms

    conn = _open_injections(repo_path)
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO hook_runs (session_id, event, tool, calls, emitted, total_ms) "
            "VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(session_id, event, tool) DO UPDATE SET "
            "calls = calls + 1, emitted = emitted + excluded.emitted, "
            "total_ms = total_ms + excluded.total_ms",
            (session_id, event, tool, 1 if emitted else 0, _elapsed_ms()),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def _record_injections(
    repo_path: Path, session_id: str, decision_ids: list[str], *, node_id: str
) -> None:
    """Log shown decisions in the sessions.db sidecar; best-effort, never raises.

    The update-time miner reads these rows to judge whether injected guidance
    was followed or contradicted (usage feedback v1). Written with raw stdlib
    sqlite3 so the hook path never imports repowise.core.
    """
    if not session_id or not decision_ids:
        return
    conn = _open_injections(repo_path)
    if conn is None:
        return
    try:
        from ._shared import _elapsed_ms

        now = time.time()
        elapsed = _elapsed_ms()
        conn.executemany(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, duration_ms) "
            "VALUES (?, ?, ?, ?, 'decision', 'session_start', ?)",
            [(session_id, did, node_id, now, elapsed) for did in decision_ids],
        )
        conn.commit()
    except sqlite3.Error:
        pass


def _claim_ledger(
    repo_path: Path,
    session_id: str,
    key: str,
    *,
    node_id: str,
    surface: str,
    category: str,
    chars: int,
) -> tuple[bool, int]:
    """Atomically claim one non-decision ledger emission.

    Generic twin of :func:`_claim_injection` for the read/search enrichment
    surfaces: *key* replaces the decision id in the primary key, so INSERT OR
    IGNORE is the once-per-session-per-key gate. Returns ``(claimed,
    surface_injection_count)`` where the count covers only rows that actually
    carried text (``chars > 0``) on *surface* — pure measurement rows must not
    eat into an injection cap. Fail-closed: any error reports unclaimed.

    ``duration_ms`` records what this firing has cost so far (see
    :func:`_shared._elapsed_ms`); it is what ``repowise hook stats`` reports
    until a transcript pass replaces it with the harness-measured total.
    """
    from ._shared import _elapsed_ms

    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, chars, "
            "duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                key,
                node_id,
                time.time(),
                surface,
                category,
                chars,
                _elapsed_ms(),
            ),
        )
        claimed = cur.rowcount > 0
        count = conn.execute(
            "SELECT COUNT(*) FROM injections WHERE session_id = ? AND surface = ? AND chars > 0",
            (session_id, surface),
        ).fetchone()[0]
        conn.commit()
        return claimed, int(count)
    except sqlite3.Error:
        return False, 0


def _claim_injection(
    repo_path: Path, session_id: str, decision_id: str, node_id: str
) -> tuple[bool, int]:
    """Atomically claim the right to show one decision this session.

    Returns ``(claimed, edit_notice_count)``. The primary key makes the
    INSERT OR IGNORE the once-per-session-per-decision gate, immune to the
    state-file races two concurrent hook processes produce; the count backs
    the strict per-session notice cap. Fail-closed: any error reports
    unclaimed, so a sidecar glitch degrades to silence, never to spam.
    """
    from ._shared import _elapsed_ms

    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, duration_ms) "
            "VALUES (?, ?, ?, ?, 'decision', 'edit_notice', ?)",
            (session_id, decision_id, node_id, time.time(), _elapsed_ms()),
        )
        claimed = cur.rowcount > 0
        # Surface-scoped: read/search enrichment rows also carry a node_id and
        # must not eat into the edit-notice cap.
        count = conn.execute(
            "SELECT COUNT(*) FROM injections WHERE session_id = ? AND node_id != '' "
            "AND surface IN ('', 'decision')",
            (session_id,),
        ).fetchone()[0]
        conn.commit()
        return claimed, int(count)
    except sqlite3.Error:
        return False, 0
