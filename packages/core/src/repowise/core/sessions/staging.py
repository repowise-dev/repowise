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
    PRIMARY KEY (session_id, decision_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_pending ON raw_candidates(structured_key)
    WHERE structured_key IS NULL;
"""

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Normalize a title for cross-session dedup (mirrors the crud dedup key)."""
    t = title.lower().strip()
    t = _NON_ALNUM_RE.sub("", t)
    return _WS_RE.sub(" ", t)


def title_key(title: str) -> str:
    """Stable 16-hex staging key for a decision title."""
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16]


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
        """
        rows = self._conn.execute(
            "SELECT hash, kind, quotes, files, session_id FROM raw_candidates "
            "WHERE structured_key IS NULL "
            "ORDER BY CASE kind WHEN 'user_correction' THEN 0 WHEN 'dead_end' THEN 1 ELSE 2 END, "
            "created_at ASC LIMIT ?",
            (limit,),
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
        key = title_key(title)
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

    def unevaluated_injections(self, *, before: float) -> list[dict[str, Any]]:
        """Shown-decision rows not yet judged, old enough that the showing
        session has plausibly moved past the guidance (see *before*)."""
        rows = self._conn.execute(
            "SELECT session_id, decision_id, node_id, shown_at FROM injections "
            "WHERE evaluated = 0 AND shown_at < ? ORDER BY shown_at ASC",
            (before,),
        ).fetchall()
        return [
            {"session_id": r[0], "decision_id": r[1], "node_id": r[2], "shown_at": r[3]}
            for r in rows
        ]

    def mark_injection_evaluated(self, session_id: str, decision_id: str) -> None:
        self._conn.execute(
            "UPDATE injections SET evaluated = 1 WHERE session_id = ? AND decision_id = ?",
            (session_id, decision_id),
        )

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
        """Drop never-structured raws past the TTL (see :data:`RAW_TTL_DAYS`)."""
        cutoff = (now if now is not None else time.time()) - RAW_TTL_DAYS * 86400.0
        self._conn.execute(
            "DELETE FROM raw_candidates WHERE structured_key IS NULL AND created_at < ?",
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
