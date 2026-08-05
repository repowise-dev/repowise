"""EpisodeStore — one row per dated thing that happened to this repo.

Lives in its own SQLite sidecar (``.repowise/episodes/episodes.db``, WAL)
rather than wiki.db, the pattern :mod:`repowise.core.distill.store` set and
:mod:`repowise.core.sessions.staging` already followed: index-time writes
never contend with hook-time reads, and a corrupt episode index degrades this
feature rather than the product.

An episode is a claim with a birth, a scope and a body kept whole — which is
what a metric cannot have. ``tier`` says where it came from and whether it is
shareable: ``structural`` and ``git`` episodes are facts about the repository
and travel with it; ``transcript`` episodes are per-machine and never feed a
stored value another surface reads.

Stdlib only, deliberately: a hook reads this store under a 155 ms budget, and
that budget has already been lost once to module-level imports.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

EPISODES_DIRNAME = "episodes"
EPISODES_DB_FILENAME = "episodes.db"

#: Tiers, in availability order. Only the first two are shareable.
TIER_STRUCTURAL = "structural"
TIER_GIT = "git"
TIER_TRANSCRIPT = "transcript"

#: Rows not re-observed for this long are dropped. Every index refreshes
#: ``last_seen_at`` for a fact that still holds, so this evicts only episodes
#: whose repository has stopped being indexed — never a live one. (Contrast
#: the OmissionStore, whose TTL is over *creation* because its rows are a
#: transient stash.)
DEFAULT_TTL_DAYS = 90.0
#: Row-count cap; oldest-seen rows pruned first when exceeded.
DEFAULT_MAX_ROWS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    evidence TEXT NOT NULL,
    nodes TEXT NOT NULL,
    birth_commit TEXT,
    birth_at REAL NOT NULL,
    last_seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_tier_kind ON episodes(tier, kind);
CREATE INDEX IF NOT EXISTS idx_episodes_last_seen ON episodes(last_seen_at);
"""


def default_store_path(repo_path: Path | str) -> Path:
    """Path to *repo_path*'s episode database (not created here)."""
    return Path(repo_path) / ".repowise" / EPISODES_DIRNAME / EPISODES_DB_FILENAME


def episode_id(tier: str, kind: str, subject: str) -> str:
    """Stable identity for an episode: same claim about the same subject."""
    raw = "\x00".join((tier, kind, subject)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class Episode:
    """One dated thing that happened, kept whole.

    ``subject`` discriminates episodes within a kind and is part of the
    identity, so re-deriving the same fact updates a row instead of adding
    one. ``nodes`` is the repo-relative file set the claim is bound to — the
    scope a later staleness query asks git about.
    """

    tier: str
    kind: str
    subject: str
    body: str
    evidence: str
    nodes: tuple[str, ...] = field(default_factory=tuple)
    birth_commit: str | None = None

    @property
    def id(self) -> str:
        return episode_id(self.tier, self.kind, self.subject)


class EpisodeStore:
    """Synchronous SQLite store for episodes.

    Synchronous on purpose, like the OmissionStore and the session staging
    store: callers are CLI commands, index phases and hooks, where an asyncio
    loop around SQLite is pure overhead.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        ttl_days: float = DEFAULT_TTL_DAYS,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.max_rows = max_rows
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @classmethod
    def open_for_repo(cls, repo_path: Path | str) -> EpisodeStore:
        """Open the store for *repo_path*.

        The caller is responsible for checking that the repo opted in (a
        ``.repowise`` directory exists); this never creates one where a user
        has not run ``repowise init``.
        """
        return cls(default_store_path(repo_path))

    # -- writes ------------------------------------------------------------

    def replace_kinds(
        self,
        *,
        tier: str,
        kinds: Sequence[str],
        episodes: Iterable[Episode],
        now: float | None = None,
    ) -> int:
        """Make *kinds* within *tier* say exactly what *episodes* say.

        Upsert-then-sweep rather than delete-then-insert, so ``birth_at``
        survives on a fact that still holds — an episode whose birth resets on
        every index is a metric wearing a record's clothes.

        Scoped to *kinds* on purpose: a run that derives only some kinds (an
        incremental update skips the ones that cost a subprocess) must not
        delete the episodes it did not look for. Returns rows retired.
        """
        if not kinds:
            return 0
        stamp = time.time() if now is None else now
        rows = [
            (
                ep.id,
                ep.tier,
                ep.kind,
                ep.subject,
                ep.body,
                ep.evidence,
                json.dumps(list(ep.nodes)),
                ep.birth_commit,
                stamp,
                stamp,
            )
            for ep in episodes
        ]
        with self._conn:  # one transaction: never half-replaced
            if rows:
                self._conn.executemany(
                    """
                    INSERT INTO episodes
                        (id, tier, kind, subject, body, evidence, nodes,
                         birth_commit, birth_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        body = excluded.body,
                        evidence = excluded.evidence,
                        nodes = excluded.nodes,
                        birth_commit = excluded.birth_commit,
                        last_seen_at = excluded.last_seen_at
                    """,
                    rows,
                )
            placeholders = ",".join("?" * len(kinds))
            cur = self._conn.execute(
                f"DELETE FROM episodes WHERE tier = ? AND kind IN ({placeholders}) "
                "AND last_seen_at < ?",
                (tier, *kinds, stamp),
            )
            retired = cur.rowcount or 0
        self.prune(now=stamp)
        return retired

    def prune(self, *, now: float | None = None) -> None:
        """Drop rows past TTL, then oldest-seen until under the row cap.

        *now* is the write's own stamp when pruning follows a write: reading
        the wall clock instead would make a row written microseconds earlier
        older than the cutoff it is measured against.
        """
        cutoff = (time.time() if now is None else now) - self.ttl_days * 86400
        with self._conn:
            self._conn.execute("DELETE FROM episodes WHERE last_seen_at < ?", (cutoff,))
            while True:
                (rows,) = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
                # Never evict the last row: it may be the one just written.
                if rows <= max(self.max_rows, 1) or rows <= 1:
                    break
                self._conn.execute(
                    """
                    DELETE FROM episodes WHERE id IN (
                        SELECT id FROM episodes ORDER BY last_seen_at ASC
                        LIMIT MIN(64, (SELECT COUNT(*) - 1 FROM episodes))
                    )
                    """
                )

    # -- reads -------------------------------------------------------------

    def list_episodes(self, *, tier: str | None = None, kind: str | None = None) -> list[dict]:
        """Episodes, newest birth first. Both filters are optional."""
        sql = (
            "SELECT id, tier, kind, subject, body, evidence, nodes, "
            "birth_commit, birth_at, last_seen_at FROM episodes"
        )
        clauses: list[str] = []
        params: list[object] = []
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY birth_at DESC, id ASC"
        return [_row_to_dict(row) for row in self._conn.execute(sql, params)]

    def count(self) -> int:
        (rows,) = self._conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
        return int(rows)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EpisodeStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _row_to_dict(row: tuple) -> dict:
    try:
        nodes = json.loads(row[6])
    except (TypeError, ValueError):
        nodes = []
    return {
        "id": row[0],
        "tier": row[1],
        "kind": row[2],
        "subject": row[3],
        "body": row[4],
        "evidence": row[5],
        "nodes": nodes,
        "birth_commit": row[7],
        "birth_at": row[8],
        "last_seen_at": row[9],
    }
