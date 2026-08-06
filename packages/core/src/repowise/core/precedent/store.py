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

#: The tiers that describe the repository rather than the machine, and so may
#: be served to somebody who did not derive them. A reader that would put an
#: episode in front of a user asks for these by name: two people asking one
#: question of one repository must get one answer, and a transcript episode
#: exists only on the laptop that recorded it.
SHAREABLE_TIERS = (TIER_STRUCTURAL, TIER_GIT)

#: Rows not re-observed for this long are dropped. Every index refreshes
#: ``last_seen_at`` for a fact that still holds, so this evicts only episodes
#: whose repository has stopped being indexed — never a live one. (Contrast
#: the OmissionStore, whose TTL is over *creation* because its rows are a
#: transient stash.)
DEFAULT_TTL_DAYS = 90.0
#: Row-count cap; oldest-seen rows pruned first when exceeded.
DEFAULT_MAX_ROWS = 5000
#: Bound on one ``IN`` list, so a large membership set stays one statement per
#: chunk rather than one variable past whatever SQLite was compiled with.
_IN_CHUNK = 500

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

    ``birth_at`` is normally left unset and stamped by the store at first
    write, which is right for a fact derived from the checkout: it was first
    observed when it was first derived. An episode whose birth is a matter of
    record rather than of observation — a commit, which happened at a time the
    walk can read — passes its own.
    """

    tier: str
    kind: str
    subject: str
    body: str
    evidence: str
    nodes: tuple[str, ...] = field(default_factory=tuple)
    birth_commit: str | None = None
    birth_at: float | None = None

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
        with self._conn:  # one transaction: never half-replaced
            self._upsert(episodes, stamp)
            placeholders = ",".join("?" * len(kinds))
            cur = self._conn.execute(
                f"DELETE FROM episodes WHERE tier = ? AND kind IN ({placeholders}) "
                "AND last_seen_at < ?",
                (tier, *kinds, stamp),
            )
            retired = cur.rowcount or 0
        self.prune(now=stamp)
        return retired

    def append_tier(
        self,
        *,
        tier: str,
        episodes: Iterable[Episode],
        oldest_birth_at: float | None = None,
        now: float | None = None,
    ) -> int:
        """Add to *tier* without replacing what is already in it.

        The writer for a tier whose members accumulate. :meth:`replace_kinds`
        makes a kind say exactly what a run derived, which is right for facts
        re-derived whole every index; it is wrong here, because an incremental
        run sees only the commits it has never seen before and its sweep would
        delete every episode written by the runs before it.

        Three things, one transaction:

        * *episodes* are upserted, so a re-derived member keeps its birth;
        * rows born before *oldest_birth_at* are dropped, which is how the
          tier stays bounded by the window it is derived from rather than by
          the TTL;
        * and then, **only then**, every row in *tier* is marked re-observed,
          because the pass that found these members looked at the whole window
          and the TTL is over ``last_seen_at``. Without it a tier whose window
          is wider than the TTL loses live episodes between full indexes.

        *oldest_birth_at* being ``None`` says the caller cannot vouch for a
        trailing edge, and it suppresses the touch as well as the drop. The two
        belong together: a run that observed nothing must not claim to have
        re-observed the tier, or the TTL is answered by a pass that looked at
        no history and rows outlive every bound the store has.

        Returns rows dropped for falling out of the window.
        """
        stamp = time.time() if now is None else now
        with self._conn:
            self._upsert(episodes, stamp)
            dropped = 0
            if oldest_birth_at is not None:
                cur = self._conn.execute(
                    "DELETE FROM episodes WHERE tier = ? AND birth_at < ?",
                    (tier, oldest_birth_at),
                )
                dropped = cur.rowcount or 0
                self._conn.execute(
                    "UPDATE episodes SET last_seen_at = ? WHERE tier = ?", (stamp, tier)
                )
        self.prune(now=stamp)
        return dropped

    def sync_tier(
        self,
        *,
        tier: str,
        kind: str,
        episodes: Iterable[Episode],
        present_subjects: Sequence[str],
        now: float | None = None,
    ) -> int:
        """Accumulate into *tier*, bounded by what still exists.

        The third lifecycle, and the reason it is not one of the other two:

        * :meth:`replace_kinds` says *this run derived the whole kind*, which
          is false here — a run reads only the transcript bytes appended since
          the last one, so a sweep would delete every session it did not
          revisit;
        * :meth:`append_tier` says *this run covered a window and everything in
          it is re-observed*, which is also false — the pass has no window, and
          nothing about reading today's session says last March's still exists.

        What is true, and true of no other tier, is that this one's membership
        can be **enumerated without being read**: the sources are files on
        disk, so a run knows which episodes still have something behind them
        for the cost of the directory listing it already did. That is the
        vouch. *present_subjects* are marked re-observed whether or not they
        were read this run — which is what stops a live episode dying of the
        TTL simply because its session ended — and the rest of *kind* is
        dropped, because its source is gone rather than merely quiet.

        Returns rows dropped for having no source left.
        """
        stamp = time.time() if now is None else now
        ids = [episode_id(tier, kind, s) for s in present_subjects]
        with self._conn:  # one transaction: never half-synced
            self._upsert(episodes, stamp)
            # Chunked: a heavy machine's transcript directory can outrun
            # SQLite's per-statement variable limit, and a partial IN list
            # would read as "these are gone" and delete live episodes.
            for start in range(0, len(ids), _IN_CHUNK):
                batch = ids[start : start + _IN_CHUNK]
                placeholders = ",".join("?" * len(batch))
                self._conn.execute(
                    f"UPDATE episodes SET last_seen_at = ? WHERE id IN ({placeholders})",
                    (stamp, *batch),
                )
            cur = self._conn.execute(
                "DELETE FROM episodes WHERE tier = ? AND kind = ? AND last_seen_at < ?",
                (tier, kind, stamp),
            )
            dropped = cur.rowcount or 0
        self.prune(now=stamp)
        return dropped

    def _upsert(self, episodes: Iterable[Episode], stamp: float) -> None:
        """Insert or refresh *episodes*. Caller owns the transaction.

        ``birth_at`` is deliberately absent from the conflict clause: a claim
        that still holds keeps the birth it was first written with, which is
        what separates an episode from a value recomputed every index.
        """
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
                stamp if ep.birth_at is None else ep.birth_at,
                stamp,
            )
            for ep in episodes
        ]
        if not rows:
            return
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

    def prune(self, *, now: float | None = None) -> None:
        """Drop rows past TTL, then trim each tier to the row cap.

        *now* is the write's own stamp when pruning follows a write: reading
        the wall clock instead would make a row written microseconds earlier
        older than the cutoff it is measured against.

        **The cap is per tier, not per store.** One tier accumulates members
        (a repository's history) while another holds a handful of facts
        re-derived every index — and the structural handful is the only supply
        a first-ever index of a history-less repository has. Under a shared cap
        the accumulating tier evicts the cold-start one, and does so first:
        structural facts are derived in the traverse phase, so a later
        tier-wide write leaves them with the oldest ``last_seen_at`` in the
        store and puts them at the front of the deletion queue.
        """
        cutoff = (time.time() if now is None else now) - self.ttl_days * 86400
        with self._conn:
            self._conn.execute("DELETE FROM episodes WHERE last_seen_at < ?", (cutoff,))
            tiers = [row[0] for row in self._conn.execute("SELECT DISTINCT tier FROM episodes")]
            for tier in tiers:
                self._trim_tier(tier)

    def _trim_tier(self, tier: str) -> None:
        """Evict *tier*'s least current rows until it is under the cap.

        The ordering carries a tie-break for a reason: :meth:`append_tier`
        stamps a whole tier with one ``last_seen_at``, so within it the primary
        key is a total tie and SQLite would fall back to insertion order — the
        walk's order, newest commit first. That evicts the most recent history
        from a layer whose whole subject is recent history. ``birth_at``
        settles it the right way round.
        """
        cap = max(self.max_rows, 1)
        while True:
            (rows,) = self._conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE tier = ?", (tier,)
            ).fetchone()
            # Never evict the last row: it may be the one just written.
            if rows <= cap or rows <= 1:
                return
            # The excess, not the whole tier minus one: batching by the latter
            # overshoots the cap by up to a batch on the way down, and empties
            # the tier outright when the cap is small.
            batch = min(64, rows - cap, rows - 1)
            self._conn.execute(
                """
                DELETE FROM episodes WHERE id IN (
                    SELECT id FROM episodes WHERE tier = ?
                    ORDER BY last_seen_at ASC, birth_at ASC
                    LIMIT ?
                )
                """,
                (tier, batch),
            )

    # -- reads -------------------------------------------------------------

    def list_episodes(
        self,
        *,
        tier: str | None = None,
        tiers: Sequence[str] | None = None,
        kind: str | None = None,
        subjects: Sequence[str] | None = None,
    ) -> list[dict]:
        """Episodes, newest birth first. Every filter is optional and ANDed.

        *tiers* is the allowlist form, for a reader that must name the tiers it
        is willing to serve rather than take whatever the store happens to
        hold — a new tier is then invisible to it until somebody decides
        otherwise, which is the opposite of the default it replaced.

        An empty *tiers* or *subjects* selects nothing, which is the honest
        reading of an empty allowlist and not the same as ``None``.
        """
        sql = (
            "SELECT id, tier, kind, subject, body, evidence, nodes, "
            "birth_commit, birth_at, last_seen_at FROM episodes"
        )
        clauses: list[str] = []
        params: list[object] = []
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        if tiers is not None:
            if not tiers:
                return []
            clauses.append(f"tier IN ({','.join('?' * len(tiers))})")
            params.extend(tiers)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if subjects is not None:
            if not subjects:
                return []
            if len(subjects) > _IN_CHUNK:
                # Chunking a SELECT would mean stitching pages back into one
                # ordering; the callers that filter by subject ask about the
                # handful they are writing, so refuse rather than mislead.
                raise ValueError(
                    f"subjects filter takes at most {_IN_CHUNK} values, got {len(subjects)}"
                )
            clauses.append(f"subject IN ({','.join('?' * len(subjects))})")
            params.extend(subjects)
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
