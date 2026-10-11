"""Transcript-tier episodes: one agent session, kept whole, bound to what it touched.

Built on real transcript files run through the real adapter, because every
question worth asserting here is a property of the seam rather than of a mock:
what counts as a touch, what the cursor leaves for a second reader, and what a
run that read nothing is allowed to delete.
"""

from __future__ import annotations

import json
import time

import pytest

from repowise.core.precedent.store import (
    SOURCE_GONE_NOTE,
    TIER_GIT,
    TIER_TRANSCRIPT,
    Episode,
    EpisodeStore,
    default_store_path,
)
from repowise.core.precedent.transcript_episodes import (
    MAX_BODY_BYTES,
    MAX_EPISODE_NODES,
    TranscriptEpisodeRecorder,
    derive_transcript_episodes,
    record_transcript_episodes,
)
from repowise.core.sessions import INTENT_TURNS, get_adapter

_TS = "2026-08-06T10:00:00.000Z"


def _line(
    kind: str, text: str = "", *, tools=(), session="s1", cwd=None, ts=_TS, **extra
) -> str:
    """One Claude Code transcript line, in the shape the adapter parses."""
    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    for name, tool_input in tools:
        content.append({"type": "tool_use", "id": "t1", "name": name, "input": tool_input})
    rec = {
        "type": kind,
        "sessionId": session,
        "timestamp": ts,
        "message": {"role": kind, "content": content},
        **extra,
    }
    if cwd is not None:
        rec["cwd"] = str(cwd)
    return json.dumps(rec)


def _transcript(tmp_path, name: str, lines: list[str]):
    d = tmp_path / "transcripts"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _repo(tmp_path, *, opted_in: bool = True, files=("a.py",)):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text("x = 1\n", encoding="utf-8")
    if opted_in:
        (root / ".repowise").mkdir(exist_ok=True)
    return root


def _fold(root, *paths) -> TranscriptEpisodeRecorder:
    """Drive one recorder over these transcripts exactly as the miner does.

    One recorder for the whole run, because that is what presence means: the
    miner shows it every transcript ``discover()`` returned, and a recorder
    shown a subset would vouch for a subset.
    """
    adapter = get_adapter()
    rec = TranscriptEpisodeRecorder(root)
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            events = adapter.events_from_lines(
                fh, prefilter=adapter.prefilter(INTENT_TURNS), path=path
            )
            for _ in rec.observe(path, events):
                pass
    return rec


def _rows(root, **kw) -> list[dict]:
    with EpisodeStore(default_store_path(root)) as store:
        return store.list_episodes(**kw)


# ---------------------------------------------------------------------------
# What an episode binds to
# ---------------------------------------------------------------------------


def test_nodes_are_what_tool_calls_touched_not_what_prose_named(tmp_path):
    """The scope rule the whole tier turns on, asserted from both sides.

    A path argued about in a paragraph is a mention; a path passed to a tool is
    a touch. Binding to mentions is how a scope stops discriminating.
    """
    root = _repo(tmp_path, files=("touched.py", "only_discussed.py"))
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line("user", "please look at only_discussed.py, it seems related"),
            _line(
                "assistant",
                "I will edit the other one instead.",
                tools=[("Edit", {"file_path": str(root / "touched.py")})],
            ),
        ],
    )
    episodes = derive_transcript_episodes(_fold(root, path), root)

    assert len(episodes) == 1
    assert episodes[0].nodes == ("touched.py",)
    # The mention is still in the body: it is evidence, just not scope.
    assert "only_discussed.py" in episodes[0].body


def test_a_path_named_only_in_a_shell_command_is_not_a_touch(tmp_path):
    root = _repo(tmp_path, files=("real.py",))
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line(
                "assistant",
                "running the tests",
                tools=[("Bash", {"command": f"pytest {root / 'real.py'}"})],
            )
        ],
    )
    episodes = derive_transcript_episodes(_fold(root, path), root)
    assert episodes and episodes[0].nodes == ()


def test_a_path_that_is_not_a_file_here_is_dropped(tmp_path):
    """Tool inputs record what was reached for, including guesses that missed."""
    root = _repo(tmp_path, files=("real.py",))
    (root / "adir").mkdir()
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line(
                "assistant",
                "looking around",
                tools=[
                    ("Read", {"file_path": str(root / "real.py")}),
                    ("Read", {"file_path": str(root / "guessed" / "nope.py")}),
                    ("Glob", {"path": str(root / "adir")}),
                    ("Read", {"file_path": str(tmp_path / "outside.py")}),
                ],
            )
        ],
    )
    episodes = derive_transcript_episodes(_fold(root, path), root)
    assert episodes[0].nodes == ("real.py",)


def test_a_session_over_the_node_ceiling_is_kept_repo_wide(tmp_path):
    """Not skipped and not truncated: kept, with its scope withdrawn.

    A commit above the ceiling is a sweep and is dropped. A session above it is
    an ordinary long session, so dropping it loses the episode outright, while
    truncating would leave a scope narrower than the body describing it.
    """
    files = [f"f{i}.py" for i in range(MAX_EPISODE_NODES + 1)]
    root = _repo(tmp_path, files=files)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line(
                "assistant",
                "a very long session",
                tools=[("Edit", {"file_path": str(root / f)}) for f in files],
            )
        ],
    )
    episodes = derive_transcript_episodes(_fold(root, path), root)

    assert len(episodes) == 1
    assert episodes[0].nodes == ()
    assert "too many to bind a scope to" in episodes[0].evidence


def test_no_located_files_reads_differently_from_too_many(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "just a question")])
    (episode,) = derive_transcript_episodes(_fold(root, path), root)
    assert "no located files" in episode.evidence


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------


def test_the_body_is_the_conversation_and_not_the_tool_output(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line("user", "why is the build failing"),
            _line("assistant", "because the lockfile is stale"),
        ],
    )
    (episode,) = derive_transcript_episodes(_fold(root, path), root)
    assert "why is the build failing" in episode.body
    assert "because the lockfile is stale" in episode.body


def test_subagent_and_harness_lines_stay_out_of_the_body(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line("user", "the real question"),
            _line("assistant", "a subagent said this", isSidechain=True),
            _line("user", "a harness reminder", isMeta=True),
        ],
    )
    (episode,) = derive_transcript_episodes(_fold(root, path), root)
    assert "the real question" in episode.body
    assert "a subagent said this" not in episode.body
    assert "a harness reminder" not in episode.body


def test_a_command_wrapper_does_not_open_the_body(tmp_path):
    """Labelling real episodes found this opening 12 of 20 bodies.

    A slash-command invocation arrives as an ordinary user turn, so anything
    checking only ``kind`` treats harness plumbing as the first thing the user
    said. The miner's shipped rule already knows better; this asserts the
    recorder asks it rather than deciding for itself.
    """
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line("user", "<command-name>/clear</command-name>\n<command-args></command-args>"),
            _line("user", "now the actual request"),
        ],
    )
    (episode,) = derive_transcript_episodes(_fold(root, path), root)

    assert episode.body.startswith("user: now the actual request")
    assert "command-name" not in episode.body


def test_the_body_cap_is_a_bound_and_not_a_suggestion(tmp_path):
    """One oversized turn must not carry the body past the cap.

    Stopping before an over-long message instead of cutting it makes the cap
    depend on turn size, and a single assistant message here is routinely tens
    of kilobytes.
    """
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [
            _line("user", "start"),
            _line("assistant", "x" * (MAX_BODY_BYTES * 2)),
            _line("user", "end"),
        ],
    )
    (episode,) = derive_transcript_episodes(_fold(root, path), root)

    assert len(episode.body.encode("utf-8")) <= MAX_BODY_BYTES
    assert "body truncated" in episode.evidence


def test_a_session_read_across_two_runs_keeps_both_halves(tmp_path):
    """The store upserts rather than appends, so the merge has to be explicit.

    A session is live while the index runs, so this is the ordinary case rather
    than the corner one: without the merge a row ends up holding whichever
    slice of the conversation the last run happened to see.
    """
    root = _repo(tmp_path, files=("first.py", "second.py"))
    first = _transcript(
        tmp_path,
        "s.jsonl",
        [_line("assistant", "the first half", tools=[("Edit", {"file_path": str(root / "first.py")})])],
    )
    record_transcript_episodes(root, _fold(root, first))

    second = _transcript(
        tmp_path,
        "s.jsonl",
        [_line("assistant", "the second half", tools=[("Edit", {"file_path": str(root / "second.py")})])],
    )
    record_transcript_episodes(root, _fold(root, second))

    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert "the first half" in row["body"]
    assert "the second half" in row["body"]
    assert sorted(row["nodes"]) == ["first.py", "second.py"]


def test_a_re_read_transcript_is_not_written_twice(tmp_path):
    """The cursor is not monotonic: a truncated file restarts at byte 0.

    So "everything appended since last time" can be everything again, and a
    merge that only ever appends writes the conversation twice.
    """
    root = _repo(tmp_path)
    lines = [_line("user", "the whole thing"), _line("assistant", "and the reply")]
    path = _transcript(tmp_path, "s.jsonl", lines)
    record_transcript_episodes(root, _fold(root, path))
    record_transcript_episodes(root, _fold(root, path))

    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert row["body"].count("the whole thing") == 1
    assert row["body"].count("and the reply") == 1


# ---------------------------------------------------------------------------
# The tee: the stream must come out the way it went in
# ---------------------------------------------------------------------------


def test_observe_yields_every_event_unchanged(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path,
        "s.jsonl",
        [_line("user", "one"), _line("assistant", "two"), _line("user", "three")],
    )
    adapter = get_adapter()
    with path.open(encoding="utf-8") as fh:
        expected = list(
            adapter.events_from_lines(fh, prefilter=adapter.prefilter(INTENT_TURNS), path=path)
        )
    rec = TranscriptEpisodeRecorder(root)
    with path.open(encoding="utf-8") as fh:
        events = adapter.events_from_lines(
            fh, prefilter=adapter.prefilter(INTENT_TURNS), path=path
        )
        seen = list(rec.observe(path, events))

    assert [e.text for e in seen] == [e.text for e in expected]


def test_a_transcript_shown_but_never_iterated_still_counts_as_present(tmp_path):
    """Presence is registered on show, not on read.

    A session already past its cursor yields nothing, and a run that read its
    silence as absence would delete the episode written when it was new.
    """
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    rec = TranscriptEpisodeRecorder(root)
    rec.observe(path, iter(()))  # deliberately not iterated

    assert rec.present_subjects == [str(path).replace("\\", "/")]
    assert rec.pending() == []


def test_a_quiet_session_keeps_its_episode(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    record_transcript_episodes(root, _fold(root, path))
    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 1

    # Second run: the file is present but has nothing new to give.
    quiet = TranscriptEpisodeRecorder(root)
    quiet.observe(path, iter(()))
    record_transcript_episodes(root, quiet)

    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert "hello" in row["body"]


def test_a_session_whose_transcript_is_gone_keeps_its_episode(tmp_path):
    """The harness prunes transcripts on its own schedule; the episode is durable.

    Measured on the machine this was written on: 1,509 transcripts, oldest 30
    days, the age distribution stopping dead at the harness default nobody
    sets. An episode that died with its transcript could never be older than
    that, which is the one thing this tier claims to be.
    """
    root = _repo(tmp_path)
    kept = _transcript(tmp_path, "kept.jsonl", [_line("user", "still here")])
    gone = _transcript(tmp_path, "gone.jsonl", [_line("user", "not for long", session="s2")])
    # One recorder across every discovered transcript, which is the production
    # shape: presence is what the whole run saw.
    record_transcript_episodes(root, _fold(root, kept, gone))
    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 2

    gone.unlink()
    still_there = TranscriptEpisodeRecorder(root)
    still_there.observe(kept, iter(()))
    record_transcript_episodes(root, still_there)

    rows = {r["subject"]: r for r in _rows(root, tier=TIER_TRANSCRIPT)}
    assert set(rows) == {str(kept).replace("\\", "/"), str(gone).replace("\\", "/")}
    orphan = rows[str(gone).replace("\\", "/")]
    # The prose it was made of survives; only the pointer is marked dead.
    assert "not for long" in orphan["body"]
    assert orphan["evidence"].endswith(SOURCE_GONE_NOTE)


def test_a_run_that_discovered_nothing_deletes_nothing(tmp_path):
    """The rule the git tier learned: a pass that looked at nothing cannot vouch.

    A transcript directory that has gone missing (a different machine, a moved
    home directory) must read as "cannot tell", not as "every session is gone".
    """
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    record_transcript_episodes(root, _fold(root, path))

    record_transcript_episodes(root, TranscriptEpisodeRecorder(root))
    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 1


# ---------------------------------------------------------------------------
# Blast radius: the other tiers, and a repo that opted out
# ---------------------------------------------------------------------------


def test_writing_transcripts_leaves_the_other_tiers_alone(tmp_path):
    root = _repo(tmp_path)
    with EpisodeStore(default_store_path(root)) as store:
        store.append_tier(
            tier=TIER_GIT,
            episodes=[
                Episode(
                    tier=TIER_GIT,
                    kind="code_fix",
                    subject="abc123",
                    body="a fix",
                    evidence="commit abc123",
                    nodes=("a.py",),
                    birth_at=1000.0,
                )
            ],
            oldest_birth_at=1.0,
        )
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    record_transcript_episodes(root, _fold(root, path))

    assert len(_rows(root, tier=TIER_GIT)) == 1
    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 1


def test_a_repo_that_never_opted_in_gets_no_store(tmp_path):
    root = _repo(tmp_path, opted_in=False)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    assert record_transcript_episodes(root, _fold(root, path)) == 0
    assert not default_store_path(root).exists()


def test_recording_never_raises(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    monkeypatch.setattr(
        "repowise.core.precedent.transcript_episodes.EpisodeStore.open_for_repo",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert record_transcript_episodes(root, _fold(root, path)) == 0


def test_no_subprocess_is_spawned(tmp_path, monkeypatch):
    """A session is dated, not committed, so nothing here may ask git anything."""
    import subprocess

    def _forbidden(*a, **k):
        raise AssertionError("no subprocess allowed here")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    assert record_transcript_episodes(root, _fold(root, path)) == 1
    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert row["birth_commit"] is None


def test_the_episode_is_dated_from_the_session_not_the_index(tmp_path):
    root = _repo(tmp_path)
    path = _transcript(tmp_path, "s.jsonl", [_line("user", "hello")])
    record_transcript_episodes(root, _fold(root, path))
    (row,) = _rows(root, tier=TIER_TRANSCRIPT)

    assert row["birth_at"] == pytest.approx(
        time.mktime(time.strptime("2026-08-06 10:00:00", "%Y-%m-%d %H:%M:%S"))
        - time.timezone,
        abs=2,
    )
    assert row["tier"] == TIER_TRANSCRIPT


def test_a_repeated_turn_later_in_a_session_is_not_mistaken_for_a_replay(tmp_path):
    """The replay guard has to be anchored, not "appears anywhere".

    Transcripts repeat short turns verbatim all the time ("Running the
    tests.", "Done."). An unanchored containment test reads the second one as
    a re-read of the first and drops it, losing real conversation. A replay
    starts where the conversation starts; a repeat does not.
    """
    root = _repo(tmp_path)
    first = _transcript(tmp_path, "s.jsonl", [_line("assistant", "Running the tests.")])
    record_transcript_episodes(root, _fold(root, first))

    # The next run reads only what was appended, and it opens with a turn
    # byte-identical to the one the body already starts with. Only the
    # timestamp separates this from a rewind.
    again = _transcript(
        tmp_path,
        "s.jsonl",
        [_line("assistant", "Running the tests.", ts="2026-08-06T11:00:00.000Z")],
    )
    record_transcript_episodes(root, _fold(root, again))

    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert row["body"].count("Running the tests.") == 2


def test_the_truncation_label_survives_a_run_that_reads_nothing(tmp_path):
    """Truncation is a property of the stored body, not of the pass that saw it.

    Every long session eventually ends, and the run after that reads nothing.
    A flag carried from that pass would quietly retract the warning exactly
    when the body is at its longest.
    """
    root = _repo(tmp_path)
    path = _transcript(
        tmp_path, "s.jsonl", [_line("assistant", "x" * (MAX_BODY_BYTES * 2))]
    )
    record_transcript_episodes(root, _fold(root, path))
    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert "body truncated" in row["evidence"]

    quiet = TranscriptEpisodeRecorder(root)
    quiet.observe(path, iter(()))
    record_transcript_episodes(root, quiet)

    (row,) = _rows(root, tier=TIER_TRANSCRIPT)
    assert "body truncated" in row["evidence"]


def test_a_listed_but_unread_transcript_keeps_its_episode(tmp_path):
    """A sweep that stops early must not read its own unfinished work as loss.

    ``note_present`` is what separates "this session exists" from "this run
    read it": the first is answerable from the directory listing, and it is the
    one deletion is allowed to depend on.
    """
    root = _repo(tmp_path)
    read = _transcript(tmp_path, "read.jsonl", [_line("user", "was read")])
    unread = _transcript(tmp_path, "unread.jsonl", [_line("user", "never reached", session="s2")])
    record_transcript_episodes(root, _fold(root, read, unread))
    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 2

    # A later, budget-limited run: both listed, only one read.
    partial = TranscriptEpisodeRecorder(root)
    partial.note_present([read, unread])
    for _ in partial.observe(read, iter(())):
        pass
    record_transcript_episodes(root, partial)

    assert len(_rows(root, tier=TIER_TRANSCRIPT)) == 2
