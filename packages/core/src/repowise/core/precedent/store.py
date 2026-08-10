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
import logging
import posixpath
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from repowise.core.fts_query import build_fts5_query, meaningful_terms

_log = logging.getLogger(__name__)

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

# Pinned, because a hand-written TypeScript union mirrors this set
# (``packages/types/src/episodes.ts``) and the HTTP layer narrows a response
# field to it. Adding a tier here without widening that union ships a type
# that lies to every consumer, and nothing on either side would fail. Change
# both, or neither.
assert SHAREABLE_TIERS == (TIER_STRUCTURAL, TIER_GIT)

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

#: How much wider than the caller's limit the BM25 window is drawn, so the
#: rerank has something to reorder, and the hard ceiling on it, because every
#: row in the window is a body this store then scans.
_RERANK_FACTOR = 5
_RERANK_WINDOW_MAX = 50

#: Appended to an episode's evidence the first time a run finds the source it
#: was derived from gone. The episode itself stays: the body is the content and
#: the pointer was only ever provenance.
SOURCE_GONE_NOTE = " (source no longer on disk)"

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

#: ``nodes`` normalised one row per path, so "which episodes are bound here"
#: is an index seek instead of a scan that JSON-decodes every row in the store.
#:
#: Measured on this repository before it existed: scanning ``nodes`` for one
#: path costs 1.3 ms across the 284 shareable rows here, but 22.6 ms at the
#: store's own 5,000-row cap, against 0.2 ms through this index. Three readers
#: made that worth normalising rather than two — ``get_why``, the counts on
#: ``get_risk``/``get_context``, and a hook, where the plan's constraint is not
#: a preference: hook delivery is one indexed lookup, because the budget is
#: 155 ms and was fought down from 965 ms.
#:
#: Deletes are a trigger and writes are not, which is the split the data
#: forces rather than a style choice: the store has five delete paths (a kind
#: sweep, a window drop, the TTL, the cap trim, and a re-derivation replacing a
#: row) and a trigger catches all of them without needing to parse JSON, while
#: an insert has the node list already in hand in Python and would otherwise
#: need ``json_each`` — a build-dependent extension this store's stdlib-only
#: rule cannot assume.
_NODES_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_nodes (
    episode_id TEXT NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (episode_id, path)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_episode_nodes_path ON episode_nodes(path);
"""

#: Created **last**, after the backfill has succeeded, which is what makes its
#: presence mean "this index is complete" rather than "somebody started
#: building one". DDL runs in autocommit, so the table is durable the moment it
#: is created and a backfill that then fails — a disk error, or ``SQLITE_BUSY``
#: from another process past the 5 s timeout — would otherwise leave a table
#: that is present, empty, and never rebuilt, because presence was the only
#: signal. That is a store answering "no episodes are bound here" in exactly
#: the shape of the truth. The search index gets away with a presence check
#: because its triggers cover insert, update *and* delete, so its objects
#: cannot exist while its contents are wrong; this one can, so it needs a
#: signal that means the contents are right.
_NODES_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS episodes_nodes_ad AFTER DELETE ON episodes BEGIN
    DELETE FROM episode_nodes WHERE episode_id = old.id;
END;
"""

#: The one object whose presence vouches for the whole index. See above.
_NODES_COMPLETE_MARKER = "episodes_nodes_ad"

#: The searchable projection of an episode. External-content rather than a
#: table of its own: ``body`` is already a column and this corpus is 11 MB on
#: a real machine, so a standalone index would keep a second copy of all of it
#: for nothing. The columns are named after the ones they shadow, which is what
#: lets SQLite read them back out of ``episodes`` on a hit.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS episode_fts USING fts5(
    subject, body, content='episodes', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS episodes_fts_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episode_fts(rowid, subject, body)
        VALUES (new.rowid, new.subject, new.body);
END;
CREATE TRIGGER IF NOT EXISTS episodes_fts_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episode_fts(episode_fts, rowid, subject, body)
        VALUES ('delete', old.rowid, old.subject, old.body);
END;
CREATE TRIGGER IF NOT EXISTS episodes_fts_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episode_fts(episode_fts, rowid, subject, body)
        VALUES ('delete', old.rowid, old.subject, old.body);
    INSERT INTO episode_fts(rowid, subject, body)
        VALUES (new.rowid, new.subject, new.body);
END;
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
        self.fts_enabled = self._ensure_fts()
        self.node_index_enabled = self._ensure_node_index()

    def _ensure_fts(self) -> bool:
        """Create the search index and refill it if it is behind. Never raises.

        Modelled on ``persistence/search.py:_upgrade_sqlite_schema``, whose one
        binding rule is that it must not raise: every command opens the store
        through it, so an exception over an index would take down a product
        that is still perfectly usable without one. Here the stake is smaller
        and the rule is the same — a store that cannot build an index still
        answers every read the guard and the hooks make of it.

        Refill is one statement rather than a re-derivation. The rows are the
        system of record and ``body`` is a column of them, so FTS5's own
        ``rebuild`` reads what a hand-written refill would have selected.

        Drift is detected by which objects are missing, not by comparing row
        counts: an external-content table reads through to ``episodes`` on a
        scan, so ``count(*)`` answers with the content table's size and reports
        an index holding nothing as full. Presence is the honest signal, and it
        is enough — once the triggers exist no drift can accumulate behind them.
        """
        try:
            existing = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE name IN "
                    "('episode_fts', 'episodes_fts_ai', 'episodes_fts_ad', 'episodes_fts_au')"
                )
            }
            self._conn.executescript(_FTS_SCHEMA)
            if len(existing) < 4:
                # A store written before this index existed, or by a build
                # without FTS5, or one whose objects somebody dropped.
                self._conn.execute("INSERT INTO episode_fts(episode_fts) VALUES ('rebuild')")
            self._conn.commit()
            return True
        except sqlite3.Error:
            self._conn.rollback()
            _log.debug("episode search index unavailable", exc_info=True)
            return False

    def _ensure_node_index(self) -> bool:
        """Create the node index and refill it if it is behind. Never raises.

        Same contract as :meth:`_ensure_fts` and for the same reason: every
        caller opens the store through here, so an exception over an index
        would take down a feature that reads perfectly well without one. A
        store where this returns ``False`` still answers every node query —
        :meth:`_scan_by_node` is the fallback, and it is the implementation
        this index replaced.

        Completion, not presence, is the signal — see :data:`_NODES_TRIGGER`.
        The refill reads ``episodes.nodes``, which stays the system of record,
        so this table is derived data that can be dropped at any time and
        rebuilt on next open. That is what makes the migration for existing
        stores "none needed": a user who never re-indexes gets the index built
        the first time anything opens their store.
        """
        try:
            complete = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (_NODES_COMPLETE_MARKER,)
            ).fetchone()
            self._conn.executescript(_NODES_SCHEMA)
            if not complete:
                self._backfill_node_index()
                self._conn.commit()
                # Only now, with the contents known good, is the index vouched
                # for. A failure above leaves no marker and the next open
                # rebuilds from scratch.
                self._conn.executescript(_NODES_TRIGGER)
            self._conn.commit()
            return True
        except sqlite3.Error:
            self._conn.rollback()
            _log.debug("episode node index unavailable", exc_info=True)
            return False

    def _backfill_node_index(self) -> None:
        """Rebuild every node row from ``episodes.nodes``. Caller owns the commit."""
        self._conn.execute("DELETE FROM episode_nodes")
        pairs: list[tuple[str, str]] = []
        for episode_id_, raw in self._conn.execute("SELECT id, nodes FROM episodes"):
            pairs.extend((episode_id_, path) for path in _decode_nodes(raw))
        if pairs:
            self._conn.executemany(
                "INSERT OR IGNORE INTO episode_nodes (episode_id, path) VALUES (?, ?)", pairs
            )

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

    def accumulate_tier(
        self,
        *,
        tier: str,
        kind: str,
        episodes: Iterable[Episode],
        present_subjects: Sequence[str],
        now: float | None = None,
    ) -> int:
        """Accumulate into *tier*, outliving the sources the episodes came from.

        The third lifecycle, and the reason it is not one of the other two:

        * :meth:`replace_kinds` says *this run derived the whole kind*, which
          is false here — a run reads only the bytes appended since the last
          one, so a sweep would delete every source it did not revisit;
        * :meth:`append_tier` says *this run covered a window and everything in
          it is re-observed*, which is also false — the pass has no window.

        What replaces both is that an episode is **self-contained**: the body
        holds the prose, so the source pointer is provenance and not content.
        The source is therefore free to disappear, and it will. The harness
        this tier reads from prunes its transcripts at 30 days by default,
        counted here at 1,509 files whose age distribution stops dead at 30 —
        so an earlier version of this method, which read "the file is gone" as
        "the episode is gone", capped the tier at the harness's retention and
        could never support the one claim the tier exists to make. Being the
        only durable copy is the strongest thing the store has to say.

        What bounds the tier instead is what bounds the other accumulating one:
        the TTL and the per-tier row cap. Every run marks the whole kind
        re-observed, because a run of the index is what vouches that this
        repository is still live; the TTL then evicts a tier whose repository
        has stopped being indexed altogether, which is what it is for.

        *present_subjects* survives the change with a smaller job. It is still
        the cheap enumeration — the sources are files on disk, so a listing
        answers it without a read — but a missing source is now recorded as a
        **note on the row** rather than acted on, so a reader can say the
        quotation outlived what it was quoted from instead of following a
        pointer into nothing. An empty *present_subjects* is no vouch at all
        (a run on a machine that never had the sources looks identical to one
        whose sources all vanished), so it annotates nothing.

        Returns rows newly marked as having lost their source.
        """
        stamp = time.time() if now is None else now
        with self._conn:  # one transaction: never half-written
            self._upsert(episodes, stamp)
            self._conn.execute(
                "UPDATE episodes SET last_seen_at = ? WHERE tier = ? AND kind = ?",
                (stamp, tier, kind),
            )
            noted = self._note_missing_sources(tier, kind, present_subjects)
        self.prune(now=stamp)
        return noted

    def _note_missing_sources(
        self, tier: str, kind: str, present_subjects: Sequence[str]
    ) -> int:
        """Make the note match what is on disk, in both directions. In-transaction.

        A temporary table rather than a ``NOT IN`` list, because this is a
        negation and a negation cannot be chunked: a partial list would read as
        "everything else is gone" and annotate live rows. The same reasoning
        that made the positive form chunk-safe makes this form all-or-nothing.

        The note is **cleared as well as written**, and the asymmetry it fixes
        is not hypothetical. Clearing it only on re-derivation would leave the
        note stuck forever on the ordinary case: a source has to be *read* to
        be re-derived, an old session has nothing new to read, and one
        directory listing that misses a file — a transient failure, no deletion
        needed — would then mark it gone permanently while the file sat there.
        Presence is a fact about now, so the row is made to agree with now.
        """
        if not present_subjects:
            return 0
        ids = [(episode_id(tier, kind, s),) for s in present_subjects]
        self._conn.execute("CREATE TEMP TABLE IF NOT EXISTS present_ids (id TEXT PRIMARY KEY)")
        self._conn.execute("DELETE FROM present_ids")
        self._conn.executemany("INSERT OR IGNORE INTO present_ids (id) VALUES (?)", ids)
        cur = self._conn.execute(
            # ``instr`` rather than LIKE: the marker is a literal, and a LIKE
            # pattern is a thing to escape even when it is a constant today.
            "UPDATE episodes SET evidence = evidence || ? "
            "WHERE tier = ? AND kind = ? AND instr(evidence, ?) = 0 "
            "AND id NOT IN (SELECT id FROM present_ids)",
            (SOURCE_GONE_NOTE, tier, kind, SOURCE_GONE_NOTE),
        )
        noted = cur.rowcount or 0
        self._conn.execute(
            "UPDATE episodes SET evidence = replace(evidence, ?, '') "
            "WHERE tier = ? AND kind = ? AND instr(evidence, ?) > 0 "
            "AND id IN (SELECT id FROM present_ids)",
            (SOURCE_GONE_NOTE, tier, kind, SOURCE_GONE_NOTE),
        )
        return noted

    def _upsert(self, episodes: Iterable[Episode], stamp: float) -> None:
        """Insert or refresh *episodes*. Caller owns the transaction.

        ``birth_at`` is deliberately absent from the conflict clause: a claim
        that still holds keeps the birth it was first written with, which is
        what separates an episode from a value recomputed every index.

        *episodes* is materialised before use because it is typed as an
        iterable and two passes are made over it — the rows and then the node
        index. Every caller today passes a list, so this is cheap insurance
        against a generator silently writing episodes with no scope.
        """
        episodes = list(episodes)
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
        self._sync_nodes(episodes)

    def _sync_nodes(self, episodes: Iterable[Episode], /) -> None:
        """Make the node index agree with what :meth:`_upsert` just wrote.

        Delete-then-insert per episode rather than an upsert, because a
        re-derived claim may name **fewer** nodes than it did last time and an
        insert-only sync would leave the dropped ones bound forever — a scope
        that can only grow is how a record ends up matching a file it stopped
        being about. Scoped to the ids in hand, so this costs the run's own
        episodes and not the store.

        The insert side lives here and the delete side is a trigger; see
        :data:`_NODES_SCHEMA` for why they are split.

        Node filtering goes through the same decoder a rebuild uses, so the
        index is a pure function of ``episodes.nodes``. Filtering on truthiness
        here while :func:`_decode_nodes` also requires ``str`` let a
        non-string node into the index on the live path and out of it on a
        rebuild — the two answers differing is worse than either.
        """
        ids = [(ep.id,) for ep in episodes]
        if not ids:
            return
        self._conn.executemany("DELETE FROM episode_nodes WHERE episode_id = ?", ids)
        pairs = [(ep.id, path) for ep in episodes for path in _valid_nodes(ep.nodes)]
        if pairs:
            self._conn.executemany(
                "INSERT OR IGNORE INTO episode_nodes (episode_id, path) VALUES (?, ?)", pairs
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

    def _filter(
        self,
        *,
        tier: str | None,
        tiers: Sequence[str] | None,
        kind: str | None,
        subjects: Sequence[str] | None,
    ) -> tuple[str, list[object]] | None:
        """The shared ``WHERE`` for the filtered reads, or None to select nothing.

        ``None`` rather than an empty clause, because an empty *tiers* or
        *subjects* allowlist selects nothing and a caller that turned that into
        ``WHERE 1=1`` would serve the whole store on an empty allowlist — the
        exact inversion the allowlist exists to prevent.
        """
        clauses: list[str] = []
        params: list[object] = []
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier)
        if tiers is not None:
            if not tiers:
                return None
            clauses.append(f"tier IN ({','.join('?' * len(tiers))})")
            params.extend(tiers)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if subjects is not None:
            if not subjects:
                return None
            if len(subjects) > _IN_CHUNK:
                # Chunking a SELECT would mean stitching pages back into one
                # ordering; the callers that filter by subject ask about the
                # handful they are writing, so refuse rather than mislead.
                raise ValueError(
                    f"subjects filter takes at most {_IN_CHUNK} values, got {len(subjects)}"
                )
            clauses.append(f"subject IN ({','.join('?' * len(subjects))})")
            params.extend(subjects)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def list_episodes(
        self,
        *,
        tier: str | None = None,
        tiers: Sequence[str] | None = None,
        kind: str | None = None,
        subjects: Sequence[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        with_body: bool = True,
    ) -> list[dict]:
        """Episodes, newest birth first. Every filter is optional and ANDed.

        *tiers* is the allowlist form, for a reader that must name the tiers it
        is willing to serve rather than take whatever the store happens to
        hold — a new tier is then invisible to it until somebody decides
        otherwise, which is the opposite of the default it replaced.

        An empty *tiers* or *subjects* selects nothing, which is the honest
        reading of an empty allowlist and not the same as ``None``.

        *limit* / *offset* page it, and either works alone — an *offset* with
        no *limit* means "the rest", not "everything", which is the trap the
        obvious implementation sets. Unpaged is still the default because the
        three in-process callers want the whole selection, but a caller
        serving a page must bound it: the per-tier cap is
        :data:`DEFAULT_MAX_ROWS` and git bodies run to hundreds of characters
        each, so the unbounded read is multi-megabyte at the ceiling.

        Ordering is ``(birth_at DESC, id ASC)`` and ``id`` is the primary key,
        so it is a total order and paging cannot duplicate or skip a row when
        births tie. There is no index on ``birth_at``, so each page sorts the
        filtered set in a temp b-tree — affordable only because of that cap,
        and cheaper with *with_body* off.

        *with_body* set false drops the ``body`` key entirely rather than
        blanking it, so a reader cannot mistake "not fetched" for "empty". The
        column is never read — the projection substitutes a constant — which
        is the point: a timeline row needs the date, the subject and the
        scope, and the body belongs to whatever opens one.
        """
        built = self._filter(tier=tier, tiers=tiers, kind=kind, subjects=subjects)
        if built is None:
            return []
        where, params = built
        body_col = "body" if with_body else "''"
        sql = (
            f"SELECT id, tier, kind, subject, {body_col}, evidence, nodes, "
            f"birth_commit, birth_at, last_seen_at FROM episodes{where}"
            " ORDER BY birth_at DESC, id ASC"
        )
        if limit is not None or offset:
            # SQLite has no bare OFFSET, so an offset without a limit needs a
            # sentinel. -1 is its own "unbounded" and is the honest one here;
            # a caller paging from an offset with no limit means "the rest",
            # not "nothing". Clamped limits stay >= 0 so a negative limit
            # returns no rows rather than inheriting that unbounded meaning.
            bound = -1 if limit is None else max(0, limit)
            sql += " LIMIT ? OFFSET ?"
            params = [*params, bound, max(0, offset)]
        rows = [_row_to_dict(row) for row in self._conn.execute(sql, params)]
        if not with_body:
            for row in rows:
                del row["body"]
        return rows

    def group_counts(
        self, column: str, *, tiers: Sequence[str] | None = None
    ) -> dict[str, int]:
        """``COUNT(*) GROUP BY column``, for a caller that needs a breakdown.

        *column* is checked against a literal allowlist rather than escaped: it
        is interpolated into the statement because a column name cannot be
        bound as a parameter, and an allowlist is the only form of that which
        is safe by construction rather than by review.

        Grouped in SQL rather than counted in Python, because the caller that
        wants this is rendering a summary and the alternative is materialising
        every row in the store to add up two integers.
        """
        if column not in {"tier", "kind"}:
            raise ValueError(f"group_counts takes 'tier' or 'kind', got {column!r}")
        built = self._filter(tier=None, tiers=tiers, kind=None, subjects=None)
        if built is None:
            return {}
        where, params = built
        sql = f"SELECT {column}, COUNT(*) FROM episodes{where} GROUP BY {column}"
        return {str(name): int(n) for name, n in self._conn.execute(sql, params)}

    def get_episode(
        self, episode_id: str, *, tiers: Sequence[str] | None = None
    ) -> dict | None:
        """One episode by id, or None — including when its tier is not allowed.

        The tier allowlist is applied here rather than by the caller because
        forgetting it on a by-id read is the same disclosure as forgetting it
        on a list, with none of the visibility: one row, addressable by a
        stable hash, is exactly the shape somebody guesses at.

        Built through :meth:`_filter` rather than with its own tier clause, so
        there is exactly one implementation of "which tiers may be read". A
        second copy here is how a later change to the allowlist reaches the
        list and misses the by-id read.
        """
        built = self._filter(tier=None, tiers=tiers, kind=None, subjects=None)
        if built is None:
            return None
        where, params = built
        clause = f"{where} AND id = ?" if where else " WHERE id = ?"
        sql = (
            "SELECT id, tier, kind, subject, body, evidence, nodes, "
            f"birth_commit, birth_at, last_seen_at FROM episodes{clause}"
        )
        row = self._conn.execute(sql, [*params, episode_id]).fetchone()
        return _row_to_dict(row) if row else None

    def count_episodes(
        self,
        *,
        tier: str | None = None,
        tiers: Sequence[str] | None = None,
        kind: str | None = None,
        subjects: Sequence[str] | None = None,
    ) -> int:
        """How many episodes the same filters select.

        Separate from :meth:`count` because a paged reader has to state a total
        it measured rather than the length of the page it happened to fetch,
        and separate from counting the page for the reason the decisions
        endpoint already learned: a capped list reported "97 of 100" on a
        repository holding several hundred records.
        """
        built = self._filter(tier=tier, tiers=tiers, kind=kind, subjects=subjects)
        if built is None:
            return 0
        where, params = built
        (rows,) = self._conn.execute(
            f"SELECT COUNT(*) FROM episodes{where}", params
        ).fetchone()
        return int(rows)

    def search(
        self,
        query: str,
        *,
        tiers: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Episodes matching *query*, best first, each with its BM25 ``score``.

        Ranked rather than filtered, which is the difference between this and
        :meth:`list_episodes`: a question is a paraphrase of the episode that
        answers it, so the expression is built by the same shared builder the
        wiki index uses (:mod:`repowise.core.fts_query`) — it is where the
        reduction to ``[a-zA-Z0-9_]+`` lives, and reimplementing that escaping
        is how a store learns about FTS5's grammar the expensive way.

        BM25 orders the candidates and a second pass reorders them by how many
        **distinct** question terms the body carries, BM25 breaking the ties.
        Measured on this repository's own store against a grep-established
        answer key: 74% to 84% hit@3, 58% to 68% hit@1. The reason is the shape
        of the document — a session is long and repeats itself, so a body
        mentioning one term of the question forty times outscores one that
        answers all four, and term frequency is exactly what BM25 rewards.
        Counting distinct terms fits no constant to this corpus; the signal is
        the question's own vocabulary.

        The opposite lever was tried first and is recorded because it looked
        obvious: passing the wiki index's document-frequency ceiling made this
        *worse* (74% to 68%), since a term in a fifth of the sessions is
        ordinary here rather than uninformative.

        Coverage counts a term as present by substring, so ``cat`` is found
        inside ``concatenate``. That is loose, and it is what the numbers above
        were measured with: tightening it to word boundaries is a change to the
        thing being measured, not a free correctness fix, so it belongs with a
        re-run rather than with a tidy-up.

        Returns nothing rather than raising when there is no index, and the
        same for a query FTS5 will not parse: a search that cannot run is a
        feature that says nothing, never a caller that has to handle it.
        """
        if not self.fts_enabled or not query.strip():
            return []
        if tiers is not None and not tiers:
            return []
        # Deliberately without the document-frequency ceiling the wiki index
        # passes here. Measured on this store: applying it moved r1's
        # grep-grounded regression from 74% to 68% hit@3, and pushed the
        # episode holding every rare term of a question from rank 9 to 11.
        # A session body is long and mentions many things, so a term in a fifth
        # of the sessions is ordinary rather than uninformative, and matching
        # more of a question's vocabulary discriminates better than matching
        # only its rarest words. The ceiling is a property of the wiki corpus's
        # vocabulary, not a general truth, and it does not transfer.
        expression = build_fts5_query(query)
        sql = (
            "SELECT e.id, e.tier, e.kind, e.subject, e.body, e.evidence, e.nodes, "
            "e.birth_commit, e.birth_at, e.last_seen_at, bm25(episode_fts) AS score "
            "FROM episode_fts JOIN episodes e ON e.rowid = episode_fts.rowid "
            "WHERE episode_fts MATCH ?"
        )
        params: list[object] = [expression]
        if tiers is not None:
            sql += f" AND e.tier IN ({','.join('?' * len(tiers))})"
            params.extend(tiers)
        # BM25 is negative and more negative is better, so plain ascending
        # order is best-first; the sign is flipped on the way out.
        sql += " ORDER BY score LIMIT ?"
        # Reranking can only reorder what BM25 hands it, so the window is wider
        # than the caller asked for — and bounded, because every row in it is a
        # body this method then scans.
        window = max(1, limit) * _RERANK_FACTOR
        params.append(min(window, _RERANK_WINDOW_MAX))
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            _log.debug("episode search failed for %r", query, exc_info=True)
            return []

        hits = [{**_row_to_dict(row), "score": -(row[10] or 0.0)} for row in rows]
        terms = set(meaningful_terms(query))
        if terms:
            hits.sort(
                key=lambda h: (
                    -sum(1 for t in terms if t in h["body"].casefold()),
                    -h["score"],
                )
            )
        return hits[: max(1, limit)]

    def count_by_node(
        self,
        paths: Sequence[str],
        *,
        tiers: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """How many episodes each of *paths* is bound to. Never raises.

        A count and nothing else, which is the whole point of the surfaces that
        call it: an integer invites a follow-up call, while a paragraph spends
        every caller's budget whether they wanted it or not.

        One indexed query per path rather than one for all of them: the callers
        ask about a handful of targets and want the answer *per* target, so a
        single grouped query would have to be un-grouped again on the way out.

        Returns only the paths with at least one episode, so a caller can omit
        the field rather than serve a zero.
        """
        if not paths:
            return {}
        if tiers is not None and not tiers:
            return {}
        if not self.node_index_enabled:
            scanned = self._scan_by_node(paths, tiers, with_body=False)
            return {p: len(rows) for p, rows in scanned.items()}
        counts: dict[str, int] = {}
        for path in paths:
            sub, params = self._node_subquery(path)
            if sub is None:
                continue
            sql = f"SELECT COUNT(*) FROM episodes e WHERE e.id IN ({sub})"
            if tiers is not None:
                sql += f" AND e.tier IN ({','.join('?' * len(tiers))})"
                params = [*params, *tiers]
            try:
                (n,) = self._conn.execute(sql, params).fetchone()
            except sqlite3.Error:
                _log.debug("episode node count failed for %r", path, exc_info=True)
                continue
            if n:
                counts[path] = int(n)
        return counts

    def list_by_node(
        self,
        paths: Sequence[str],
        *,
        tiers: Sequence[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Episodes bound to any of *paths*, newest birth first. Never raises.

        The bodied form of :meth:`count_by_node`, for a reader that asked a
        question rather than one that is annotating a card. Ordered by birth so
        a caller taking the first *limit* takes the most recent claims, which is
        the ordering a reader of "what happened here" expects.
        """
        if not paths:
            return []
        if tiers is not None and not tiers:
            return []
        if not self.node_index_enabled:
            seen: dict[str, dict] = {}
            for rows in self._scan_by_node(paths, tiers).values():
                for row in rows:
                    seen[row["id"]] = row
            ordered = sorted(
                seen.values(), key=lambda r: (-(r.get("birth_at") or 0.0), r["id"])
            )
            return ordered[: max(1, limit)]
        subs: list[str] = []
        params: list[object] = []
        for path in paths:
            sub, sub_params = self._node_subquery(path)
            if sub is None:
                continue
            subs.append(f"e.id IN ({sub})")
            params.extend(sub_params)
        if not subs:
            return []
        sql = (
            "SELECT e.id, e.tier, e.kind, e.subject, e.body, e.evidence, "
            "e.nodes, e.birth_commit, e.birth_at, e.last_seen_at FROM episodes e "
            f"WHERE ({' OR '.join(subs)})"
        )
        if tiers is not None:
            sql += f" AND e.tier IN ({','.join('?' * len(tiers))})"
            params.extend(tiers)
        sql += " ORDER BY e.birth_at DESC, e.id ASC LIMIT ?"
        params.append(max(1, limit))
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            _log.debug("episode node lookup failed", exc_info=True)
            return []
        return [_row_to_dict(row) for row in rows]

    def _scan_by_node(
        self,
        paths: Sequence[str],
        tiers: Sequence[str] | None,
        *,
        with_body: bool = True,
    ) -> dict[str, list[dict]]:
        """The pre-index implementation, kept as the fallback. Never raises.

        Only reached when :meth:`_ensure_node_index` could not build the index
        — a read-only database, a disk error, a build without the objects. It
        costs a JSON decode per row (measured at 22.6 ms against this store's
        5,000-row cap, versus 1.6 ms indexed), which is the right trade against
        the alternative: answering "nothing is bound here" in the same shape as
        the truth, on a surface whose whole job is to say what happened.

        *with_body* exists for :meth:`count_by_node`, whose fallback otherwise
        materialises every body in the store to return integers and throw the
        text away — megabytes allocated per call at the cap, for nothing.
        """
        try:
            rows = self.list_episodes(tiers=tiers, with_body=with_body)
        except sqlite3.Error:
            _log.debug("episode node scan failed", exc_info=True)
            return {}
        out: dict[str, list[dict]] = {}
        for path in paths:
            norm = _normalise_node_path(path)
            if not norm:
                continue
            hits = [row for row in rows if _covers_path(row.get("nodes") or [], norm)]
            if hits:
                out[path] = hits
        return out

    def _node_subquery(self, path: str) -> tuple[str | None, list[object]]:
        """A subquery selecting the ids bound at, above or below *path*.

        Both directions are the question being asked, because a target is a
        file *or* a directory. An episode naming ``pkg/mod/file.py`` is about
        the module ``pkg/mod``; an episode naming the directory ``pkg`` is
        about every file in it. Matching one direction only would answer
        confidently and wrongly for whichever shape it left out.

        Written as ``id IN (SELECT ...)`` rather than a join, and that is a
        correctness-of-plan decision rather than a style one. The join form was
        measured first and SQLite drove it from ``episodes`` — filtering by
        tier and probing the node table per row — which scans the whole tier
        and never touches the path index, giving back exactly the cost this
        table exists to remove. The subquery makes the indexed lookup the
        driver. A query-plan test holds it there, because the join form was not
        *wrong*, only slow, and nothing else would have caught it.

        Ancestors are enumerated here and compared with equality, since a path
        has a handful of them. Descendants are a **half-open range** rather
        than a pattern, and that is a correctness fix rather than a
        micro-optimisation: ``GLOB`` reads ``[`` as a character class and
        SQLite's ``GLOB`` has no ``ESCAPE`` clause, so a framework's dynamic
        route — ``app/repos/[id]`` — silently matched ``app/repos/i`` and
        ``app/repos/d`` while missing every real child. 170 rows in this
        repository's own store carry bracketed paths. ``*`` and ``?`` in a
        path fail the same way. A range compares bytes and has no
        metacharacters at all, so there is nothing to escape and nothing to
        get wrong; ``/`` is ``0x2F`` and ``0`` is its successor, which is what
        makes the upper bound exact rather than approximate.
        """
        norm = _normalise_node_path(path)
        if not norm:
            return None, []
        # ``a/b/c.py`` is covered by an episode bound to it, to ``a/b`` or to
        # ``a`` — self and every ancestor.
        parts = norm.split("/")
        selves = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        sub = (
            "SELECT episode_id FROM episode_nodes WHERE "
            f"path IN ({','.join('?' * len(selves))}) OR (path >= ? AND path < ?)"
        )
        return sub, [*selves, f"{norm}/", f"{norm}0"]

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


def _normalise_node_path(path: str) -> str:
    """A repo-relative path in the one form the node index stores.

    Separators, redundant segments and stray slashes are all things a caller
    hands over without meaning anything by them: ``pkg\\mod``, ``pkg/mod/``,
    ``pkg//mod`` and ``pkg/x/../mod`` are one location, and matching them by
    bytes would answer three of the four with silence. ``..`` is resolved
    rather than rejected because an agent composing a path from a symbol's
    module does produce them.

    A path that climbs out of the repository has no node to match and returns
    empty, which callers read as "ask nothing".
    """
    norm = posixpath.normpath(str(path).replace("\\", "/")).strip("/")
    if not norm or norm == "." or norm.startswith("../"):
        return ""
    return norm


def _covers_path(nodes: Sequence[object], norm: str) -> bool:
    """True when *norm* is at, above or below one of *nodes*.

    The scan-side twin of :meth:`EpisodeStore._node_subquery`, and the two are
    tested against each other so the fallback cannot drift into answering a
    different question from the index.
    """
    for node in nodes:
        if not isinstance(node, str):
            continue
        n = _normalise_node_path(node)
        if not n:
            continue
        if norm == n or norm.startswith(f"{n}/") or n.startswith(f"{norm}/"):
            return True
    return False


def _valid_nodes(nodes: Iterable[object]) -> list[str]:
    """The usable paths in a node list. One predicate, both writers.

    Shared by the live write path and by a rebuild from the stored column, so
    the index cannot answer differently depending on which one last touched it.
    """
    return [n for n in nodes if isinstance(n, str) and n]


def _decode_nodes(raw: object) -> list[str]:
    """The node list a stored ``nodes`` column holds, or empty if unreadable.

    Tolerant on purpose: a row whose scope will not decode is still a claim
    worth serving repo-wide, and this runs on the read path.
    """
    try:
        nodes = json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    return _valid_nodes(nodes) if isinstance(nodes, list) else []


def _row_to_dict(row: tuple) -> dict:
    nodes = _decode_nodes(row[6])
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
