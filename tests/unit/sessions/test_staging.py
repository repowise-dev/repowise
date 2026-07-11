"""Staging-store tests: raw queue, observation counting, promotion, cursors."""

from __future__ import annotations

import json

import pytest

from repowise.core.sessions import ClaudeCodeAdapter
from repowise.core.sessions.cursor import iter_new_events
from repowise.core.sessions.staging import SessionStagingStore, title_key


@pytest.fixture
def store(tmp_path):
    s = SessionStagingStore(tmp_path / "sessions.db")
    yield s
    s.close()


def _raw(store, *, hash_="raw1", kind="explicit_choice", session_id="s1"):
    return store.add_raw(
        hash_=hash_,
        kind=kind,
        quotes=["we chose sqlite because it is local"],
        files=["a.py"],
        session_id=session_id,
        now=100.0,
    )


STRUCTURED = {
    "title": "Use sqlite for staging",
    "decision": "we chose sqlite",
    "rationale": "because it is local",
    "source_quote": "we chose sqlite because it is local",
    "verification": "exact",
    "affected_files": ["a.py"],
}


def test_add_raw_is_idempotent(store):
    assert _raw(store) is True
    assert _raw(store) is False
    assert len(store.pending_raws(10)) == 1


def test_mark_raw_rejected_removes_from_pending(store):
    _raw(store)
    store.mark_raw_rejected("raw1")
    assert store.pending_raws(10) == []


def test_upsert_structured_merges_sessions_and_kind(store):
    _raw(store, hash_="r1", kind="explicit_choice", session_id="s1")
    _raw(store, hash_="r2", kind="user_correction", session_id="s2")
    key = store.upsert_structured(
        "r1",
        kind="explicit_choice",
        title=STRUCTURED["title"],
        structured=STRUCTURED,
        quotes=["q1"],
        files=["a.py"],
        session_id="s1",
        now=100.0,
    )
    same_key = store.upsert_structured(
        "r2",
        kind="user_correction",
        title="use SQLITE for staging!",  # same normalized title
        structured=STRUCTURED,
        quotes=["q2"],
        files=["b.py"],
        session_id="s2",
        now=101.0,
    )
    assert same_key == key == title_key(STRUCTURED["title"])
    assert store.pending_raws(10) == []

    (row,) = store.promotable()
    assert sorted(row["sessions"]) == ["s1", "s2"]
    assert row["kind"] == "user_correction"  # correction kind is sticky
    assert sorted(row["files"]) == ["a.py", "b.py"]


def test_single_choice_observation_does_not_promote(store):
    _raw(store, hash_="r1")
    store.upsert_structured(
        "r1",
        kind="explicit_choice",
        title=STRUCTURED["title"],
        structured=STRUCTURED,
        quotes=["q"],
        files=[],
        session_id="s1",
    )
    assert store.promotable() == []


def test_single_correction_observation_promotes(store):
    _raw(store, hash_="r1", kind="user_correction")
    store.upsert_structured(
        "r1",
        kind="user_correction",
        title=STRUCTURED["title"],
        structured=STRUCTURED,
        quotes=["q"],
        files=[],
        session_id="s1",
    )
    (row,) = store.promotable()
    assert row["first_promotion"] is True
    assert row["observations"] == 1


def test_emit_bookkeeping_requires_new_observations(store):
    _raw(store, hash_="r1", kind="user_correction", session_id="s1")
    store.upsert_structured(
        "r1",
        kind="user_correction",
        title=STRUCTURED["title"],
        structured=STRUCTURED,
        quotes=["q"],
        files=[],
        session_id="s1",
    )
    (row,) = store.promotable()
    store.mark_emitted(row["key"], observations=row["observations"])
    assert store.promotable() == []  # nothing new to say

    # A new session observing the same decision re-qualifies it, but as a
    # re-emission (first_promotion False -> upserted as proposed evidence).
    _raw(store, hash_="r2", kind="explicit_choice", session_id="s2")
    store.upsert_structured(
        "r2",
        kind="explicit_choice",
        title=STRUCTURED["title"],
        structured=STRUCTURED,
        quotes=["q2"],
        files=[],
        session_id="s2",
    )
    (row,) = store.promotable()
    assert row["first_promotion"] is False
    assert row["observations"] == 2


def test_prune_drops_stale_unstructured_raws(store):
    _raw(store, hash_="old")
    store.prune(now=100.0 + 91 * 86400.0)
    assert store.pending_raws(10) == []


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "sessions.db"
    with SessionStagingStore(path) as s:
        _raw(s, hash_="r1", kind="user_correction")
        s.upsert_structured(
            "r1",
            kind="user_correction",
            title=STRUCTURED["title"],
            structured=STRUCTURED,
            quotes=["q"],
            files=[],
            session_id="s1",
        )
        s.commit()
    with SessionStagingStore(path) as s:
        (row,) = s.promotable()
        assert row["title"] == STRUCTURED["title"]


def test_db_cursors_drive_iter_new_events(tmp_path):
    """The DB-backed cursor store satisfies the iter_new_events contract."""
    adapter = ClaudeCodeAdapter()
    transcript = tmp_path / "session.jsonl"
    line = json.dumps(
        {"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "hello"}}
    )
    transcript.write_text(line + "\n", encoding="utf-8")

    path = tmp_path / "sessions.db"
    with SessionStagingStore(path) as store:
        events = list(iter_new_events(adapter, transcript, store.cursors))
        assert [e.text for e in events] == ["hello"]
        store.cursors.save()

    with transcript.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line.replace("hello", "again") + "\n")

    with SessionStagingStore(path) as store:
        events = list(iter_new_events(adapter, transcript, store.cursors))
        assert [e.text for e in events] == ["again"]
