"""Hook efficacy: emission parsing, per-surface classification, ledger ingest.

The numbers this module produces are the input to every later decision about
which hook surfaces to keep, so the tests here are mostly about the ways a
classifier can lie: counting one firing twice, crediting an action it cannot
attribute, or reporting 0% for a notice whose success looks like silence.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.sessions.efficacy import (
    ACTION_WINDOW,
    AMBIGUOUS,
    classify,
    ingest_transcript_efficacy,
    iter_transcript_firings,
    ledger_key,
    parse_emission,
)
from repowise.core.sessions.staging import SessionStagingStore

NUDGE = (
    "[repowise] A skeleton of pkg/core/thing.py is ~200 tokens vs ~4000 for the "
    'full file. For structure-level questions use get_context(["pkg/core/thing.py"], '
    'include=["skeleton"]).'
)
TRIAGE = (
    "[repowise] 40+ matches for `parse_yaml`. Top files by graph centrality:\n"
    "  pkg/core/loader.py\n"
    "  pkg/core/schema.py"
)
RESCUE = (
    "[repowise] No literal match for `parseYaml`. Closest indexed symbol: "
    "function `parse_yaml` in pkg/core/loader.py:12"
)
WRONG_PATH = (
    "[repowise] pkg/core/fix_events.py is not in this tree. "
    "The only indexed fix_events.py is pkg/core/ingestion/fix_events.py"
)
TRIAGE_V2 = (
    "[repowise] 40+ matches for `parse_yaml` across 12 files. Most likely relevant, "
    "ranked over the files your search matched:\n"
    "  pkg/core/loader.py  (9 matches)\n"
    "  pkg/core/schema.py  (2 matches)"
)
RESCUE_WIDE = (
    "[repowise] `parse_yaml` matched 3 files, but not pkg/core/loader.py:12, "
    "where indexed function `parseYaml` is defined."
)
FIXES = "[repowise] pkg/core/loader.py has been bug-fixed 9x in the last 6 months, last 3 days ago."
REREAD = (
    "[repowise] You already read pkg/core/thing.py this session and it is unchanged — "
    "its content is still in context."
)


def _use(name, **inp):
    return (name, json.dumps(inp))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_each_surface_with_its_target():
    for text, surface, category, target in (
        (NUDGE, "read", "skeleton_nudge", "pkg/core/thing.py"),
        (TRIAGE, "search", "triage", "pkg/core/loader.py"),
        (RESCUE, "search", "rescue", "pkg/core/loader.py"),
        (TRIAGE_V2, "search", "triage", "pkg/core/loader.py"),
        (RESCUE_WIDE, "search", "rescue_wide", "pkg/core/loader.py"),
        (FIXES, "fix_history", "edit_notice", "pkg/core/loader.py"),
        (REREAD, "read", "reread", "pkg/core/thing.py"),
    ):
        (firing,) = parse_emission(text)
        assert (firing.surface, firing.category) == (surface, category)
        assert firing.targets[0] == target


def test_one_emission_can_carry_several_firings():
    """A file that is both governed and much-fixed emits two notices at once."""
    firings = parse_emission(f"{FIXES}\n{NUDGE}")
    assert [(f.surface, f.category) for f in firings] == [
        ("fix_history", "edit_notice"),
        ("read", "skeleton_nudge"),
    ]


def test_triage_keeps_its_ranked_file_list_in_order():
    (firing,) = parse_emission(TRIAGE)
    assert firing.targets == ["pkg/core/loader.py", "pkg/core/schema.py"]
    assert firing.pattern == "parse_yaml"


def test_the_retired_triage_header_still_parses():
    """`hook backfill` replays transcripts written before the item-9 rewrite."""
    (firing,) = parse_emission(TRIAGE)
    assert (firing.surface, firing.category) == ("search", "triage")


def test_new_triage_header_keeps_its_ranked_list_and_match_counts():
    (firing,) = parse_emission(TRIAGE_V2)
    assert firing.targets == ["pkg/core/loader.py", "pkg/core/schema.py"]
    assert firing.pattern == "parse_yaml"


def test_widened_rescue_is_scored_apart_from_the_zero_result_one():
    """Same emitter, different population; pooling would move rescue's 44%."""
    (firing,) = parse_emission(RESCUE_WIDE)
    assert firing.category == "rescue_wide"
    assert firing.targets == ["pkg/core/loader.py", "parseYaml"]
    classify(firing, [_use("Read", file_path="pkg/core/loader.py")])
    assert firing.acted is True


def test_digest_paths_are_normalized_from_windows_spelling():
    """The digest groups whatever spelling grep emitted, backslashes included."""
    text = (
        "[repowise] Search flood — compact digest (files ordered by match count):\n"
        "  pkg\\core\\loader.py  (10 matches)\n"
        "  pkg\\core\\schema.py  (3 matches)"
    )
    (firing,) = parse_emission(text)
    assert firing.targets == ["pkg/core/loader.py", "pkg/core/schema.py"]


def test_unrelated_repowise_text_is_not_a_firing():
    assert parse_emission("[repowise] Index is behind HEAD: indexed abc, now def.") == []


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_skeleton_nudge_acted_only_on_a_structure_call():
    (firing,) = parse_emission(NUDGE)
    classify(firing, [_use("mcp__repowise__get_context", targets=["pkg/core/thing.py"],
                           include=["skeleton"])])
    assert firing.acted is True
    assert firing.evidence == "skeleton_call"
    assert firing.distance == 0


def test_ranged_read_after_a_nudge_is_ambiguous_not_acted():
    """Reading a range of a file you just read in full is ordinary edit prep.

    Crediting it is what turns the nudge's real 0.2% into a flattering 19.7%.
    The suspicion that fell out of that — that 0.2% measured the judge rather
    than the surface — was tested and did not hold: three looser readings all
    came back at or below the base rate, and the nudge has been retired on the
    emission side. Every skeleton_nudge case in this file now guards a
    historical population, not a live one.
    """
    (firing,) = parse_emission(NUDGE)
    classify(firing, [_use("Read", file_path="pkg/core/thing.py", offset=40, limit=20)])
    assert firing.acted is False
    assert firing.evidence == AMBIGUOUS


def test_a_real_action_after_an_ambiguous_one_still_counts():
    (firing,) = parse_emission(NUDGE)
    classify(
        firing,
        [
            _use("Read", file_path="pkg/core/thing.py", offset=40, limit=20),
            _use("mcp__repowise__get_symbol", id="pkg/core/thing.py::Widget"),
        ],
    )
    assert firing.acted is True
    assert firing.distance == 1


def test_triage_records_which_rank_the_agent_took():
    (firing,) = parse_emission(TRIAGE)
    classify(firing, [_use("Read", file_path="/repo/pkg/core/schema.py")])
    assert (firing.acted, firing.evidence) == (True, "touched_rank1")


def test_rescue_counts_the_offered_symbol_as_well_as_the_file():
    (firing,) = parse_emission(RESCUE)
    classify(firing, [_use("Grep", pattern="parse_yaml")])
    assert firing.acted is True


def test_a_wrong_path_rescue_is_acted_on_by_going_where_it_pointed():
    (firing,) = parse_emission(WRONG_PATH)
    assert (firing.surface, firing.category) == ("wrong_path", "rescue")
    classify(firing, [_use("Read", file_path="/repo/pkg/core/ingestion/fix_events.py")])
    assert (firing.acted, firing.evidence) == (True, "touched_rank0")


def test_retrying_the_path_that_failed_is_not_acting_on_the_rescue():
    """The attempted path shares the line with the answer and must not count.

    ``parse_emission`` harvests loose path tokens from continuation lines, so
    a rescue that ever grows a second line would silently start crediting the
    agent for repeating the mistake. One line, one captured target.
    """
    (firing,) = parse_emission(WRONG_PATH)
    assert firing.targets == ["pkg/core/ingestion/fix_events.py"]
    classify(firing, [_use("Read", file_path="/repo/pkg/core/fix_events.py")])
    assert firing.acted is False


def test_a_retry_that_contains_the_answer_is_still_not_acting():
    """Half this surface's corpus is an extra directory in front of a real
    file, so the attempted path *contains* the resolved one. A substring judge
    scores a verbatim retry as compliance; this is the guard against that."""
    (firing,) = parse_emission(
        "[repowise] packages/docs/guide.md is not in this tree. "
        "The only indexed guide.md is docs/guide.md"
    )
    assert firing.targets == ["docs/guide.md"]
    classify(firing, [_use("Read", file_path="/repo/packages/docs/guide.md")])
    assert firing.acted is False
    # Going where it actually pointed still counts.
    (firing,) = parse_emission(
        "[repowise] packages/docs/guide.md is not in this tree. "
        "The only indexed guide.md is docs/guide.md"
    )
    classify(firing, [_use("Read", file_path="/repo/docs/guide.md")])
    assert firing.acted is True


def test_a_resolved_path_containing_a_space_is_not_truncated():
    """An indexed path can contain a space; ``\\S+`` would keep "packages/My"
    and substring-match nearly every later tool call in that repo."""
    (firing,) = parse_emission(
        "[repowise] src/config.py is not in this tree. "
        "The only indexed config.py is packages/My App/config.py"
    )
    assert firing.targets == ["packages/My App/config.py"]


def test_normalize_keeps_a_leading_dot_directory():
    """``lstrip("./")`` strips a character set and ate the dot."""
    (firing,) = parse_emission(
        "[repowise] ci.yml is not in this tree. "
        "The only indexed ci.yml is .github/workflows/ci.yml"
    )
    assert firing.targets == [".github/workflows/ci.yml"]


def test_fix_history_acted_on_a_test_run_or_a_history_look():
    for call, evidence in (
        (_use("Bash", command="pytest tests/unit/core"), "ran_test"),
        (_use("Bash", command="git log -p pkg/core/loader.py"), "read_history"),
    ):
        (firing,) = parse_emission(FIXES)
        classify(firing, [call])
        assert (firing.acted, firing.evidence) == (True, evidence)


def test_fix_history_ignores_unrelated_shell_work():
    (firing,) = parse_emission(FIXES)
    classify(firing, [_use("Bash", command="ls -la")])
    assert firing.acted is False


def test_reread_is_respected_unless_the_file_is_read_in_full_again():
    """Its success looks like silence, so adoption scoring would report 0%."""
    (firing,) = parse_emission(REREAD)
    classify(firing, [_use("Grep", pattern="anything")])
    assert (firing.acted, firing.evidence) == (True, "respected")

    (offender,) = parse_emission(REREAD)
    classify(offender, [_use("Read", file_path="pkg/core/thing.py")])
    assert (offender.acted, offender.evidence) == (False, "reread_again")

    (ranged,) = parse_emission(REREAD)
    classify(ranged, [_use("Read", file_path="pkg/core/thing.py", offset=5, limit=10)])
    assert ranged.acted is True


def test_stale_read_has_no_action_to_take():
    text = "[repowise] a/b.py changed (Edit/Write) after your previous read of it — stale."
    (firing,) = parse_emission(text)
    classify(firing, [_use("Read", file_path="a/b.py")])
    assert firing.acted is None


def test_action_beyond_the_window_is_not_credited():
    (firing,) = parse_emission(TRIAGE)
    following = [_use("Bash", command="ls")] * ACTION_WINDOW
    classify(firing, [*following, _use("Read", file_path="pkg/core/loader.py")])
    assert firing.acted is False


# ---------------------------------------------------------------------------
# Transcript replay and ledger ingest
# ---------------------------------------------------------------------------


def _transcript(path, emissions_then_uses):
    """Write a minimal Claude Code transcript: hook attachments + tool_use lines."""
    lines = []
    for kind, value in emissions_then_uses:
        if kind == "hook":
            lines.append(
                json.dumps(
                    {
                        "type": "attachment",
                        "sessionId": "sess-1",
                        "timestamp": "2026-08-03T10:00:00.000Z",
                        "attachment": {
                            "type": "hook_success",
                            "hookName": "PostToolUse:Read",
                            "durationMs": 1200,
                            "stdout": json.dumps(
                                {
                                    "hookSpecificOutput": {
                                        "hookEventName": "PostToolUse",
                                        "additionalContext": value,
                                    }
                                }
                            ),
                        },
                    }
                )
            )
        elif kind == "echo":
            # The paired record the harness also writes for the same firing.
            lines.append(
                json.dumps(
                    {"type": "attachment", "attachment": {
                        "type": "hook_additional_context", "content": [value]}}
                )
            )
        else:
            name, inp = value
            lines.append(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "tool_use", "id": "t1", "name": name,
                                 "input": json.loads(inp)}
                            ]
                        },
                    }
                )
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_replay_counts_each_firing_once_despite_the_paired_record(tmp_path):
    """The harness logs one emission twice; counting both doubles every figure."""
    t = tmp_path / "sess-1.jsonl"
    _transcript(t, [("hook", TRIAGE), ("echo", TRIAGE),
                    ("use", _use("Read", file_path="pkg/core/loader.py"))])

    firings = list(iter_transcript_firings(t))
    assert len(firings) == 1
    assert firings[0].acted is True
    assert firings[0].duration_ms == 1200


def test_replay_ignores_the_agent_quoting_the_hook_text(tmp_path):
    """Reading the hook's own source must not register as a firing."""
    t = tmp_path / "sess-1.jsonl"
    _transcript(t, [("use", _use("Write", file_path="x.py", content=NUDGE))])
    assert list(iter_transcript_firings(t)) == []


def test_ingest_is_idempotent_and_settles_the_live_row(tmp_path):
    """Replaying twice must not double-count, and must upgrade the hook's row."""
    repo = tmp_path / "repo"
    (repo / ".repowise" / "sessions").mkdir(parents=True)
    projects = tmp_path / "projects"
    from repowise.core.sessions.adapters.claude_code import transcript_dir_for

    d = transcript_dir_for(repo.resolve(), projects)
    d.mkdir(parents=True)
    _transcript(d / "sess-1.jsonl", [("hook", TRIAGE),
                                     ("use", _use("Read", file_path="pkg/core/loader.py"))])

    # The live hook wrote its own row first, keyed on the same text.
    store = SessionStagingStore.open_default(repo)
    store.record_firing(session_id="sess-1", key=ledger_key("search", "triage", TRIAGE),
                        surface="search", category="triage", chars=len(TRIAGE),
                        shown_at=1.0, duration_ms=50, acted=None)
    store.commit()
    store.close()

    for _ in range(2):
        ingest_transcript_efficacy(repo, projects_root=projects)

    store = SessionStagingStore.open_default(repo)
    try:
        rows = [r for r in store.efficacy_rows() if r["surface"] == "search"]
    finally:
        store.close()
    assert len(rows) == 1
    assert rows[0]["firings"] == 1, "replay inserted a second row for one firing"
    assert rows[0]["acted"] == 1
    assert rows[0]["duration_ms_total"] == 1200, "harness timing should win over the hook's"


@pytest.mark.parametrize("surface,category,text", [
    ("read", "skeleton_nudge", NUDGE),
    ("search", "triage", TRIAGE),
    ("fix_history", "edit_notice", FIXES),
])
def test_ledger_key_mirrors_the_hook_side_helper(surface, category, text):
    """Drift here silently doubles every count — the hook cannot import core."""
    from repowise.cli.commands.augment_cmd._shared import _ledger_key

    assert ledger_key(surface, category, text) == _ledger_key(surface, category, text)
