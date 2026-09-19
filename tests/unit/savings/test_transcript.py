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
) -> list[dict]:
    return [
        {
            "type": "assistant",
            "cwd": cwd,
            "timestamp": _iso(ts),
            "message": {
                "role": "assistant",
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


def test_the_event_is_attributed_to_the_harness_it_came_from(
    repo: Path, projects: Path
) -> None:
    """The live path writes unknown here; a transcript knows who was running."""
    _write(projects, repo, _pair(_distilled("bbbbbbbbbbbb", 500), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["agent"] == "claude_code"
    assert event["integration"] == "claude_code"


def test_backfilled_events_are_stamped_apart_from_live_ones(
    repo: Path, projects: Path
) -> None:
    """Only two of six agents keep transcripts, so the two populations must
    stay separable or the per-agent split can never be labelled."""
    _write(projects, repo, _pair(_distilled("cccccccccccc", 500), cwd=str(repo)))

    _sync(repo, projects)

    (event,) = _events(repo)
    assert event["estimator"] == ESTIMATOR
    assert event["estimator"] != "chars_per_token_floor_v1"


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
    assert event["occurred_at"].startswith(
        datetime.fromtimestamp(then, UTC).strftime("%Y-%m-%d")
    )


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


def test_a_marker_in_an_mcp_result_is_left_to_the_mcp_surface(
    repo: Path, projects: Path
) -> None:
    _write(
        projects,
        repo,
        _pair(_distilled("111111111111", 900), cwd=str(repo), tool="mcp__repowise__get_context"),
    )

    result = _sync(repo, projects)

    assert result.recorded == 0


def test_a_marker_in_prose_is_not_a_saving(repo: Path, projects: Path) -> None:
    """Markers appear in documentation, pull-request bodies and fixtures. Only
    a shell result is evidence that a command's output was distilled."""
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
        KEPT
        + "\n[repowise#a1b2c3d4e5f6: 230 lines omitted (~6.1k tokens); "
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


def test_sync_creates_the_sidecar_the_recorder_never_would(
    repo: Path, projects: Path
) -> None:
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


def test_a_dry_run_leaves_the_next_real_run_everything_to_do(
    repo: Path, projects: Path
) -> None:
    _write(projects, repo, _pair(_distilled("aaaaaaaaaaa4", 900), cwd=str(repo)))

    _sync(repo, projects, dry_run=True)

    assert _sync(repo, projects).recorded == 1


def test_an_exhausted_budget_defers_rather_than_dropping(
    repo: Path, projects: Path
) -> None:
    for index in range(4):
        _write(
            projects,
            repo,
            _pair(_distilled(f"{index}00000000000", 900), cwd=str(repo)),
            name=f"s{index}",
        )

    result = _sync(repo, projects, budget=-1.0)

    assert result.deferred == 4
    assert result.recorded == 0
    assert not result.complete


def test_a_repository_with_no_transcripts_records_nothing(repo: Path, tmp_path: Path) -> None:
    empty = tmp_path / "no-transcripts"
    empty.mkdir()

    result = sync_transcript_savings(repo, harnesses=("claude_code",), projects_root=empty)

    assert result.recorded == 0
    assert result.transcripts_read == 0
