"""Staging sidecar for session-mined decision candidates.

Candidates mined from agent transcripts live in their own WAL SQLite sidecar
(``.repowise/sessions/sessions.db``, the OmissionStore pattern from
:mod:`repowise.core.distill.store`) rather than wiki.db, so update-time writes
never contend with indexing. The sidecar holds three things:

- ``raw_candidates``: deterministic-gate output awaiting the batched LLM
  structuring pass. A raw row survives an LLM failure, so nothing mined is
  ever lost to a flaky call; it is retried on the next update.
- ``decisions``: structured candidates keyed by normalized title, carrying
  the distinct sessions that observed them (the promotion counter) and the
  emit bookkeeping that keeps promotion idempotent across updates.
- ``cursors``: per-transcript byte offsets, the DB-backed twin of
  :class:`repowise.core.sessions.cursor.CursorStore` (same ``get`` /
  ``advance`` / ``save`` surface, so :func:`iter_new_events` consumes it
  unchanged). Living in the same database means a cursor only advances in
  the same commit that stages what was read under it.
- ``injections``: decision ids the augment hooks showed to an agent session
  (written hook-side with raw stdlib sqlite3), read back at update time to
  judge whether the guidance was followed or contradicted (usage feedback).

Everything is local; transcripts and candidates never leave the machine.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

SESSIONS_DIRNAME = "sessions"
SESSIONS_DB_FILENAME = "sessions.db"

#: Raw candidates older than this that were never structured are dropped:
#: they will not gain observations sitting in the queue, and an unbounded
#: backlog would grow the batched LLM pass forever.
RAW_TTL_DAYS = 90.0

#: ``PRAGMA user_version`` marking that :meth:`retire_unjudgeable_verdicts`
#: has run on this store. A one-shot data repair, not a schema migration, so
#: it rides the pragma rather than a table: the hook path opens this same
#: database with raw sqlite3 and must not learn about a new one.
_VERDICT_REPAIR_VERSION = 1

#: ``raw_candidates.kind`` for a broad-discovery candidate. Discovery writes
#: its raw row only as the anchor ``upsert_structured`` needs, never as work
#: for the deterministic structuring pass.
DISCOVERY_KIND = "session_discovery"

#: Retries a discovery span gets across updates before it is retired. Bounded
#: so one span the provider keeps choking on cannot wedge the queue head
#: forever; a validation rejection is not a retry, since the span was read.
MAX_SPAN_ATTEMPTS = 3

#: Cap on the distinct session ids tracked per structured decision. Two is
#: enough to promote; beyond a handful the extra ids only pad evidence.
_MAX_SESSIONS_TRACKED = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_candidates (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    quotes TEXT NOT NULL,
    files TEXT NOT NULL,
    session_id TEXT,
    created_at REAL NOT NULL,
    structured_key TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    structured TEXT NOT NULL,
    sessions TEXT NOT NULL,
    quotes TEXT NOT NULL,
    files TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    promoted_at REAL,
    emitted_sessions INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hook_runs (
    session_id TEXT NOT NULL,
    event TEXT NOT NULL,
    tool TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    emitted INTEGER NOT NULL DEFAULT 0,
    total_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, event, tool)
);
CREATE TABLE IF NOT EXISTS cursors (
    file TEXT PRIMARY KEY,
    offset INTEGER NOT NULL,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS injections (
    session_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    node_id TEXT NOT NULL DEFAULT '',
    shown_at REAL NOT NULL,
    evaluated INTEGER NOT NULL DEFAULT 0,
    surface TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    chars INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    acted INTEGER NOT NULL DEFAULT 0,
    verdict TEXT NOT NULL DEFAULT '',
    build TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, decision_id)
);
CREATE TABLE IF NOT EXISTS discovery_spans (
    span_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    files TEXT NOT NULL,
    ts REAL,
    created_at REAL NOT NULL,
    consumed_at REAL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_pending ON raw_candidates(structured_key)
    WHERE structured_key IS NULL;
CREATE INDEX IF NOT EXISTS idx_discovery_pending ON discovery_spans(created_at)
    WHERE consumed_at IS NULL;
"""

#: Columns added to ``injections`` after PR4 shipped the table (the ledger now
#: records every hook surface, not just decision injections). Kept in one place
#: so the hook-side writer (augment's stdlib-sqlite3 opener) and this reader
#: apply the identical migration to sidecars created by either side first.
INJECTIONS_LEDGER_COLUMNS = (
    ("surface", "TEXT NOT NULL DEFAULT ''"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("chars", "INTEGER NOT NULL DEFAULT 0"),
    # Wall-clock cost of the firing, and whether the agent acted on it. See
    # :mod:`repowise.core.sessions.efficacy` for how both are filled in.
    ("duration_ms", "INTEGER NOT NULL DEFAULT 0"),
    ("acted", "INTEGER NOT NULL DEFAULT 0"),
    # How a decision injection was judged: 'followed' | 'contradicted', or ''
    # for a row judged on some other surface, not yet judged, or evaluated
    # before this column existed. See :meth:`decision_feedback_totals`.
    ("verdict", "TEXT NOT NULL DEFAULT ''"),
    # Which repowise build emitted the row ("<version>+<install digest>"); see
    # ``repowise.cli.hook_ledger.emitting_build``. Empty on every row written
    # before this column existed, and that emptiness is the point: those are
    # exactly the rows whose attribution cannot be recovered.
    ("build", "TEXT NOT NULL DEFAULT ''"),
)


def _migrate_injections_columns(conn: sqlite3.Connection) -> None:
    """Best-effort ALTER for sidecars created before the ledger columns."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(injections)")}
    for name, decl in INJECTIONS_LEDGER_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE injections ADD COLUMN {name} {decl}")


_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Normalize a title for cross-session dedup (mirrors the crud dedup key)."""
    t = title.lower().strip()
    t = _NON_ALNUM_RE.sub("", t)
    return _WS_RE.sub(" ", t)


def title_key(title: str, lane: str = "") -> str:
    """Stable 16-hex staging key for a decision title within one *lane*.

    Lanes get separate key namespaces because folding is destructive: the merge
    path overwrites ``structured`` and keeps a ``user_correction`` kind sticky.
    A broad-discovery candidate whose title happened to normalize onto a gate
    hit's would replace that row's text while inheriting its one-observation
    promotion path, which is neither lane's rule.
    """
    payload = f"{lane}|{normalize_title(title)}" if lane else normalize_title(title)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def default_store_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".repowise" / SESSIONS_DIRNAME / SESSIONS_DB_FILENAME


class _DbCursors:
    """DB-backed transcript cursors with the :class:`CursorStore` surface.

    Mutation is in memory; :meth:`save` writes the rows on the shared
    connection and commits, so a cursor never lands without whatever the
    caller staged before calling it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._cursors: dict[str, dict[str, Any]] = {
            row[0]: {"offset": row[1], "mtime": row[2]}
            for row in conn.execute("SELECT file, offset, mtime FROM cursors")
        }

    def get(self, file: Path) -> dict[str, Any] | None:
        return self._cursors.get(str(file))

    def advance(self, file: Path, *, offset: int, mtime: float) -> None:
        self._cursors[str(file)] = {"offset": offset, "mtime": mtime}

    def save(self) -> None:
        self._conn.executemany(
            "INSERT INTO cursors (file, offset, mtime) VALUES (?, ?, ?) "
            "ON CONFLICT(file) DO UPDATE SET offset = excluded.offset, mtime = excluded.mtime",
            [(f, c["offset"], c["mtime"]) for f, c in self._cursors.items()],
        )
        self._conn.commit()


class SessionStagingStore:
    """Synchronous SQLite staging store for session-mined decisions.

    Synchronous on purpose, like the OmissionStore: the caller is a CLI
    update step where an asyncio loop around SQLite is pure overhead.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        _migrate_injections_columns(self._conn)
        self._conn.commit()
        self.cursors = _DbCursors(self._conn)

    @classmethod
    def open_default(cls, repo_path: Path) -> SessionStagingStore:
        return cls(default_store_path(repo_path))

    # -- raw candidates ------------------------------------------------------

    def add_raw(
        self,
        *,
        hash_: str,
        kind: str,
        quotes: list[str],
        files: list[str],
        session_id: str | None,
        now: float | None = None,
    ) -> bool:
        """Stage one gate hit; idempotent per content hash. True when new."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO raw_candidates "
            "(hash, kind, quotes, files, session_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                hash_,
                kind,
                json.dumps(quotes),
                json.dumps(files),
                session_id,
                now if now is not None else time.time(),
            ),
        )
        return cur.rowcount > 0

    def pending_raws(self, limit: int) -> list[dict[str, Any]]:
        """Raw candidates awaiting the LLM structuring pass.

        User corrections first (they carry the one-observation fast path),
        then dead ends, then choices; oldest first within a kind, so a
        cold-start backlog drains its highest-value candidates first.

        Broad-discovery rows are excluded: they arrive already structured and
        grounded against their own spans, and this query feeds a prompt that
        would re-derive them from the wrong evidence.
        """
        rows = self._conn.execute(
            "SELECT hash, kind, quotes, files, session_id FROM raw_candidates "
            "WHERE structured_key IS NULL AND kind <> ? "
            "ORDER BY CASE kind WHEN 'user_correction' THEN 0 WHEN 'dead_end' THEN 1 ELSE 2 END, "
            "created_at ASC LIMIT ?",
            (DISCOVERY_KIND, limit),
        ).fetchall()
        return [
            {
                "hash": r[0],
                "kind": r[1],
                "quotes": json.loads(r[2]),
                "files": json.loads(r[3]),
                "session_id": r[4],
            }
            for r in rows
        ]

    def mark_raw_rejected(self, hash_: str) -> None:
        """The LLM (or the substring gate) ruled this raw out; never retry it."""
        self._conn.execute("UPDATE raw_candidates SET structured_key = '' WHERE hash = ?", (hash_,))

    # -- discovery spans -----------------------------------------------------
    # The durable input queue for the one broad update-level discovery call.
    # Spans are written during the same transcript read that stages gate hits,
    # so they commit with the cursors; whatever does not fit one update's
    # budget stays pending and is served oldest-first by the next.

    def add_discovery_span(
        self,
        *,
        span_id: str,
        session_id: str,
        role: str,
        text: str,
        files: list[str],
        ts: float | None,
        now: float | None = None,
    ) -> bool:
        """Queue one prose span; idempotent per span id. True when new."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO discovery_spans "
            "(span_id, session_id, role, text, files, ts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                span_id,
                session_id,
                role,
                text,
                json.dumps(files),
                ts,
                now if now is not None else time.time(),
            ),
        )
        return cur.rowcount > 0

    def pending_discovery_spans(self, limit: int) -> list[dict[str, Any]]:
        """Unconsumed spans, oldest first, then in transcript order.

        Ordered by ``created_at`` before ``ts`` so a backlog drains in the
        order it accumulated rather than by whichever session happens to hold
        the oldest wall-clock turn.
        """
        rows = self._conn.execute(
            "SELECT span_id, session_id, role, text, files, ts "
            "FROM discovery_spans WHERE consumed_at IS NULL "
            "ORDER BY created_at ASC, ts ASC, span_id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "span_id": r[0],
                "session_id": r[1],
                "role": r[2],
                "text": r[3],
                "files": json.loads(r[4]),
                "ts": r[5],
            }
            for r in rows
        ]

    def pending_discovery_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM discovery_spans WHERE consumed_at IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0

    def mark_discovery_consumed(self, span_ids: list[str], *, now: float | None = None) -> None:
        """Retire spans that were actually put in front of the model."""
        ts = now if now is not None else time.time()
        self._conn.executemany(
            "UPDATE discovery_spans SET consumed_at = ? WHERE span_id = ?",
            [(ts, span_id) for span_id in span_ids],
        )

    def bump_discovery_attempts(self, span_ids: list[str], *, now: float | None = None) -> int:
        """Record a transient failure over *span_ids*; retire the exhausted.

        Returns how many spans hit :data:`MAX_SPAN_ATTEMPTS` and were retired,
        so the caller can report a permanent drop rather than a silent one.
        """
        ts = now if now is not None else time.time()
        self._conn.executemany(
            "UPDATE discovery_spans SET attempts = attempts + 1 WHERE span_id = ?",
            [(span_id,) for span_id in span_ids],
        )
        marks = ",".join("?" * len(span_ids))
        cur = self._conn.execute(
            f"UPDATE discovery_spans SET consumed_at = ? "
            f"WHERE consumed_at IS NULL AND attempts >= ? AND span_id IN ({marks})",
            (ts, MAX_SPAN_ATTEMPTS, *span_ids),
        )
        return cur.rowcount

    # -- structured decisions --------------------------------------------------

    def upsert_structured(
        self,
        raw_hash: str,
        *,
        kind: str,
        title: str,
        structured: dict[str, Any],
        quotes: list[str],
        files: list[str],
        session_id: str | None,
        lane: str = "",
        now: float | None = None,
    ) -> str:
        """Fold one structured candidate into its normalized-title row.

        Merges the observing session id, quotes, and files into the existing
        row (a ``user_correction`` kind is sticky: it carries the fast
        promotion path, so a later ``explicit_choice`` observation never
        weakens it) and links the raw row so it is not re-structured.
        Returns the decision key.
        """
        ts = now if now is not None else time.time()
        key = title_key(title, lane)
        row = self._conn.execute(
            "SELECT kind, sessions, quotes, files FROM decisions WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            sessions = [session_id] if session_id else []
            self._conn.execute(
                "INSERT INTO decisions "
                "(key, kind, title, structured, sessions, quotes, files, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    kind,
                    title,
                    json.dumps(structured),
                    json.dumps(sessions),
                    json.dumps(quotes),
                    json.dumps(files),
                    ts,
                    ts,
                ),
            )
        else:
            merged_kind = "user_correction" if "user_correction" in (kind, row[0]) else row[0]
            sessions = json.loads(row[1])
            if session_id and session_id not in sessions:
                sessions = [*sessions, session_id][:_MAX_SESSIONS_TRACKED]
            merged_quotes = list(dict.fromkeys(json.loads(row[2]) + quotes))[:_MAX_SESSIONS_TRACKED]
            merged_files = list(dict.fromkeys(json.loads(row[3]) + files))
            self._conn.execute(
                "UPDATE decisions SET kind = ?, structured = ?, sessions = ?, quotes = ?, "
                "files = ?, last_seen = ? WHERE key = ?",
                (
                    merged_kind,
                    json.dumps(structured),
                    json.dumps(sessions),
                    json.dumps(merged_quotes),
                    json.dumps(merged_files),
                    ts,
                    key,
                ),
            )
        self._conn.execute(
            "UPDATE raw_candidates SET structured_key = ? WHERE hash = ?", (key, raw_hash)
        )
        return key

    # -- promotion ---------------------------------------------------------

    def structured_exists(self, title: str, lane: str = "") -> bool:
        """Whether *title* already folds into an existing row in *lane*."""
        row = self._conn.execute(
            "SELECT 1 FROM decisions WHERE key = ?", (title_key(title, lane),)
        ).fetchone()
        return row is not None

    def promotable(self) -> list[dict[str, Any]]:
        """Decisions that qualify for (re-)emission into decision_records.

        Qualifies when 2+ distinct sessions observed it, or on a single
        observation for a user correction (the fast path). Emits only when
        there is something new to say: never promoted before, or observed by
        more sessions than the last emission. A promoted decision is therefore not
        re-upserted (and can never resurrect a human status change) on every
        update.
        """
        rows = self._conn.execute(
            "SELECT key, kind, title, structured, sessions, quotes, files, "
            "promoted_at, emitted_sessions FROM decisions"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            sessions = json.loads(r[4])
            observations = max(1, len(sessions))
            qualifies = observations >= 2 or r[1] == "user_correction"
            if not qualifies:
                continue
            first_promotion = r[7] is None
            if not first_promotion and observations <= r[8]:
                continue
            out.append(
                {
                    "key": r[0],
                    "kind": r[1],
                    "title": r[2],
                    "structured": json.loads(r[3]),
                    "sessions": sessions,
                    "quotes": json.loads(r[5]),
                    "files": json.loads(r[6]),
                    "first_promotion": first_promotion,
                    "observations": observations,
                }
            )
        return out

    def mark_emitted(self, key: str, *, observations: int, now: float | None = None) -> None:
        self._conn.execute(
            "UPDATE decisions SET promoted_at = COALESCE(promoted_at, ?), "
            "emitted_sessions = ? WHERE key = ?",
            (now if now is not None else time.time(), observations, key),
        )

    # -- injections (usage feedback v1) --------------------------------------
    # The rows themselves are written by the augment hooks with raw stdlib
    # sqlite3 (the hook path never imports repowise.core); these methods are
    # the update-time reader side.

    #: Surfaces whose ledger ids are ``decision_records`` primary keys, and so
    #: can be judged by looking the record up. Every other surface is judged by
    #: the transcript classifier in :mod:`repowise.core.sessions.efficacy`.
    #: Pre-column rows (surface '') are all decision injections.
    DECISION_SURFACES = ("", "decision")

    def unevaluated_injections(
        self, *, before: float, surfaces: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        """Shown-hook rows not yet judged, old enough that the showing
        session has plausibly moved past the guidance (see *before*).

        *surfaces* defaults to :data:`DECISION_SURFACES`, the only rows whose
        ``decision_id`` resolves to a decision record. Pass ``()`` for every
        surface — what the transcript classifier does, since it judges a firing
        by what the agent did next rather than by looking anything up.
        """
        scope = self.DECISION_SURFACES if surfaces is None else surfaces
        where = "evaluated = 0 AND shown_at < ?"
        params: list[Any] = [before]
        if scope:
            where += f" AND surface IN ({','.join('?' * len(scope))})"
            params.extend(scope)
        rows = self._conn.execute(
            "SELECT session_id, decision_id, node_id, shown_at, surface, category "
            f"FROM injections WHERE {where} ORDER BY shown_at ASC",
            params,
        ).fetchall()
        return [
            {
                "session_id": r[0],
                "decision_id": r[1],
                "node_id": r[2],
                "shown_at": r[3],
                "surface": r[4],
                "category": r[5],
            }
            for r in rows
        ]

    def record_firing(
        self,
        *,
        session_id: str,
        key: str,
        surface: str,
        category: str,
        node_id: str = "",
        chars: int = 0,
        shown_at: float,
        duration_ms: int = 0,
        acted: bool | None = None,
    ) -> None:
        """Upsert one classified hook firing (the transcript-side writer).

        The live hooks insert their own row the moment they fire; this both
        backfills firings that predate the ledger and settles the columns the
        hook could not know — ``acted`` (which needs the following tool calls)
        and the true end-to-end ``duration_ms`` (which only the harness
        measures). An existing row keeps its ``shown_at`` and ``chars``: the
        hook recorded those first-hand.

        Every row written here is ``evaluated``, including the surfaces whose
        verdict is "no action was called for" (*acted* ``None``, which stores
        as 0). Those are told apart by ``(surface, category)`` at report time
        via :data:`~repowise.core.sessions.efficacy.NO_ACTION_EXPECTED`, so an
        unjudgeable firing is not re-examined on every update forever.
        """
        self._conn.execute(
            "INSERT INTO injections "
            "(session_id, decision_id, node_id, shown_at, surface, category, chars, "
            "duration_ms, acted, evaluated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(session_id, decision_id) DO UPDATE SET "
            "duration_ms = MAX(excluded.duration_ms, injections.duration_ms), "
            "acted = excluded.acted, evaluated = 1",
            (
                session_id,
                key,
                node_id,
                shown_at,
                surface,
                category,
                chars,
                duration_ms,
                1 if acted else 0,
            ),
        )

    def clear_surfaces(self, surfaces: tuple[str, ...]) -> int:
        """Drop every ledger row on *surfaces*. Returns the rows removed.

        Only ever used to rebuild transcript-derived surfaces from scratch,
        where the transcripts are the source of truth and the rows are
        reconstructed in the same command. Decision rows carry state that has
        no other home (a promotion's emit bookkeeping) and are never passed
        here.
        """
        if not surfaces:
            return 0
        cur = self._conn.execute(
            f"DELETE FROM injections WHERE surface IN ({','.join('?' * len(surfaces))})",
            surfaces,
        )
        return cur.rowcount

    def efficacy_rows(self) -> list[dict[str, Any]]:
        """Per (surface, category) firing counts, action rates and latency."""
        rows = self._conn.execute(
            "SELECT surface, category, COUNT(*), SUM(acted), SUM(evaluated), "
            "COUNT(DISTINCT session_id), SUM(chars), "
            "SUM(CASE WHEN duration_ms > 0 THEN duration_ms ELSE 0 END), "
            "SUM(CASE WHEN duration_ms > 0 THEN 1 ELSE 0 END) "
            "FROM injections GROUP BY surface, category ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [
            {
                "surface": r[0] or "decision",
                "category": r[1] or "",
                "firings": r[2],
                "acted": r[3] or 0,
                "evaluated": r[4] or 0,
                "sessions": r[5],
                "chars": r[6] or 0,
                "duration_ms_total": r[7] or 0,
                "duration_ms_count": r[8] or 0,
            }
            for r in rows
        ]

    def rewrite_run_totals(self) -> list[dict[str, Any]]:
        """What the PreToolUse rewrite hook did, per outcome and reason.

        The rewrite hook's only instrument. An ``updatedInput`` rewrite never
        appears in a transcript and neither does a passthrough, so before these
        rows the busiest hook surface reported nothing at all and its bail
        distribution was inference.
        """
        try:
            rows = self._conn.execute(
                "SELECT outcome, reason, SUM(calls), COUNT(DISTINCT session_id), SUM(total_ms) "
                "FROM rewrite_runs GROUP BY outcome, reason ORDER BY SUM(calls) DESC"
            ).fetchall()
        except sqlite3.Error:
            return []  # sidecar predates the table: no rewrite rows, not an error
        return [
            {
                "outcome": r[0],
                "reason": r[1],
                "calls": r[2] or 0,
                "sessions": r[3] or 0,
                "total_ms": r[4] or 0,
            }
            for r in rows
        ]

    def injection_builds(self) -> list[dict[str, Any]]:
        """Which repowise builds emitted the rows in this ledger, busiest first.

        More than one live build here means two installs are emitting into the
        same repo, and the rows above them are not one population: a surface
        deleted in one install can still be firing from the other. Rows written
        before the ``build`` column are grouped under ``""`` and labelled as
        unattributable rather than folded into whichever build is current.
        """
        rows = self._conn.execute(
            "SELECT build, COUNT(*), COUNT(DISTINCT session_id), MAX(shown_at) "
            "FROM injections GROUP BY build ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [
            {"build": r[0] or "", "firings": r[1], "sessions": r[2], "last_seen": r[3] or 0.0}
            for r in rows
        ]

    def session_duration_totals(self) -> list[int]:
        """Total hook wall-time per session, in ms, for sessions that have it."""
        rows = self._conn.execute(
            "SELECT SUM(duration_ms) FROM injections WHERE duration_ms > 0 "
            "GROUP BY session_id"
        ).fetchall()
        return [int(r[0]) for r in rows if r[0]]

    def hook_run_totals(self) -> list[dict[str, Any]]:
        """Per-session hook invocation counts and in-process wall time.

        The counterpart to :meth:`efficacy_rows`, and the only source for what
        the *silent* invocations cost: a hook that returns nothing leaves no
        transcript record at all, so the emissions ledger cannot see the calls
        that make up most of the bill.
        """
        rows = self._conn.execute(
            "SELECT session_id, SUM(calls), SUM(emitted), SUM(total_ms) "
            "FROM hook_runs GROUP BY session_id"
        ).fetchall()
        return [
            {"session_id": r[0], "calls": r[1] or 0, "emitted": r[2] or 0, "total_ms": r[3] or 0}
            for r in rows
        ]

    def hook_run_by_tool(self) -> list[dict[str, Any]]:
        """Invocation counts and in-process wall time per hook event and tool."""
        rows = self._conn.execute(
            "SELECT event, tool, SUM(calls), SUM(emitted), SUM(total_ms) "
            "FROM hook_runs GROUP BY event, tool ORDER BY SUM(total_ms) DESC"
        ).fetchall()
        return [
            {
                "event": r[0],
                "tool": r[1],
                "calls": r[2] or 0,
                "emitted": r[3] or 0,
                "total_ms": r[4] or 0,
            }
            for r in rows
        ]

    def mark_injection_evaluated(
        self, session_id: str, decision_id: str, *, verdict: str = ""
    ) -> None:
        """Settle one injection row, recording *verdict* when there is one.

        A row judged with no verdict (the decision record is gone, so there is
        nothing to judge against) is still marked evaluated so it is not
        re-examined forever — it just doesn't count towards either side.
        """
        self._conn.execute(
            "UPDATE injections SET evaluated = 1, verdict = ? "
            "WHERE session_id = ? AND decision_id = ?",
            (verdict, session_id, decision_id),
        )

    def retire_unjudgeable_verdicts(self) -> int:
        """Drop ``followed`` from rows nothing could have contradicted.

        ``followed`` used to be the else branch of the contradiction test, so
        every injection into a session with no mined correction earned one for
        free, and those rows are already ``evaluated`` — the live judgement
        never reads them again. Without this the reported rate stays pinned at
        whatever the else branch produced, which on this machine was all 106
        of them at 100%.

        **Runs exactly once per store**, gated on ``PRAGMA user_version``
        rather than repeated every pass, and that is not a cost decision. The
        test it applies — the showing session mined no ``user_correction`` —
        is only true-forever for rows written under the old rule. Run
        perpetually it would also retire *earned* verdicts, on two paths: as
        :data:`RAW_TTL_DAYS` prunes the corrections that justified them, and
        as a session's only correction turns out to be a repeat of one already
        staged under another session (see ``SessionCandidate.hash``). Either
        way a real "followed" would decay to no-verdict and the rate would
        understate itself a little more each quarter. Returns the number of
        rows retired, and 0 on a store that has already had it.
        """
        if self._conn.execute("PRAGMA user_version").fetchone()[0] >= _VERDICT_REPAIR_VERSION:
            return 0
        placeholders = ",".join("?" * len(self.DECISION_SURFACES))
        cur = self._conn.execute(
            f"UPDATE injections SET verdict = '' WHERE surface IN ({placeholders}) "
            "AND verdict = 'followed' AND NOT EXISTS ("
            "SELECT 1 FROM raw_candidates rc WHERE rc.session_id = injections.session_id "
            "AND rc.kind = 'user_correction')",
            self.DECISION_SURFACES,
        )
        # PRAGMA takes no parameters, hence the interpolation of an int constant.
        self._conn.execute(f"PRAGMA user_version = {_VERDICT_REPAIR_VERSION}")
        return cur.rowcount or 0

    def decision_feedback_totals(self) -> dict[str, int]:
        """Counts of decision injections by followed-vs-contradicted verdict.

        Covers :data:`DECISION_SURFACES` only — the surfaces whose rows are
        judged against the decision records rather than by the transcript
        classifier. ``pending`` is rows awaiting the next update's judgement;
        ``no_verdict`` is rows already settled without one — most of them
        injections no session could have disagreed with, because no correction
        was mined from any session that saw them (see
        :func:`~repowise.core.sessions.miners.decisions.apply_injection_feedback`),
        plus a drained orphan (the decision record was gone) or a row judged
        before this column existed. None of it is recoverable, so all of it is
        reported rather than quietly folded into one of the two real verdicts,
        which is exactly how the followed rate came to read 100%.
        """
        placeholders = ",".join("?" * len(self.DECISION_SURFACES))
        rows = self._conn.execute(
            "SELECT verdict, evaluated, COUNT(*) FROM injections "
            f"WHERE surface IN ({placeholders}) GROUP BY verdict, evaluated",
            self.DECISION_SURFACES,
        ).fetchall()
        totals = {"followed": 0, "contradicted": 0, "pending": 0, "no_verdict": 0}
        for verdict, evaluated, count in rows:
            if verdict in ("followed", "contradicted"):
                totals[verdict] += count
            elif evaluated:
                totals["no_verdict"] += count
            else:
                totals["pending"] += count
        return totals

    def correction_quotes(self, session_id: str) -> list[str]:
        """Verbatim user-correction quotes mined from one session's transcript."""
        rows = self._conn.execute(
            "SELECT quotes FROM raw_candidates WHERE session_id = ? AND kind = 'user_correction'",
            (session_id,),
        ).fetchall()
        out: list[str] = []
        for (raw,) in rows:
            try:
                quotes = json.loads(raw)
            except (TypeError, ValueError):
                continue
            out.extend(q for q in quotes if isinstance(q, str))
        return out

    # -- lifecycle -----------------------------------------------------------

    def prune(self, *, now: float | None = None) -> None:
        """Drop spent rows past the TTL (see :data:`RAW_TTL_DAYS`).

        Discovery spans are dropped only once *consumed*: the candidate they
        produced keeps its own copy of the quote, so the raw prose is only the
        input queue. A span still waiting for a call is never pruned, however
        old, because ageing out unread input is exactly the silent backlog loss
        the queue exists to prevent.
        """
        cutoff = (now if now is not None else time.time()) - RAW_TTL_DAYS * 86400.0
        self._conn.execute(
            "DELETE FROM raw_candidates WHERE structured_key IS NULL AND created_at < ?",
            (cutoff,),
        )
        self._conn.execute(
            "DELETE FROM discovery_spans WHERE consumed_at IS NOT NULL AND consumed_at < ?",
            (cutoff,),
        )

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SessionStagingStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
