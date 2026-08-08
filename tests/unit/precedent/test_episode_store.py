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


def _bound(
    subject: str,
    nodes: tuple[str, ...],
    *,
    tier: str = TIER_GIT,
    birth_at: float = 1000.0,
) -> Episode:
    return Episode(
        tier=tier,
        kind="code_fix",
        subject=subject,
        body="b",
        evidence="e",
        nodes=nodes,
        birth_commit="a" * 40,
        birth_at=birth_at,
    )


class TestNodeLookup:
    """``count_by_node`` / ``list_by_node`` — the scope question, indexed."""

    def test_an_episode_is_found_by_the_file_it_names(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/file.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg/mod/file.py"]) == {"pkg/mod/file.py": 1}

    def test_a_directory_target_finds_the_episodes_beneath_it(self, tmp_path: Path) -> None:
        """A module target is a real target, and the files under it are its episodes."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/mod/a.py",)), _bound("s2", ("pkg/mod/b.py",))],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["pkg/mod"]) == {"pkg/mod": 2}

    def test_a_file_target_finds_the_episode_bound_to_its_directory(self, tmp_path: Path) -> None:
        """The other direction: a claim about a directory is a claim about its files."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg/mod/file.py"]) == {"pkg/mod/file.py": 1}

    def test_a_sibling_prefix_is_not_a_match(self, tmp_path: Path) -> None:
        """``pkg/mod2`` starts with ``pkg/mod`` as a string and is a different directory."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod2/file.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg/mod"]) == {}

    def test_an_episode_naming_a_path_twice_counts_once(self, tmp_path: Path) -> None:
        """Self and ancestor both match; the caller asked how many episodes, not nodes."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/mod", "pkg/mod/file.py"))],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["pkg/mod/file.py"]) == {"pkg/mod/file.py": 1}

    def test_a_path_with_no_episodes_is_absent_rather_than_zero(self, tmp_path: Path) -> None:
        """So a caller can omit the field instead of serving a zero."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/a.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["other/file.py"]) == {}

    def test_windows_separators_are_normalised(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/file.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg\\mod\\file.py"]) == {"pkg\\mod\\file.py": 1}

    def test_the_tier_allowlist_is_honoured(self, tmp_path: Path) -> None:
        """A transcript episode is per-machine and must not reach a shareable reader."""
        with _store(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[
                    Episode(
                        tier=TIER_TRANSCRIPT,
                        kind="session",
                        subject="s",
                        body="b",
                        evidence="e",
                        nodes=("pkg/mod/file.py",),
                    )
                ],
                present_subjects=["s"],
            )
            assert store.count_by_node(["pkg/mod/file.py"]) == {"pkg/mod/file.py": 1}
            assert store.count_by_node(["pkg/mod/file.py"], tiers=SHAREABLE_TIERS) == {}

    def test_an_empty_allowlist_selects_nothing(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/a.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg/mod/a.py"], tiers=[]) == {}
            assert store.list_by_node(["pkg/mod/a.py"], tiers=[]) == []

    def test_no_paths_asks_nothing(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            assert store.count_by_node([]) == {}
            assert store.list_by_node([]) == []

    def test_list_by_node_returns_newest_births_first(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    _bound("old", ("pkg/a.py",), birth_at=1000.0),
                    _bound("new", ("pkg/a.py",), birth_at=2000.0),
                ],
                oldest_birth_at=None,
            )
            rows = store.list_by_node(["pkg/a.py"])
            assert [r["subject"] for r in rows] == ["new", "old"]

    def test_list_by_node_honours_its_limit(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound(f"s{i}", ("pkg/a.py",), birth_at=1000.0 + i) for i in range(5)],
                oldest_birth_at=None,
            )
            assert len(store.list_by_node(["pkg/a.py"], limit=2)) == 2

    def test_an_episode_matching_two_targets_is_listed_once(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/a.py", "pkg/b.py"))],
                oldest_birth_at=None,
            )
            assert len(store.list_by_node(["pkg/a.py", "pkg/b.py"])) == 1


class TestNodeIndexMaintenance:
    """The index is derived data and must never disagree with ``episodes.nodes``."""

    def test_a_re_derived_episode_loses_the_nodes_it_stopped_naming(self, tmp_path: Path) -> None:
        """A scope that can only grow is how a claim ends up matching a file it left."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/a.py", "pkg/b.py"))],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["pkg/b.py"]) == {"pkg/b.py": 1}
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/a.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["pkg/a.py"]) == {"pkg/a.py": 1}
            assert store.count_by_node(["pkg/b.py"]) == {}

    def test_a_deleted_episode_takes_its_nodes_with_it(self, tmp_path: Path) -> None:
        """Every delete path goes through the trigger, including the kind sweep."""
        with _store(tmp_path) as store:
            store.replace_kinds(
                tier=TIER_STRUCTURAL,
                kinds=["nested_repos"],
                episodes=[_episode()],
                now=1000.0,
            )
            assert store.count_by_node(["backend"]) == {"backend": 1}
            store.replace_kinds(
                tier=TIER_STRUCTURAL, kinds=["nested_repos"], episodes=[], now=2000.0
            )
            assert store.count_by_node(["backend"]) == {}
            (orphans,) = store._conn.execute("SELECT COUNT(*) FROM episode_nodes").fetchone()
            assert orphans == 0

    def test_the_ttl_sweep_leaves_no_orphan_nodes(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/a.py",))],
                oldest_birth_at=None,
                now=1000.0,
            )
            store.prune(now=1000.0 + store.ttl_days * 86400 * 2)
            (orphans,) = store._conn.execute("SELECT COUNT(*) FROM episode_nodes").fetchone()
            assert orphans == 0

    def test_a_store_written_before_the_index_backfills_on_open(self, tmp_path: Path) -> None:
        """Existing stores need no migration: the rows are the system of record."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/a.py",))], oldest_birth_at=None
            )
            db_path = store.db_path
            # Drop the index the way a store written before it would not have had it.
            store._conn.executescript("DROP TRIGGER episodes_nodes_ad; DROP TABLE episode_nodes;")
            store._conn.commit()

        with EpisodeStore(db_path) as reopened:
            assert reopened.node_index_enabled
            assert reopened.count_by_node(["pkg/a.py"]) == {"pkg/a.py": 1}

    def test_a_generator_of_episodes_still_gets_its_nodes_indexed(self, tmp_path: Path) -> None:
        """``_upsert`` makes two passes; a lazy caller must not silently lose scope."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=(e for e in [_bound("s1", ("pkg/a.py",))]),
                oldest_birth_at=None,
            )
            assert store.count_by_node(["pkg/a.py"]) == {"pkg/a.py": 1}


class TestNodeLookupStaysIndexed:
    """The plan, not the clock: a busy machine cannot make this pass or fail."""

    def test_the_lookup_uses_the_path_index_in_both_directions(self, tmp_path: Path) -> None:
        """The join form measured 14x slower because SQLite drove it from ``episodes``.

        It was not wrong, only slow, so nothing but the plan would have caught
        it. Both the self/ancestor equality and the descendant ``GLOB`` must
        resolve through ``idx_episode_nodes_path``.
        """
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/a.py",))], oldest_birth_at=None
            )
            sub, params = store._node_subquery("pkg/mod/a.py")
            plan = "\n".join(
                str(row[-1])
                for row in store._conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT COUNT(*) FROM episodes e WHERE e.id IN ({sub})",
                    params,
                )
            )
            assert "idx_episode_nodes_path" in plan
            assert "SCAN episode_nodes" not in plan
            # The GLOB half is rewritten into a range scan over the same index.
            assert "path>? AND path<?" in plan


class TestPatternCharactersInPaths:
    """Paths are data, not patterns, and a framework's routes prove it."""

    def test_a_bracketed_directory_matches_its_own_children(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    _bound("s1", ("src/app/[repo]/page.tsx",)),
                    _bound("s2", ("src/app/[repo]/layout.tsx",)),
                ],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["src/app/[repo]"]) == {"src/app/[repo]": 2}
            assert len(store.list_by_node(["src/app/[repo]"])) == 2

    def test_a_bracket_is_not_a_character_class(self, tmp_path: Path) -> None:
        """``[repo]`` read as a pattern matches ``r``, ``e``, ``p`` and ``o``."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    _bound("s1", ("src/app/r/unrelated.tsx",)),
                    _bound("s2", ("src/app/e/other.tsx",)),
                ],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["src/app/[repo]"]) == {}

    def test_star_and_question_marks_are_literal(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("docs/aXb/deep.md",))],
                oldest_birth_at=None,
            )
            assert store.count_by_node(["docs/a?b"]) == {}
            assert store.count_by_node(["docs/a*b"]) == {}


class TestPathNormalisation:
    """One location written four ways is one location."""

    @pytest.mark.parametrize(
        "target",
        ["pkg/mod/a.py", "pkg\\mod\\a.py", "pkg//mod/a.py", "pkg/mod/../mod/a.py",
         "./pkg/mod/a.py", "/pkg/mod/a.py"],
    )
    def test_equivalent_spellings_all_match(self, tmp_path: Path, target: str) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/mod/a.py",))], oldest_birth_at=None
            )
            assert store.count_by_node([target]) == {target: 1}

    def test_a_path_climbing_out_of_the_repo_asks_nothing(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/a.py",))], oldest_birth_at=None
            )
            assert store.count_by_node(["../escape"]) == {}
            assert store.count_by_node([""]) == {}


class TestNodeIndexRecovery:
    """The index is derived data; a failed build must not become permanent."""

    def test_an_interrupted_build_is_rebuilt_on_the_next_open(self, tmp_path: Path) -> None:
        """DDL is autocommit, so the table outlives a backfill that fails.

        Presence alone would then read as "complete" forever, and the store
        would answer "nothing is bound here" in the shape of the truth. The
        trigger is created last, so its presence is what vouches for the rest.
        """
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT, episodes=[_bound("s1", ("pkg/a.py",))], oldest_birth_at=None
            )
            db_path = store.db_path
            # Exactly the state an interrupted build leaves behind.
            store._conn.executescript(
                "DROP TRIGGER episodes_nodes_ad; DELETE FROM episode_nodes;"
            )
            store._conn.commit()

        with EpisodeStore(db_path) as reopened:
            assert reopened.count_by_node(["pkg/a.py"]) == {"pkg/a.py": 1}

    def test_a_store_that_cannot_build_the_index_still_answers(self, tmp_path: Path) -> None:
        """A wrong "no history" is worse than a slow right one."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[_bound("s1", ("pkg/a.py",)), _bound("s2", ("pkg/sub/b.py",))],
                oldest_birth_at=None,
            )
            store._conn.executescript(
                "DROP TRIGGER episodes_nodes_ad; DROP TABLE episode_nodes;"
            )
            store._conn.commit()
            store.node_index_enabled = False

            assert store.count_by_node(["pkg/a.py"]) == {"pkg/a.py": 1}
            assert store.count_by_node(["pkg"]) == {"pkg": 2}
            assert store.count_by_node(["nope"]) == {}
            assert store.count_by_node(["pkg"], tiers=SHAREABLE_TIERS) == {"pkg": 2}
            assert len(store.list_by_node(["pkg"])) == 2

    @pytest.mark.parametrize(
        "target",
        ["pkg/a.py", "pkg", "pkg/sub", "pkg/sub/b.py", "other", "src/app/[repo]",
         "pkg\\a.py", "pkg/x/../a.py"],
    )
    def test_the_fallback_answers_what_the_index_answers(
        self, tmp_path: Path, target: str
    ) -> None:
        """Two implementations of one question is one too many unless they agree."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    _bound("s1", ("pkg/a.py",)),
                    _bound("s2", ("pkg/sub/b.py",)),
                    _bound("s3", ("src/app/[repo]/page.tsx",)),
                    _bound("s4", ("pkg",)),
                ],
                oldest_birth_at=None,
            )
            indexed = store.count_by_node([target]).get(target, 0)
            scanned = len(store._scan_by_node([target], None).get(target, []))
            assert indexed == scanned

    def test_a_non_string_node_is_filtered_the_same_way_by_both_writers(
        self, tmp_path: Path
    ) -> None:
        """Or a rebuild silently answers differently from the live path."""
        with _store(tmp_path) as store:
            store.append_tier(
                tier=TIER_GIT,
                episodes=[
                    Episode(
                        tier=TIER_GIT,
                        kind="code_fix",
                        subject="s1",
                        body="b",
                        evidence="e",
                        nodes=(123, "pkg/a.py"),  # type: ignore[arg-type]
                        birth_commit="a" * 40,
                        birth_at=1000.0,
                    )
                ],
                oldest_birth_at=None,
            )
            live = store.count_by_node(["pkg/a.py"])
            store._backfill_node_index()
            store._conn.commit()
            assert store.count_by_node(["pkg/a.py"]) == live
            (rows,) = store._conn.execute(
                "SELECT COUNT(*) FROM episode_nodes WHERE path = '123'"
            ).fetchone()
            assert rows == 0


def _tiered(tier: str, kind: str, subject: str, birth_at: float) -> Episode:
    """An episode with an explicit birth, so ordering is deterministic."""
    return Episode(
        tier=tier,
        kind=kind,
        subject=subject,
        body=f"body of {subject}",
        evidence="e",
        nodes=("pkg/a.py",),
        birth_commit="a" * 40,
        birth_at=birth_at,
    )


def _seeded(tmp_path: Path) -> EpisodeStore:
    """Five episodes across two tiers and three kinds, newest last."""
    store = _store(tmp_path)
    store.append_tier(
        tier=TIER_GIT,
        episodes=[_tiered(TIER_GIT, "code_fix", f"sha{i}", 1000.0 + i) for i in range(3)],
        oldest_birth_at=1000.0,
    )
    store.replace_kinds(
        tier=TIER_STRUCTURAL,
        kinds=["nested_repos", "formatter_drift"],
        episodes=[
            _tiered(TIER_STRUCTURAL, "nested_repos", ".", 900.0),
            _tiered(TIER_STRUCTURAL, "formatter_drift", "ruff", 901.0),
        ],
    )
    return store


class TestPaging:
    def test_limit_and_offset_walk_the_ordering(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            everything = store.list_episodes(tiers=SHAREABLE_TIERS)
            first = store.list_episodes(tiers=SHAREABLE_TIERS, limit=2, offset=0)
            second = store.list_episodes(tiers=SHAREABLE_TIERS, limit=2, offset=2)
        assert [r["id"] for r in first + second] == [r["id"] for r in everything[:4]]

    def test_unpaged_is_still_the_default(self, tmp_path: Path) -> None:
        """The three in-process callers pass no limit and must keep everything."""
        with _seeded(tmp_path) as store:
            assert len(store.list_episodes(tiers=SHAREABLE_TIERS)) == 5

    def test_offset_past_the_end_is_empty_not_an_error(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            assert store.list_episodes(tiers=SHAREABLE_TIERS, limit=10, offset=99) == []


class TestBodyProjection:
    def test_without_body_the_key_is_absent_not_blank(self, tmp_path: Path) -> None:
        """Absent, so a reader cannot mistake "not fetched" for "empty"."""
        with _seeded(tmp_path) as store:
            (row,) = store.list_episodes(tiers=SHAREABLE_TIERS, limit=1, with_body=False)
            (full,) = store.list_episodes(tiers=SHAREABLE_TIERS, limit=1)
        assert "body" not in row
        assert full["body"]
        assert {*row} | {"body"} == {*full}

    def test_every_other_field_survives(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            (row,) = store.list_episodes(tiers=SHAREABLE_TIERS, limit=1, with_body=False)
        assert row["nodes"] == ["pkg/a.py"]
        assert row["birth_commit"] and row["birth_at"]


class TestCountEpisodes:
    def test_counts_what_the_same_filters_select(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            for kwargs in ({}, {"tier": TIER_GIT}, {"kind": "code_fix"}):
                assert store.count_episodes(**kwargs) == len(store.list_episodes(**kwargs))

    def test_an_empty_allowlist_counts_nothing(self, tmp_path: Path) -> None:
        """Not everything — the inversion the allowlist exists to prevent."""
        with _seeded(tmp_path) as store:
            assert store.count_episodes(tiers=[]) == 0
            assert store.list_episodes(tiers=[]) == []

    def test_total_is_independent_of_the_page(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            page = store.list_episodes(tiers=SHAREABLE_TIERS, limit=2)
            assert len(page) == 2
            assert store.count_episodes(tiers=SHAREABLE_TIERS) == 5


class TestGroupCounts:
    def test_groups_by_tier_and_kind(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            assert store.group_counts("tier", tiers=SHAREABLE_TIERS) == {
                TIER_GIT: 3,
                TIER_STRUCTURAL: 2,
            }
            assert store.group_counts("kind", tiers=SHAREABLE_TIERS) == {
                "code_fix": 3,
                "formatter_drift": 1,
                "nested_repos": 1,
            }

    def test_an_unlisted_column_raises_rather_than_interpolating(
        self, tmp_path: Path
    ) -> None:
        with _seeded(tmp_path) as store:
            with pytest.raises(ValueError):
                store.group_counts("body")
            with pytest.raises(ValueError):
                store.group_counts("id) --")

    def test_the_allowlist_is_honoured(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[_tiered(TIER_TRANSCRIPT, "session", "s1", 1200.0)],
                present_subjects=["s1"],
            )
            assert TIER_TRANSCRIPT not in store.group_counts("tier", tiers=SHAREABLE_TIERS)
            assert TIER_TRANSCRIPT in store.group_counts("tier")


class TestGetEpisode:
    def test_returns_the_row_by_id(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            (want,) = store.list_episodes(tier=TIER_STRUCTURAL, kind="nested_repos")
            got = store.get_episode(want["id"], tiers=SHAREABLE_TIERS)
        assert got == want

    def test_a_tier_outside_the_allowlist_is_not_reachable_by_id(
        self, tmp_path: Path
    ) -> None:
        """A stable hash is exactly the shape somebody guesses at."""
        with _seeded(tmp_path) as store:
            store.accumulate_tier(
                tier=TIER_TRANSCRIPT,
                kind="session",
                episodes=[_tiered(TIER_TRANSCRIPT, "session", "s1", 1200.0)],
                present_subjects=["s1"],
            )
            (secret,) = store.list_episodes(tier=TIER_TRANSCRIPT)
            assert store.get_episode(secret["id"], tiers=SHAREABLE_TIERS) is None
            assert store.get_episode(secret["id"]) is not None

    def test_unknown_id_is_none(self, tmp_path: Path) -> None:
        with _seeded(tmp_path) as store:
            assert store.get_episode("nope", tiers=SHAREABLE_TIERS) is None
