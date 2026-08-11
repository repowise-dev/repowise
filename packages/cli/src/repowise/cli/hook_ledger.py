"""The write side of the ``sessions.db`` hook ledger, shared by *both* hooks.

Every hook surface records what it showed and what it cost here, so the
update-time miner and ``repowise hook stats`` judge one corpus rather than one
per surface. These helpers grew inside :mod:`decision_inject`, which owns only
the *decision* surface, and then moved to ``augment_cmd/ledger.py``, which
could only ever serve the PostToolUse hook.

**It lives at the top of ``repowise.cli`` because the PreToolUse rewrite hook
needs it too, and cannot reach into ``augment_cmd``.** That package's
``__init__`` imports click and every submodule; paying it on every Bash call to
write one counter row would cost far more than the row is worth. Hoisting the
module was the alternative to a second copy of the connect/migrate/upsert
logic, and a second copy is how the two would drift.

The rewrite hook is here at all because its firing rate is not observable any
other way: an ``updatedInput`` rewrite never appears in a transcript, so a
transcript scan can only find hand-typed ``repowise distill`` invocations,
which say nothing about the hook. It sits on the shell tools, the busiest
surface either hook has, and until now wrote nothing at all.

Import it lazily, inside the function that writes, the way every caller here
does. Two reasons and both are load-bearing: a hook invocation that says
nothing must not pay for a database module it never opens, and a module-scope
``from .hook_ledger import x`` binds a copy that a test patching
``hook_ledger.x`` will not reach — so a spy would silently miss whichever
surfaces bound early.

Three constraints shape everything here, and none is negotiable:

* **No ``repowise.core``.** The hook budget is 155 ms and importing the ORM
  costs more than that on its own, so this is raw stdlib :mod:`sqlite3` and the
  ``injections`` migration is duplicated verbatim from
  ``core.sessions.staging`` rather than imported.
  ``tests/unit/cli/test_augment_hook_perf.py`` asserts on the import graph.
* **Nothing at module scope but the clock.** ``sqlite3`` is deferred into the
  functions that need it, because ``rewrite_hook`` imports this module at *its*
  module scope — see :data:`_T0` for why it has to — and a command that bails
  on shape must not pay for a database it never opens. Paths are handled with
  :mod:`os.path` throughout for the same reason.
  ``tests/unit/cli/test_augment_hook_perf.py`` asserts the deferral holds.
* **Silence over failure.** A hook must never surface an error into the agent's
  transcript, so every function here swallows :class:`sqlite3.Error` and the
  claim helpers fail *closed* — an unusable sidecar degrades to saying nothing,
  never to saying it twice.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - deferred on the hot path, see above
    import sqlite3
    from pathlib import Path

#: Process start, near enough. Both hook entry points import this module at
#: their own module scope so this is captured before any work happens; a lazy
#: import inside the writer would stamp every firing with ~0 ms.
_T0 = time.perf_counter()


def _elapsed_ms() -> int:
    """Milliseconds of in-process hook work so far (see :data:`_T0`)."""
    return int((time.perf_counter() - _T0) * 1000)


#: Memo for :func:`emitting_build`. One hook process emits at most a handful of
#: rows and every one of them wants the same answer.
_BUILD: str | None = None


def emitting_build() -> str:
    """Which repowise build produced this emission: ``"<version>+<install>"``.

    Every ledger row carries this, and the SessionStart block prints it,
    because without it an emission cannot be attributed to the code that
    produced it. A retired emitter that keeps reaching agents is
    indistinguishable from a retirement that never shipped, and an unstamped
    ledger cannot tell an adoption rate apart from two builds averaged
    together.

    A version alone is not enough: the common case is two installs of the
    *same* release, an editable checkout and a pip-installed console script,
    disagreeing about what has been retired. So the second half is an install
    identity — eight hex of the directory this module was imported from. It
    says which install spoke, never who or where: the digest is one-way and
    the path never leaves the machine.
    """
    global _BUILD
    if _BUILD is None:
        import hashlib

        from repowise.cli import __version__

        # The directory rather than ``__file__``: two modules in one install
        # must not read as two installs.
        install = hashlib.sha1(os.path.dirname(__file__).encode("utf-8", "replace")).hexdigest()
        _BUILD = f"{__version__}+{install[:8]}"
    return _BUILD

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
    "build TEXT NOT NULL DEFAULT '', "
    "PRIMARY KEY (session_id, decision_id))"
)

_HOOK_RUNS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS hook_runs ("
    "session_id TEXT NOT NULL, event TEXT NOT NULL, tool TEXT NOT NULL, "
    "calls INTEGER NOT NULL DEFAULT 0, emitted INTEGER NOT NULL DEFAULT 0, "
    "total_ms INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, event, tool))"
)

#: The rewrite hook's own counter, aggregated rather than one row per firing.
#:
#: It cannot use ``injections``: that table is keyed on a hash of the emitted
#: text and INSERT-OR-IGNOREs, which is right for a notice that should be said
#: once and wrong for a counter — every ``bailed``/``shape`` would collapse into
#: a single row per session and the distribution this exists to measure would
#: read as "1". So this upserts a count, the same shape ``hook_runs`` already
#: uses, on the one axis that matters: **why** a command was or was not
#: rewritten. ``repowise hook stats`` renders it as the ``rewrite`` surface.
_REWRITE_RUNS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS rewrite_runs ("
    "session_id TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL, "
    "calls INTEGER NOT NULL DEFAULT 0, total_ms INTEGER NOT NULL DEFAULT 0, "
    "build TEXT NOT NULL DEFAULT '', "
    "PRIMARY KEY (session_id, outcome, reason))"
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
    ("build", "TEXT NOT NULL DEFAULT ''"),
)

#: One ledger connection per hook process, opened lazily. A single invocation
#: can write several rows (two notices plus the run counter), and connect +
#: WAL pragma + the CREATE/PRAGMA/ALTER migration is the expensive part, not
#: the INSERT. The process is short-lived and single-threaded, so caching is
#: safe; every writer commits, and process exit closes the handle.
#: Keyed by repo path, not global: a hook process only ever sees one repo, but
#: the test suite drives many through one interpreter, and a cache that
#: ignored the path would hand the second repo the first one's database.
#: Keyed on the *string* path: callers pass ``Path`` (augment) and ``str``
#: (the rewrite hook, which keeps pathlib off its hot path), and two spellings
#: of one repo must not open two connections.
_CONN: dict[str, sqlite3.Connection | None] = {}


def _open_injections(repo_path: Path | str) -> sqlite3.Connection | None:
    """The shared ledger connection, migrated and ready. None if unusable.

    Callers must NOT close the returned connection — see :data:`_CONN`.
    """
    key = str(repo_path)
    if key in _CONN:
        return _CONN[key]
    import sqlite3

    db_dir = os.path.join(key, ".repowise", "sessions")
    db_path = os.path.join(db_dir, "sessions.db")
    try:
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(_INJECTIONS_TABLE_SQL)
        conn.execute(_HOOK_RUNS_TABLE_SQL)
        conn.execute(_REWRITE_RUNS_TABLE_SQL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(injections)")}
        for name, decl in _LEDGER_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE injections ADD COLUMN {name} {decl}")
        _CONN[key] = conn
        return conn
    except (sqlite3.Error, OSError):
        _CONN[key] = None
        return None


def _record_hook_run(
    repo_path: Path, session_id: str, event: str, tool: str, *, emitted: bool
) -> None:
    """Count one hook invocation and what it cost. Best-effort, never raises.

    Every invocation lands here, including the large majority that return
    nothing: the matcher covers Read/Edit/Write/Grep/Glob and the repowise MCP
    tools, and a silent run still pays the import cost. The
    harness only writes a transcript record for hooks that *emitted*, so
    without this counter the silent invocations — the bulk of the bill — are
    invisible. Aggregated per (session, event, tool) rather than one row per
    call, so the table stays small and the write stays a single upsert.
    """
    if not session_id:
        return
    conn = _open_injections(repo_path)
    if conn is None:
        return
    import sqlite3

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
    repo_path: Path,
    session_id: str,
    decision_ids: list[str],
    *,
    node_id: str,
    chars: int = 0,
) -> None:
    """Log shown decisions in the sessions.db sidecar; best-effort, never raises.

    The update-time miner reads these rows to judge whether injected guidance
    was followed or contradicted (usage feedback v1). Written with raw stdlib
    sqlite3 so the hook path never imports repowise.core.

    *chars* is the whole block's size, divided across the decisions it carried:
    the cost model sums this column, and a block recorded at zero is one the
    net treats as free. It is the largest single thing this hook emits, so
    getting that wrong understates the debit side more than everything else
    combined. Rounding is upward on the first row rather than lost.
    """
    if not session_id or not decision_ids:
        return
    conn = _open_injections(repo_path)
    if conn is None:
        return
    import sqlite3

    try:
        now = time.time()
        elapsed = _elapsed_ms()
        build = emitting_build()
        share, remainder = divmod(max(0, chars), len(decision_ids))
        conn.executemany(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, duration_ms, "
            "build, chars) VALUES (?, ?, ?, ?, 'decision', 'session_start', ?, ?, ?)",
            [
                (session_id, did, node_id, now, elapsed, build, share + (remainder if i == 0 else 0))
                for i, did in enumerate(decision_ids)
            ],
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
    :func:`_elapsed_ms`); it is what ``repowise hook stats`` reports until a
    transcript pass replaces it with the harness-measured total.
    """
    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    import sqlite3

    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, chars, "
            "duration_ms, build) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                key,
                node_id,
                time.time(),
                surface,
                category,
                chars,
                _elapsed_ms(),
                emitting_build(),
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
    repo_path: Path, session_id: str, decision_id: str, node_id: str, *, chars: int = 0
) -> tuple[bool, int]:
    """Atomically claim the right to show one decision this session.

    Returns ``(claimed, edit_notice_count)``. The primary key makes the
    INSERT OR IGNORE the once-per-session-per-decision gate, immune to the
    state-file races two concurrent hook processes produce; the count backs
    the strict per-session notice cap. Fail-closed: any error reports
    unclaimed, so a sidecar glitch degrades to silence, never to spam.
    """
    conn = _open_injections(repo_path)
    if conn is None:
        return False, 0
    import sqlite3

    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, duration_ms, "
            "build, chars) VALUES (?, ?, ?, ?, 'decision', 'edit_notice', ?, ?, ?)",
            (
                session_id,
                decision_id,
                node_id,
                time.time(),
                _elapsed_ms(),
                emitting_build(),
                chars,
            ),
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


#: What a rewrite-hook invocation did. ``rewritten`` carries the distill family
#: as its reason; ``bailed`` carries why, which is the distribution the plan
#: asked for and which was previously guesswork.
REWRITTEN = "rewritten"
BAILED = "bailed"


def _open_rewrite_runs(repo_path: str):
    """A connection carrying only what the rewrite counter needs.

    The PreToolUse hook fires on every shell command, so its write has to be
    the smallest one that is still correct: one CREATE IF NOT EXISTS against a
    table nothing else migrates, and no ``injections`` work at all. Shares
    :data:`_CONN` keyed on the same path — a process is either the rewrite hook
    or the augment hook, never both, so the two openers cannot race.

    ``None`` whenever the sidecar is unusable; a counter is never worth an
    error in the agent's transcript.
    """
    key = str(repo_path)
    if key in _CONN:
        return _CONN[key]
    import sqlite3

    db_dir = os.path.join(key, ".repowise", "sessions")
    try:
        os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(os.path.join(db_dir, "sessions.db"), timeout=1)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(_REWRITE_RUNS_TABLE_SQL)
        _CONN[key] = conn
        return conn
    except (sqlite3.Error, OSError):
        _CONN[key] = None
        return None


def record_rewrite(
    repo_path: str, session_id: str, *, outcome: str, reason: str, elapsed_ms: int | None = None
) -> None:
    """Count one ``repowise-rewrite`` decision. Best-effort, never raises.

    Called after the response is on stdout, for the same reason every other
    write here is deferred: the agent must not wait on accounting.

    An upsert, not an INSERT-OR-IGNORE — see :data:`_REWRITE_RUNS_TABLE_SQL`.
    The point is *how many* commands bailed on shape versus on family, and a
    once-per-session row would answer "one of each".

    ``repo_path`` is a plain string because the caller resolved it with
    :mod:`os.path`; the rewrite hook keeps ``pathlib`` off its hot path.
    """
    if not session_id or not repo_path:
        return
    # Deliberately *not* :func:`_open_injections`. That opener migrates the
    # ``injections`` table — three CREATEs, a PRAGMA table_info and an ALTER
    # loop — none of which this writer touches, and it would pay for all of it
    # on every shell command an agent runs. Measured on this machine, the lean
    # path below is roughly half the cost of the shared one.
    conn = _open_rewrite_runs(repo_path)
    if conn is None:
        return
    import sqlite3

    try:
        conn.execute(
            "INSERT INTO rewrite_runs (session_id, outcome, reason, calls, total_ms, build) "
            "VALUES (?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(session_id, outcome, reason) DO UPDATE SET "
            # ``build`` moves to whichever install wrote last. An upgrade
            # mid-session would otherwise leave the first build owning the row
            # and pool two builds' calls under it, which is the averaging the
            # column exists to make visible.
            "calls = calls + 1, total_ms = total_ms + excluded.total_ms, "
            "build = excluded.build",
            (
                session_id,
                outcome,
                reason,
                _elapsed_ms() if elapsed_ms is None else elapsed_ms,
                emitting_build(),
            ),
        )
        conn.commit()
    except sqlite3.Error:
        pass
