"""The transcript savings surface: what it counts, and what it refuses to.

Transcript lines follow the Claude Code JSONL shape, matching
``tests/unit/distill/test_missed_savings.py``: an assistant entry carries
``message.content[]`` ``tool_use`` blocks with a top-level ``cwd`` and
``timestamp``; the paired user entry carries a ``tool_result`` block and a
top-level ``toolUseResult`` with ``stdout``/``stderr``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.budget import estimate_tokens
from repowise.core.distill.markers import render_marker
from repowise.core.savings import recorder
from repowise.core.savings.transcript import (
    ESTIMATOR,
    HOST_OUTPUT_CAP_TOKENS,
    _Candidate,
    sync_transcript_savings,
)
from repowise.core.sessions import transcript_dir_for

NOW = time.time()

#: Long enough that the omitted count dominates, short enough to stay well
#: under the host cap so a test about the cap is the only one that hits it.
KEPT = "ok\n" * 40


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pair(
    output: str,
    *,
    cwd: str,
    tool: str = "Bash",
    block_id: str = "toolu_01",
    ts: float = NOW,
    model: str | None = None,
) -> list[dict]:
    return [
        {
            "type": "assistant",
            "cwd": cwd,
            "timestamp": _iso(ts),
            "message": {
                "role": "assistant",
                **({"model": model} if model else {}),
                "content": [
                    {
                        "type": "tool_use",
                        "id": block_id,
                        "name": tool,
                        "input": {"command": "pytest -q"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "cwd": cwd,
            "timestamp": _iso(ts + 1),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": block_id, "content": "..."}],
            },
            "toolUseResult": {"stdout": output, "stderr": ""},
        },
    ]


def _distilled(ref: str, tokens_omitted: int, kept: str = KEPT) -> str:
    return kept + "\n" + render_marker(ref, 100, tokens_omitted) + "\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "myrepo"
    (root / ".repowise").mkdir(parents=True)
    return root


@pytest.fixture()
def projects(tmp_path: Path, repo: Path) -> Path:
    root = tmp_path / "projects"
    transcript_dir_for(repo, root).mkdir(parents=True)
    return root


def _write(projects: Path, repo: Path, entries: list[dict], name: str = "s1") -> Path:
    path = transcript_dir_for(repo, projects) / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def _sync(repo: Path, projects: Path, **kwargs):
    return sync_transcript_savings(
        repo, harnesses=("claude_code",), projects_root=projects, **kwargs
    )


def _events(repo: Path) -> list[sqlite3.Row]:
    database = repo / ".repowise" / "omissions" / "omissions.db"
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM savings_events").fetchall()


# -- what it counts ---------------------------------------------------------


def test_a_distilled_shell_result_becomes_one_measured_event(repo: Path, projects: Path) -> None:
    output = _distilled("aaaaaaaaaaaa", 900)
    _write(projects, repo, _pair(output, cwd=str(repo)))

    result = _sync(repo, projects)

    assert result.recorded == 1
    (event,) = _events(repo)
    assert event["evidence_kind"] == "measured"
    assert event["result_state"] == "success"
    # delivered is the text the agent actually received; baseline adds back
    # what the marker says was dropped, minus the marker's own cost.
    delivered = estimate_tokens(output)
    marker_cost = estimate_tokens(render_marker("aaaaaaaaaaaa", 100, 900))
    assert event["delivered_input_tokens"] == delivered
    assert event["baseline_input_tokens"] == max(delivered - marker_cost, 0) + 900
    assert event["saved_input_tokens"] == (
        event["baseline_input_tokens"] - event["delivered_input_tokens"]
    )
    assert result.saved_input_tokens == event["saved_input_tokens"]


def test_the_event_is_attributed_to_the_harness_it_came_from(repo: Path, projects: Path) -> None:
    """The live path writes unknown here; a transcript knows who was running."""
    _write(projects, repo, _pair(_distilled("bbbbbbbbbbbb", 500), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["agent"] == "claude_code"
    assert event["integration"] == "claude_code"


def test_backfilled_events_are_stamped_apart_from_live_ones(repo: Path, projects: Path) -> None:
    """Only two of six agents keep transcripts, so the two populations must
    stay separable or the per-agent split can never be labelled."""
    _write(projects, repo, _pair(_distilled("cccccccccccc", 500), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    # The literal, not the constant: comparing the module's own value to
    # itself passes whatever the value is, including the live one.
    assert event["estimator"] == "transcript_marker_v1"
    assert ESTIMATOR != "chars_per_token_floor_v1"


def test_history_is_not_valued_at_todays_rates(repo: Path, projects: Path) -> None:
    _write(projects, repo, _pair(_distilled("dddddddddddd", 500), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] is None
    assert event["input_rate_usd_per_million"] is None


def test_the_event_carries_the_transcripts_own_timestamp(repo: Path, projects: Path) -> None:
    then = NOW - 90 * 86400
    _write(projects, repo, _pair(_distilled("eeeeeeeeeeee", 500), cwd=str(repo), ts=then))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["occurred_at"].startswith(datetime.fromtimestamp(then, UTC).strftime("%Y-%m-%d"))


def test_the_host_truncation_caps_the_baseline(repo: Path, projects: Path) -> None:
    """Bytes past the host's own cap never reached the model and cannot be
    claimed -- the same ceiling the live path applies."""
    _write(projects, repo, _pair(_distilled("ffffffffffff", 5_000_000), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["baseline_input_tokens"] == HOST_OUTPUT_CAP_TOKENS


def test_the_filter_name_is_recovered_when_the_omission_row_survives(
    repo: Path, projects: Path
) -> None:
    from repowise.core.distill.store import OmissionStore

    store = OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")
    ref = store.put("x" * 100, source="cli:git_diff", original_tokens=900, kept_tokens=25)
    store.close()
    _write(projects, repo, _pair(_distilled(ref, 800), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["operation"] == "git_diff"
    assert event["surface"] == "distill"


def test_a_pruned_ref_records_an_unknown_filter_rather_than_a_guess(
    repo: Path, projects: Path
) -> None:
    _write(projects, repo, _pair(_distilled("999999999999", 800), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["operation"] == "unknown"


# -- what it refuses to count ----------------------------------------------


def test_a_marker_in_an_mcp_result_is_left_to_the_mcp_surface(repo: Path, projects: Path) -> None:
    _write(
        projects,
        repo,
        _pair(_distilled("111111111111", 900), cwd=str(repo), tool="mcp__repowise__get_context"),
    )

    result = _sync(repo, projects)

    assert result.recorded == 0


def test_a_marker_in_prose_is_not_a_saving(repo: Path, projects: Path) -> None:
    """Markers appear in documentation, pull-request bodies and fixtures. An
    entry with no tool result at all carries no evidence of a command."""
    _write(
        projects,
        repo,
        [
            {
                "type": "assistant",
                "cwd": str(repo),
                "timestamp": _iso(NOW),
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": _distilled("222222222222", 900)},
                    ],
                },
            }
        ],
    )

    result = _sync(repo, projects)

    assert result.recorded == 0


def test_the_documented_example_marker_is_not_counted(repo: Path, projects: Path) -> None:
    """The docs print ``~6.1k tokens``, rounded. It is quoted into transcripts
    often enough that admitting it would put a fabricated saving in the
    ledger, and requiring both counts is what keeps it out."""
    example = (
        KEPT + "\n[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); "
        "restore: repowise expand a1b2c3d4e5f6]\n"
    )
    _write(projects, repo, _pair(example, cwd=str(repo)))

    result = _sync(repo, projects)

    assert result.recorded == 0


def test_another_repositorys_session_is_not_counted(
    repo: Path, projects: Path, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    _write(projects, repo, _pair(_distilled("333333333333", 900), cwd=str(other)))

    result = _sync(repo, projects)

    assert result.recorded == 0


# -- deduplication ----------------------------------------------------------


def test_the_same_ref_in_two_sessions_is_one_saving(repo: Path, projects: Path) -> None:
    """The ref is content-addressed, so the same ref is the same distilled
    output -- a compaction replay, usually. Counting once is the floor."""
    output = _distilled("444444444444", 900)
    _write(projects, repo, _pair(output, cwd=str(repo)), name="s1")
    _write(projects, repo, _pair(output, cwd=str(repo), block_id="toolu_02"), name="s2")

    result = _sync(repo, projects)

    assert result.recorded == 1
    assert len(_events(repo)) == 1


def test_a_ref_the_ledger_already_claims_is_left_alone(repo: Path, projects: Path) -> None:
    """Dedup rides the omission ref, not the idempotency key: a live event's
    key is seeded on a random id and carries no content identity."""
    from repowise.core.distill.store import OmissionStore

    database = repo / ".repowise" / "omissions" / "omissions.db"
    OmissionStore(database).close()
    assert recorder.record_event(
        repo,
        {
            "event_id": "11111111-1111-4111-8111-111111111111",
            "idempotency_key": "sha256:" + "0" * 64,
            "occurred_at": datetime.now(UTC),
            "surface": "distill",
            "integration": "unknown",
            "agent": "unknown",
            "operation": "git_diff",
            "evidence_kind": "measured",
            "estimator": "chars_per_token_floor_v1",
            "token_unit": "estimated_tokens",
            "result_state": "success",
            "is_usable": True,
            "baseline_input_tokens": 1000,
            "delivered_input_tokens": 100,
            "omission_refs": ("555555555555",),
        },
    )
    _write(projects, repo, _pair(_distilled("555555555555", 900), cwd=str(repo)))

    result = _sync(repo, projects)

    assert result.recorded == 0
    assert result.already_recorded == 1
    assert len(_events(repo)) == 1


def test_a_second_run_over_unchanged_transcripts_records_nothing(
    repo: Path, projects: Path
) -> None:
    _write(projects, repo, _pair(_distilled("666666666666", 900), cwd=str(repo)))

    assert _sync(repo, projects).recorded == 1
    second = _sync(repo, projects)

    assert second.recorded == 0
    assert len(_events(repo)) == 1


def test_a_second_run_picks_up_what_was_appended_since(repo: Path, projects: Path) -> None:
    path = _write(projects, repo, _pair(_distilled("777777777777", 900), cwd=str(repo)))
    assert _sync(repo, projects).recorded == 1

    with path.open("a", encoding="utf-8") as handle:
        for entry in _pair(_distilled("888888888888", 900), cwd=str(repo), block_id="toolu_09"):
            handle.write(json.dumps(entry) + "\n")

    assert _sync(repo, projects).recorded == 1
    assert len(_events(repo)) == 2


def test_the_cursor_is_this_surfaces_own(repo: Path, projects: Path) -> None:
    """Sharing the decision miner's staging cursors would starve whichever ran
    second: that cursor advances as the bytes are read."""
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa1", 900), cwd=str(repo)))

    _sync(repo, projects)

    assert (repo / ".repowise" / "omissions" / "transcript-cursors.json").is_file()
    assert not (repo / ".repowise" / "sessions").exists()


# -- writing, and not writing ----------------------------------------------


def test_sync_creates_the_sidecar_the_recorder_never_would(repo: Path, projects: Path) -> None:
    """Being run is the opt-in the recorder cannot assume from a hook."""
    database = recorder.sidecar_path(repo)
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa2", 900), cwd=str(repo)))
    assert not database.exists()

    _sync(repo, projects)

    assert database.is_file()


def test_a_dry_run_writes_nothing_at_all(repo: Path, projects: Path) -> None:
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa3", 900), cwd=str(repo)))

    result = _sync(repo, projects, dry_run=True)

    assert result.recorded == 1
    assert result.saved_input_tokens > 0
    assert not recorder.sidecar_path(repo).exists()
    assert not (repo / ".repowise" / "omissions" / "transcript-cursors.json").exists()


def test_a_dry_run_leaves_the_next_real_run_everything_to_do(repo: Path, projects: Path) -> None:
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa4", 900), cwd=str(repo)))

    _sync(repo, projects, dry_run=True)

    assert _sync(repo, projects).recorded == 1


def test_an_exhausted_budget_defers_rather_than_dropping(
    repo: Path, projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline is checked between files, so a stop leaves whole files
    unread and the next run picks them up."""
    for index in range(4):
        _write(
            projects,
            repo,
            _pair(_distilled(f"{index}00000000000", 900), cwd=str(repo)),
            name=f"s{index}",
        )

    # Expire after the second file rather than before the first, so the stop
    # is mid-sweep: a budget that runs out before any read defers everything
    # and proves nothing about resuming.
    ticks = iter([0.0, 0.0, 1.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr("repowise.core.savings.transcript.time.monotonic", lambda: next(ticks))
    result = _sync(repo, projects, budget=50.0)

    assert result.deferred > 0
    assert not result.complete
    assert result.recorded + result.deferred == 4

    monkeypatch.undo()
    rest = _sync(repo, projects)
    assert rest.deferred == 0
    assert len(_events(repo)) == 4


def test_a_repository_with_no_transcripts_records_nothing(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "no-transcripts"
    empty.mkdir()

    result = sync_transcript_savings(repo, harnesses=("claude_code",), projects_root=empty)

    assert result.recorded == 0
    assert result.transcripts_read == 0


# -- scoping, pairing and origins ------------------------------------------


def test_a_session_that_states_no_directory_is_not_claimed(repo: Path, projects: Path) -> None:
    """One harness returns every rollout on the machine from `discover`, so a
    stated cwd is the only thing separating this repository from another.
    Reading an absent one as "no opinion" would bank another repo's savings
    here, and the anti-join is per repository so both would count it."""
    entries = _pair(_distilled("aaaaaaaaaaa5", 900), cwd=str(repo))
    for entry in entries:
        del entry["cwd"]
    _write(projects, repo, entries)

    assert _sync(repo, projects).recorded == 0


def test_a_session_in_a_subdirectory_is_this_repositorys(repo: Path, projects: Path) -> None:
    nested = repo / "packages" / "core"
    nested.mkdir(parents=True)
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa6", 900), cwd=str(nested)))

    assert _sync(repo, projects).recorded == 1


def test_a_result_whose_call_was_never_seen_is_not_attributed(repo: Path, projects: Path) -> None:
    """Without its tool_use, nothing says the marker came from a shell command
    rather than an MCP response."""
    _, result_entry = _pair(_distilled("aaaaaaaaaaa7", 900), cwd=str(repo))
    _write(projects, repo, [result_entry])

    assert _sync(repo, projects).recorded == 0


def test_a_call_still_running_holds_its_file_back_rather_than_losing_it(
    repo: Path, projects: Path
) -> None:
    """The pairing that identifies a shell result sits behind the cursor. If
    the cursor advanced past an unanswered call, the marker appended later
    could never be attributed, and nothing would look for it again."""
    call, result_entry = _pair(_distilled("aaaaaaaaaaa8", 900), cwd=str(repo))
    path = _write(projects, repo, [call])

    assert _sync(repo, projects).recorded == 0

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result_entry) + "\n")

    assert _sync(repo, projects).recorded == 1


def test_an_mcp_sourced_ref_keeps_the_mcp_surface(repo: Path, projects: Path) -> None:
    from repowise.core.distill.store import OmissionStore

    store = OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")
    ref = store.put("y" * 100, source="mcp:get_context", original_tokens=900, kept_tokens=25)
    store.close()
    _write(projects, repo, _pair(_distilled(ref, 800), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["surface"] == "mcp"
    assert event["operation"] == "get_context"


def test_a_hook_sourced_ref_keeps_the_hook_surface(repo: Path, projects: Path) -> None:
    from repowise.core.distill.store import OmissionStore

    store = OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")
    ref = store.put("z" * 100, source="hook-codex:test_output", original_tokens=900, kept_tokens=25)
    store.close()
    _write(projects, repo, _pair(_distilled(ref, 800), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["surface"] == "hook"
    assert event["operation"] == "test_output"


def test_a_refused_write_holds_the_cursor_back(
    repo: Path, projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorder never raises: a contended sidecar comes back as False. An
    advanced cursor would step past a marker nothing recorded."""
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa9", 900), cwd=str(repo)))
    monkeypatch.setattr("repowise.core.savings.recorder.record_event_in", lambda *a, **k: False)

    refused = _sync(repo, projects)
    assert refused.recorded == 0
    assert refused.write_failures == 1

    monkeypatch.undo()
    assert _sync(repo, projects).recorded == 1


def test_one_harness_failing_does_not_cost_the_other_its_reads(
    repo: Path, projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(projects, repo, _pair(_distilled("aaaaaaaaaab1", 900), cwd=str(repo)))
    real = sync_transcript_savings.__globals__["get_adapter"]

    def explode(name: str):
        if name == "codex":
            raise RuntimeError("codex adapter is broken")
        return real(name)

    monkeypatch.setattr("repowise.core.savings.transcript.get_adapter", explode)

    result = sync_transcript_savings(
        repo, harnesses=("codex", "claude_code"), projects_root=projects
    )

    assert result.recorded == 1


def test_a_dry_run_against_an_existing_ledger_sees_what_it_already_holds(
    repo: Path, projects: Path
) -> None:
    _write(projects, repo, _pair(_distilled("aaaaaaaaaab2", 900), cwd=str(repo)))
    assert _sync(repo, projects).recorded == 1

    # A fresh cursor file, so the dry run re-reads the same marker rather than
    # finding nothing appended.
    (repo / ".repowise" / "omissions" / "transcript-cursors.json").unlink()
    dry = _sync(repo, projects, dry_run=True)

    assert dry.recorded == 0
    assert dry.already_recorded == 1


def test_every_marker_in_one_result_is_counted(repo: Path, projects: Path) -> None:
    output = (
        KEPT
        + "\n"
        + render_marker("aaaaaaaaaab3", 10, 400)
        + "\n"
        + render_marker("aaaaaaaaaab4", 10, 500)
        + "\n"
    )
    _write(projects, repo, _pair(output, cwd=str(repo)))

    assert _sync(repo, projects).recorded == 2


# -- what it was worth ------------------------------------------------------
#
# 93% of this ledger's tokens carried no rate at all, because the backfill
# dropped a model the transcript was stating plainly. Recovering it is not
# repricing: "never repriced" forbids valuing a past saving at *today's*
# model, and each event here is priced from the model that was in the chair
# when it happened, read out of the same file that proves the saving.


def test_an_event_is_priced_from_the_model_that_was_in_the_chair(
    repo: Path, projects: Path
) -> None:
    _write(
        projects,
        repo,
        _pair(_distilled("aaaabbbbcccc", 500), cwd=str(repo), model="claude-opus-5"),
    )

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] == "claude-opus-5"
    assert event["input_rate_usd_per_million"] == 5.0
    assert event["output_rate_usd_per_million"] == 25.0
    # Provenance names the method, so this population stays separable from a
    # live event priced off the session-model cache.
    assert event["pricing_source"] == "transcript_model:claude_code"
    assert event["pricing_version"]


def test_a_model_the_rate_table_does_not_know_leaves_the_event_unpriced(
    repo: Path, projects: Path
) -> None:
    """A guessed rate is worse than the silence it replaced. The lenient
    lookup would have answered $3/$15 here and stamped it as measured."""
    _write(
        projects,
        repo,
        _pair(_distilled("ddddeeeeffff", 500), cwd=str(repo), model="totally-made-up-xyz"),
    )

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] is None
    assert event["input_rate_usd_per_million"] is None
    assert event["pricing_source"] is None
    # The saving itself is unaffected: unpriced, never unrecorded.
    assert event["baseline_input_tokens"] > event["delivered_input_tokens"]


def test_a_model_stated_only_on_a_line_carrying_no_tool_call_still_reaches_the_event(
    repo: Path, projects: Path
) -> None:
    """The gate's widening, pinned by the case that motivated it.

    Codex states its model on a ``turn_context`` line with no tool call on it,
    so the tool-call prefilter consumed it and *every* Codex event was written
    unpriced -- measured at 0 of 1,219 candidates against 25 of 27 for Claude
    Code, which happens to state its model on the same line as the tool call.
    Codex is 93% of this machine's ledger, so the gate was the whole fix.

    Written in the Claude Code shape because that is what this module's
    fixtures speak; what it pins is the gate, which is harness-agnostic.
    Remove ``'"model"' in raw_line`` from ``_gate`` and this fails.
    """
    entries = _pair(_distilled("111122223333", 500), cwd=str(repo))
    standalone = {
        "type": "assistant",
        "cwd": str(repo),
        "timestamp": _iso(NOW - 10),
        "message": {"role": "assistant", "model": "claude-opus-5", "content": []},
    }
    _write(projects, repo, [standalone, *entries])

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] == "claude-opus-5"


def test_a_models_reach_stops_at_the_transcript_it_was_stated_in(
    repo: Path, projects: Path
) -> None:
    """Carrying the model forward must not carry it across sessions. A second
    transcript that names no model is unpriced, not priced at the first's."""
    _write(
        projects,
        repo,
        _pair(_distilled("444455556666", 500), cwd=str(repo), model="claude-opus-5"),
        name="s1",
    )
    _write(
        projects,
        repo,
        _pair(_distilled("777788889999", 500), cwd=str(repo), block_id="toolu_02"),
        name="s2",
    )

    _sync(repo, projects)

    events = _events(repo)
    assert len(events) == 2
    assert sorted(row["model"] or "" for row in events) == ["", "claude-opus-5"]


# -- the baseline is a ceiling, not a guess ---------------------------------


def test_no_event_can_deliver_more_than_its_own_baseline(
    repo: Path, projects: Path
) -> None:
    """The invariant a measured pair cannot violate, and nothing asserted it.

    ``baseline`` is what the output cost before distillation and ``delivered``
    is what the model read back, so ``delivered <= baseline`` holds by
    construction -- unless the cap applied to the baseline is not the cap the
    host actually applied. On this machine 305 stored events carry a baseline
    of exactly 7,500 tokens and 125 of them deliver more than that, which is
    proof by contradiction that the cap did not apply to them.

    Written against a delivered size larger than the Claude Code cap, which
    is exactly the shape that was being mis-accounted.
    """
    big = "x" * (HOST_OUTPUT_CAP_TOKENS * 4 * 3)
    _write(
        projects,
        repo,
        _pair(_distilled("abcdef012345", 500, kept=big), cwd=str(repo)),
    )

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["delivered_input_tokens"] <= event["baseline_input_tokens"], (
        f"delivered {event['delivered_input_tokens']} exceeds baseline "
        f"{event['baseline_input_tokens']}: the cap charged to this event is "
        "not the cap its host applied"
    )


def _accounting_for(harness: str, *, delivered: int, omitted: int) -> tuple[int, int]:
    """``_accounting`` for one synthetic marker, at the unit level.

    Direct rather than through ``_sync`` because the fixtures here speak only
    the Claude Code transcript shape, and what is under test is precisely the
    behaviour that must differ *between* harnesses.
    """
    from repowise.core.distill.markers import parse_markers
    from repowise.core.savings.transcript import _accounting

    text = render_marker("abcdef012345", 100, omitted)
    (marker,) = parse_markers(text)
    candidate = _Candidate(
        marker=marker,
        harness=harness,
        occurred_at=datetime.fromtimestamp(NOW, UTC),
        delivered_tokens=delivered,
        session_id=None,
    )
    return _accounting(candidate)


def test_claude_codes_cap_is_still_charged_to_claude_code(repo: Path) -> None:
    """The confinement. Its 30,000 characters were measured exactly right:
    across 117,148 shell results the largest it ever delivered is 30,000
    characters to the byte, and none exceeds it."""
    baseline, _ = _accounting_for("claude_code", delivered=100, omitted=5_000_000)

    # The literal, not the constant: comparing the module's own value to
    # itself passes whatever the value is, including a wrong one.
    assert baseline == 7_500
    assert HOST_OUTPUT_CAP_TOKENS == 7_500


def test_claude_codes_cap_is_not_charged_to_codex(repo: Path) -> None:
    """Codex delivers results two orders of magnitude larger and truncates
    nowhere near 7,500 tokens. Charging it Claude Code's limit clipped 23% of
    this ledger's events and made 125 of them deliver more than they cost."""
    # Over Claude Code's 7,500 and under Codex's own, so nothing clips and
    # the marker's arithmetic survives whole.
    baseline, _ = _accounting_for("codex", delivered=100, omitted=9_000)

    assert baseline > HOST_OUTPUT_CAP_TOKENS
    assert baseline == 9_000 + 100 - estimate_tokens(
        render_marker("abcdef012345", 100, 9_000)
    )


def test_codex_is_capped_at_its_own_measured_plateau(repo: Path) -> None:
    """Pins the central claim of the per-agent cap, which nothing else did.

    The sibling test above deliberately stays *under* Codex's cap, so it
    passes whether Codex is capped at 10,000 or not capped at all. This one
    clips: set the Codex entry to ``None`` and it fails.
    """
    baseline, _ = _accounting_for("codex", delivered=100, omitted=5_000_000)

    # 2,552,250 characters: the largest result Codex was observed to deliver.
    assert baseline == 638_062


def test_a_harness_nobody_measured_is_not_charged_someone_elses_cap(repo: Path) -> None:
    """Asserting a truncation we have not observed is how this bug happened,
    so an unlisted harness gets no cap rather than the nearest one."""
    baseline, _ = _accounting_for("some_future_agent", delivered=100, omitted=9_000_000)

    # Larger than any cap in the table, so this pins "no cap" rather than
    # "some other cap": giving the unlisted harness Codex's fails.
    assert baseline > 8_000_000


def test_a_cap_never_clips_below_what_the_host_actually_delivered(repo: Path) -> None:
    """The invariant, made true by construction rather than by luck.

    A host cannot have truncated below what it demonstrably handed the model.
    Pinned on the *capped* harness, which is the only one where clipping can
    happen at all.
    """
    delivered = HOST_OUTPUT_CAP_TOKENS * 3
    baseline, reported = _accounting_for("claude_code", delivered=delivered, omitted=10)

    assert reported == delivered
    assert baseline >= delivered


def _assistant(cwd: str, model: str, *, sidechain: bool = False, ts: float = NOW) -> dict:
    """An assistant line that states a model and runs no tool."""
    entry = {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": _iso(ts),
        "message": {"role": "assistant", "model": model, "content": []},
    }
    if sidechain:
        entry["isSidechain"] = True
    return entry


def test_a_sub_agents_model_is_not_charged_to_the_main_thread(
    repo: Path, projects: Path
) -> None:
    """Claude Code interleaves Task sub-agent lines into the same transcript,
    and a sub-agent can run a different model. Carrying one forward prices the
    main thread's next command at the sub-agent's rate -- 5x out when a Haiku
    sub-agent lands between an Opus tool call and its result."""
    entries = [
        _assistant(str(repo), "claude-opus-5", ts=NOW - 20),
        _assistant(str(repo), "claude-haiku-4-5", sidechain=True, ts=NOW - 10),
        *_pair(_distilled("aabbccddeeff", 500), cwd=str(repo)),
    ]
    _write(projects, repo, entries)

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] == "claude-opus-5"
    assert event["input_rate_usd_per_million"] == 5.0


def test_a_sentinel_label_does_not_overwrite_a_known_model(
    repo: Path, projects: Path
) -> None:
    """Claude Code writes ``<synthetic>`` for an API error or an interrupted
    message. It resolves to no rate, which is right -- but if it is allowed to
    overwrite the carried model it leaves every later marker in the file
    unpriced, which is a silent loss rather than a refusal."""
    entries = [
        _assistant(str(repo), "claude-opus-5", ts=NOW - 20),
        _assistant(str(repo), "<synthetic>", ts=NOW - 10),
        *_pair(_distilled("ffeeddccbbaa", 500), cwd=str(repo)),
    ]
    _write(projects, repo, entries)

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["model"] == "claude-opus-5"
