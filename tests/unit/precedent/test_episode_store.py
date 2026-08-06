"""Unit tests for the episode store (``repowise.core.precedent.store``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.precedent.store import (
    SHAREABLE_TIERS,
    SOURCE_GONE_NOTE,
    TIER_GIT,
    TIER_STRUCTURAL,
    TIER_TRANSCRIPT,
    Episode,
    EpisodeStore,
    default_store_path,
)


def _episode(kind: str = "nested_repos", subject: str = ".", body: str = "a") -> Episode:
    return Episode(
        tier=TIER_STRUCTURAL,
        kind=kind,
        subject=subject,
        body=body,
        evidence="e",
        nodes=("backend",),
    )


def _store(tmp_path: Path) -> EpisodeStore:
    (tmp_path / ".repowise").mkdir(exist_ok=True)
    return EpisodeStore.open_for_repo(tmp_path)


class TestReplaceKinds:
    def test_writes_and_reads_back(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            rows = store.list_episodes(tier=TIER_STRUCTURAL)
        assert len(rows) == 1
        assert rows[0]["kind"] == "nested_repos"
        assert rows[0]["nodes"] == ["backend"]

    def test_rederiving_preserves_birth(self, tmp_path: Path) -> None:
        """A fact that still holds keeps its birth; only the body may move."""
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode(body="first")],
                now=1000.0,
            )
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode(body="second")],
                now=2000.0,
            )
            rows = store.list_episodes()
        assert len(rows) == 1
        assert rows[0]["birth_at"] == 1000.0
        assert rows[0]["last_seen_at"] == 2000.0
        assert rows[0]["body"] == "second"

    def test_fact_that_stopped_holding_is_retired(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=1000.0
            )
            retired = store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[], now=2000.0
            )
        assert retired == 1
        with _store(tmp_path) as store:
            assert store.count() == 0

    def test_replace_is_scoped_to_named_kinds(self, tmp_path: Path) -> None:
        """An update that skips the formatter check must not delete its episode."""
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos", "formatter_drift"],
                episodes=[_episode(), _episode(kind="formatter_drift", subject="ruff format .")],
                now=1000.0,
            )
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode()],
                now=2000.0,
            )
            kinds = {row["kind"] for row in store.list_episodes()}
        assert kinds == {"nested_repos", "formatter_drift"}

    def test_no_kinds_is_a_no_op(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(tier=TIER_STRUCTURAL, kinds=[], episodes=[_episode()])
            assert store.count() == 0


class TestPrune:
    def test_ttl_drops_only_unseen_rows(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), ttl_days=0.0) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            # Written with a current timestamp, so a zero TTL must not evict it.
            assert store.count() == 1

    def test_row_cap_never_evicts_the_last_row(self, tmp_path: Path) -> None:
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=0) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            assert store.count() == 1

    def test_an_accumulating_tier_cannot_evict_the_cold_start_one(self, tmp_path: Path) -> None:
        """The cap is per tier.

        Structural facts are derived in the traverse phase, so a later
        tier-wide write leaves them holding the oldest ``last_seen_at`` in the
        store. Under a shared cap they would be first out of the door, and they
        are the only supply a first-ever index of a history-less repository
        has.
        """
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=3) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=1000.0
            )
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    Episode(
                        tier=TIER_GIT,
                        kind="code_fix",
                        subject=f"{i}",
                        body="b",
                        evidence="e",
                        nodes=("a.py",),
                        birth_at=float(i),
                    )
                    for i in range(10)
                ],
                oldest_birth_at=0.0,
                now=2000.0,
            )

            assert len(store.list_episodes(tier=TIER_STRUCTURAL)) == 1
            assert len(store.list_episodes(tier=TIER_GIT)) == 3

    def test_the_cap_evicts_the_oldest_claims_not_the_newest(self, tmp_path: Path) -> None:
        """One ``last_seen_at`` across a tier is a total tie.

        SQLite would fall back to insertion order, which is the walk's order,
        newest commit first. That would evict the most recent history from a
        layer whose subject is recent history.
        """
        (tmp_path / ".repowise").mkdir()
        with EpisodeStore(default_store_path(tmp_path), max_rows=2) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    Episode(
                        tier=TIER_GIT,
                        kind="code_fix",
                        subject=f"{i}",
                        body="b",
                        evidence="e",
                        nodes=("a.py",),
                        birth_at=float(i),
                    )
                    # Newest first, as the walk yields them.
                    for i in (5, 4, 3, 2, 1)
                ],
                oldest_birth_at=0.0,
            )

            kept = {row["birth_at"] for row in store.list_episodes(tier=TIER_GIT)}
            assert kept == {5.0, 4.0}

    def test_a_run_that_observed_nothing_does_not_refresh_the_tier(self, tmp_path: Path) -> None:
        """Without a trailing edge to vouch for, the touch is a false claim.

        A repository whose authors stop writing fix-shaped subjects yields no
        fixes on every later run. If those runs still marked the tier
        re-observed, the TTL would be answered by a pass that looked at no
        history and the rows would outlive every bound the store has.
        """
        (tmp_path / ".repowise").mkdir()
        episode = Episode(
            tier=TIER_GIT,
            kind="code_fix",
            subject="sha",
            body="b",
            evidence="e",
            nodes=("a.py",),
            birth_at=10.0,
        )
        with EpisodeStore(default_store_path(tmp_path), ttl_days=1.0) as store:
            store.append_tier(tier=TIER_GIT, episodes=[episode], oldest_birth_at=0.0, now=1000.0)

            store.append_tier(tier=TIER_GIT, episodes=[], oldest_birth_at=None, now=1060.0)
            assert [row["last_seen_at"] for row in store.list_episodes()] == [1000.0]

            store.prune(now=1000.0 + 2 * 86400)
            assert store.count() == 0


class TestPaths:
    def test_store_path_is_a_sidecar(self, tmp_path: Path) -> None:
        path = default_store_path(tmp_path)
        assert path.parent.parent.name == ".repowise"
        assert path.name == "episodes.db"
        assert not path.exists()  # resolving must not create anything


class TestAccumulateTier:
    """The third lifecycle: accumulate, and outlive the sources.

    The tier's sources are pruned by a harness on a schedule nobody sets, so an
    episode that dies with its source can only ever be as old as that schedule.
    The body is the content and the pointer is provenance, so the episode
    stays and the missing source becomes a note.
    """

    def _session(self, subject: str, body: str = "b", birth_at: float = 10.0) -> Episode:
        return Episode(
            tier=TIER_TRANSCRIPT,
            kind="session",
            subject=subject,
            body=body,
            evidence="e",
            nodes=("a.py",),
            birth_at=birth_at,
        )

    def test_a_present_but_unread_member_is_kept_and_re_observed(self, tmp_path: Path) -> None:
        """The case append_tier cannot express and replace_kinds would delete."""
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one"), self._session("two")],
                present_subjects=["one", "two"],
                now=1000.0,
            )
            # A later run reads nothing new but both sources are still there.
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=["one", "two"],
                now=2000.0,
            )
            rows = store.list_episodes(tier=TIER_TRANSCRIPT)
            assert {r["subject"] for r in rows} == {"one", "two"}
            assert {r["last_seen_at"] for r in rows} == {2000.0}
            # Re-observation must not reset the birth.
            assert {r["birth_at"] for r in rows} == {10.0}

    def test_a_member_whose_source_is_gone_is_kept_and_noted(self, tmp_path: Path) -> None:
        """The retention fix, stated as a test: the episode outlives its source."""
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one"), self._session("two")],
                present_subjects=["one", "two"],
                now=1000.0,
            )
            noted = store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=["one"],
                now=2000.0,
            )
            assert noted == 1
            rows = {r["subject"]: r for r in store.list_episodes(tier=TIER_TRANSCRIPT)}
            assert set(rows) == {"one", "two"}
            assert rows["two"]["evidence"].endswith(SOURCE_GONE_NOTE)
            assert SOURCE_GONE_NOTE not in rows["one"]["evidence"]
            # The body — the thing the episode is actually made of — is intact.
            assert rows["two"]["body"] == "b"

    def test_the_note_is_written_once_however_often_it_is_missed(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one"), self._session("two")],
                present_subjects=["one", "two"],
                now=1000.0,
            )
            noted = [
                store.accumulate_tier(
                    tier=TIER_TRANSCRIPT,
                    kind="session",
                    episodes=[],
                    present_subjects=["one"],
                    now=stamp,
                )
                for stamp in (2000.0, 3000.0, 4000.0)
            ]
            # Newly marked once, then nothing left to mark: the count is the
            # contract, and a note appended every run would still read as one
            # note if only the string were checked.
            assert noted == [1, 0, 0]
            (two,) = [
                r for r in store.list_episodes(tier=TIER_TRANSCRIPT) if r["subject"] == "two"
            ]
            assert two["evidence"].count(SOURCE_GONE_NOTE) == 1

    def test_a_source_that_comes_back_loses_the_note(self, tmp_path: Path) -> None:
        """A re-derivation rewrites the evidence, so the note is not sticky."""
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one"), self._session("two")],
                present_subjects=["one", "two"],
                now=1000.0,
            )
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=["one"],
                now=2000.0,
            )
            # Assert the transition, not just the destination: without this the
            # test passes on code that never writes a note at all.
            (gone,) = [
                r for r in store.list_episodes(tier=TIER_TRANSCRIPT) if r["subject"] == "two"
            ]
            assert SOURCE_GONE_NOTE in gone["evidence"]

            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("two")],
                present_subjects=["one", "two"],
                now=3000.0,
            )
            (two,) = [
                r for r in store.list_episodes(tier=TIER_TRANSCRIPT) if r["subject"] == "two"
            ]
            assert SOURCE_GONE_NOTE not in two["evidence"]

    def test_a_run_that_enumerated_nothing_annotates_nothing(self, tmp_path: Path) -> None:
        """No listing is "cannot tell", not "every source has gone"."""
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one")],
                present_subjects=["one"],
                now=1000.0,
            )
            assert (
                store.accumulate_tier(
                    tier=TIER_TRANSCRIPT,
                    kind="session",
                    episodes=[],
                    present_subjects=[],
                    now=2000.0,
                )
                == 0
            )
            (row,) = store.list_episodes(tier=TIER_TRANSCRIPT)
            assert SOURCE_GONE_NOTE not in row["evidence"]

    def test_it_leaves_other_tiers_and_kinds_alone(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=500.0
            )
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session("one")],
                present_subjects=["one"],
                now=1000.0,
            )
            assert len(store.list_episodes(tier=TIER_STRUCTURAL)) == 1
            assert len(store.list_episodes(tier=TIER_TRANSCRIPT)) == 1

    def test_a_membership_past_the_variable_limit_annotates_nothing(
        self, tmp_path: Path
    ) -> None:
        """The negation is all-or-nothing: a chunk of it would annotate live rows.

        SQLite's per-statement variable limit is what a heavy machine's
        transcript directory outruns, so the membership goes through a temp
        table instead of an ``IN`` list.
        """
        from repowise.core.precedent.store import _IN_CHUNK

        subjects = [f"s{i}" for i in range(_IN_CHUNK + 25)]
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[self._session(s) for s in subjects],
                present_subjects=subjects,
                now=1000.0,
            )
            noted = store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=subjects,
                now=2000.0,
            )
            rows = store.list_episodes(tier=TIER_TRANSCRIPT)
            assert noted == 0
            assert len(rows) == len(subjects)
            assert {r["last_seen_at"] for r in rows} == {2000.0}
            assert not [r for r in rows if SOURCE_GONE_NOTE in r["evidence"]]

    def test_the_transcript_tier_is_capped_without_touching_the_others(
        self, tmp_path: Path
    ) -> None:
        """The tier expected to grow largest is the reason the cap is per tier.

        Sessions accumulate for as long as the harness keeps their transcripts,
        so this is the tier that reaches the cap first; the cold-start facts it
        would otherwise evict are the only supply a history-less repo has.
        """
        (tmp_path / ".repowise").mkdir(exist_ok=True)
        with EpisodeStore(default_store_path(tmp_path), max_rows=3) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()], now=500.0
            )
            subjects = [f"s{i}" for i in range(10)]
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    self._session(s, birth_at=float(i)) for i, s in enumerate(subjects)
                ],
                present_subjects=subjects,
                now=1000.0,
            )

            assert len(store.list_episodes(tier=TIER_STRUCTURAL)) == 1
            kept = store.list_episodes(tier=TIER_TRANSCRIPT)
            assert len(kept) == 3
            # One stamp across the tier is a total tie, so birth has to break
            # it: the newest sessions are the ones worth keeping.
            assert {r["subject"] for r in kept} == {"s7", "s8", "s9"}

class TestListFilters:
    def test_tiers_is_an_allowlist(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject="one",
                        body="b",
                        evidence="e",
                    )
                ],
                present_subjects=["one"],
            )
            served = store.list_episodes(tiers=SHAREABLE_TIERS)
            assert {r["tier"] for r in served} == {TIER_STRUCTURAL}
            assert len(store.list_episodes()) == 2

    def test_an_empty_allowlist_selects_nothing(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[_episode()]
            )
            assert store.list_episodes(tiers=[]) == []
            assert store.list_episodes(subjects=[]) == []

    def test_the_transcript_tier_is_not_shareable(self) -> None:
        """Asserted rather than commented: this is the whole tier boundary."""
        assert TIER_TRANSCRIPT not in SHAREABLE_TIERS
        assert set(SHAREABLE_TIERS) == {TIER_STRUCTURAL, TIER_GIT}

    def test_too_many_subjects_is_refused_rather_than_silently_paged(
        self, tmp_path: Path
    ) -> None:
        from repowise.core.precedent.store import _IN_CHUNK

        with _store(tmp_path) as store, pytest.raises(ValueError, match="at most"):
            store.list_episodes(subjects=[f"s{i}" for i in range(_IN_CHUNK + 1)])


class TestSearch:
    """Ranked retrieval over the bodies.

    The tier this exists for holds one row per session with the session's prose
    in it, so the only way to ask it a question is to rank; a filter over node
    sets answers "what happened to this file", never "where did we discuss
    this".
    """

    def _episodes(self) -> list[Episode]:
        return [
            Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject="a.jsonl",
                body="the traverser skips nested git repositories while walking",
                evidence="e",
            ),
            Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject="b.jsonl",
                body="the embedder batches pages before calling the provider",
                evidence="e",
            ),
            Episode(
                tier=TIER_GIT,
                kind="code_fix",
                subject="fix the nested checkout walk",
                body="a nested checkout was being descended into",
                evidence="e",
            ),
        ]

    def _fill(self, store: EpisodeStore) -> None:
        store.accumulate_tier(
            tier=TIER_TRANSCRIPT,
            kind="session",
            episodes=[e for e in self._episodes() if e.tier == TIER_TRANSCRIPT],
            present_subjects=["a.jsonl", "b.jsonl"],
        )
        store.append_tier(
            tier=TIER_GIT,
            episodes=[e for e in self._episodes() if e.tier == TIER_GIT],
            oldest_birth_at=0.0,
        )

    def test_a_question_finds_the_episode_that_answers_it(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            self._fill(store)
            hits = store.search("how are nested repositories handled when walking the tree")
            assert hits
            assert hits[0]["subject"] == "a.jsonl"
            assert hits[0]["score"] > 0

    def test_tiers_filter_the_ranking_rather_than_the_query(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            self._fill(store)
            hits = store.search("nested repositories", tiers=[TIER_GIT])
            assert {h["tier"] for h in hits} == {TIER_GIT}
            assert store.search("nested repositories", tiers=[]) == []

    def test_a_deleted_episode_leaves_the_index(self, tmp_path: Path) -> None:
        """The triggers are what make this true without a second write path."""
        with _store(tmp_path) as store:
            self._fill(store)
            assert store.search("embedder batches")
            store.replace_kinds(tier=TIER_TRANSCRIPT, kinds=["session"], episodes=[])
            assert store.search("embedder batches") == []

    def test_an_edited_body_is_searchable_at_its_new_text(self, tmp_path: Path) -> None:
        """An upsert is an UPDATE, and a stale index would answer with old prose."""
        with _store(tmp_path) as store:
            self._fill(store)
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject="a.jsonl",
                        body="the traverser now consults the submodule manifest",
                        evidence="e",
                    )
                ],
                present_subjects=["a.jsonl", "b.jsonl"],
            )
            assert [h["subject"] for h in store.search("submodule manifest")] == ["a.jsonl"]
            assert not [h for h in store.search("nested git repositories") if h["subject"] == "a.jsonl"]

    def test_a_query_of_pure_punctuation_says_nothing_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """FTS5's grammar is a thing to escape; the shared builder is where that lives."""
        with _store(tmp_path) as store:
            self._fill(store)
            for hostile in ('" OR "', "*", "()", "  ", "NEAR(a b", 'walker"'):
                assert isinstance(store.search(hostile), list)

    def test_rows_written_before_the_index_existed_are_backfilled(
        self, tmp_path: Path
    ) -> None:
        """The upgrade path: an installed store predates its own search index."""
        import sqlite3

        with _store(tmp_path) as store:
            self._fill(store)
            path = store.db_path

        # Drop the index and its triggers the way a build without FTS5 leaves it.
        conn = sqlite3.connect(path)
        conn.executescript(
            "DROP TABLE episode_fts;"
            "DROP TRIGGER episodes_fts_ai;"
            "DROP TRIGGER episodes_fts_ad;"
            "DROP TRIGGER episodes_fts_au;"
        )
        conn.commit()
        conn.close()

        with EpisodeStore(path) as reopened:
            assert reopened.fts_enabled
            assert [h["subject"] for h in reopened.search("embedder batches")] == ["b.jsonl"]

    def test_no_index_is_silence_rather_than_an_error(self, tmp_path: Path) -> None:
        """A store that cannot build an index still answers every other read."""
        with _store(tmp_path) as store:
            self._fill(store)
            store.fts_enabled = False
            assert store.search("nested repositories") == []
            assert len(store.list_episodes()) == 3

    def test_the_session_answering_more_of_the_question_outranks_the_louder_one(
        self, tmp_path: Path
    ) -> None:
        """BM25 rewards repetition; a session repeats itself constantly.

        The measured failure this pins: a body saying one term of the question
        over and over outscored the body that carried every term of it, and the
        answer sat at rank 9. Distinct-term coverage is the tie the ranking was
        missing, and it fits no constant to any corpus.
        """
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject="loud.jsonl",
                        # One term of the question, forty times over.
                        body=" ".join(["staleness"] * 40),
                        evidence="e",
                    ),
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject="answer.jsonl",
                        body=(
                            "the staleness divisor needed fifteen commits in ninety "
                            "days before a record ever moved, so it was inert"
                        ),
                        evidence="e",
                    ),
                ],
                present_subjects=["loud.jsonl", "answer.jsonl"],
            )
            hits = store.search("why did staleness need fifteen commits in ninety days")
            assert hits[0]["subject"] == "answer.jsonl"

    def test_the_rerank_window_is_bounded(self, tmp_path: Path) -> None:
        """Every row in the window is a body this store scans, so it has a ceiling.

        Filled past the ceiling on purpose: with a handful of rows the corpus
        bounds the result and the assertion passes whether the cap exists or
        not, which is a test of the fixture rather than of the code.
        """
        from repowise.core.precedent.store import _RERANK_WINDOW_MAX

        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject=f"s{i}.jsonl",
                        body="the traverser skips nested git repositories",
                        evidence="e",
                    )
                    for i in range(_RERANK_WINDOW_MAX * 2)
                ],
                present_subjects=[f"s{i}.jsonl" for i in range(_RERANK_WINDOW_MAX * 2)],
            )
            assert store.count() > _RERANK_WINDOW_MAX
            assert len(store.search("nested repositories", limit=1000)) == _RERANK_WINDOW_MAX

    def test_a_noted_source_does_not_change_what_the_cap_evicts(self, tmp_path: Path) -> None:
        """Presence must not become an eviction signal, and the reason is the point.

        Every transcript is pruned by the harness eventually, so after a month
        *every* episode's source is gone. Ranking eviction by presence would
        therefore prefer whatever was recorded in the last thirty days — which
        is precisely the ceiling this tier was repaired to escape. Age decides,
        as it does for the other accumulating tier.
        """
        def session(subject: str, birth_at: float) -> Episode:
            return Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject=subject,
                body="b",
                evidence="e",
                birth_at=birth_at,
            )

        (tmp_path / ".repowise").mkdir(exist_ok=True)
        with EpisodeStore(default_store_path(tmp_path), max_rows=2) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    session("old-but-live", 1.0),
                    session("newer-but-gone", 2.0),
                    session("newest", 3.0),
                ],
                present_subjects=["old-but-live", "newest"],
                now=1000.0,
            )
            kept = {r["subject"] for r in store.list_episodes(tier=TIER_TRANSCRIPT)}
            assert kept == {"newer-but-gone", "newest"}

    def test_a_source_that_comes_back_unread_loses_the_note(self, tmp_path: Path) -> None:
        """The steady-state case, and the one a re-derivation test cannot reach.

        An episode is only re-derived when its source is *read*, and an old
        session has nothing new to read. If the note were cleared only on
        re-derivation, a single directory listing that missed a file would
        stamp it gone permanently while the file sat on disk.
        """
        def session(subject: str) -> Episode:
            return Episode(
                tier=TIER_TRANSCRIPT,
                kind="session",
                subject=subject,
                body="b",
                evidence="e",
            )

        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[session("one"), session("two")],
                present_subjects=["one", "two"],
                now=1000.0,
            )
            # One run misses it.
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=["one"],
                now=2000.0,
            )
            # The next run sees it again and reads nothing new from it.
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[],
                present_subjects=["one", "two"],
                now=3000.0,
            )
            rows = {r["subject"]: r for r in store.list_episodes(tier=TIER_TRANSCRIPT)}
            assert SOURCE_GONE_NOTE not in rows["two"]["evidence"]
            assert rows["two"]["evidence"] == "e"
