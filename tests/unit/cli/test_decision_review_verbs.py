"""The review verbs an agent drives: reaffirm, deprecate, merge, dismiss, split.

Each must refuse honestly, name the next step, sign as the agent that acted,
and answer ``--format json`` in the one batch shape whatever the id count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

from .test_decision_cmd import _seed_wiki_db


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / ".repowise").mkdir()
    _seed_wiki_db(
        path,
        [
            {"id": "a" * 32, "title": "Use JWT", "status": "proposed", "source": "pr"},
            {"id": "b" * 32, "title": "Use Postgres", "status": "proposed", "source": "pr"},
        ],
    )
    return path


def _run(repo: Path, *args: str):
    return CliRunner().invoke(cli, ["decision", *args, str(repo), "--format", "json"])


def _only(result) -> dict:
    (outcome,) = json.loads(result.output)["results"]
    return outcome


def _signature(repo: Path, decision_id: str) -> dict | None:
    return json.loads(_run(repo, "show", decision_id).output)["decision"]["signature"]


def test_confirming_a_decision_twice_points_at_reaffirm(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    again = _run(repo, "confirm", "aaaa")

    assert again.exit_code == 1, again.output
    outcome = _only(again)
    assert outcome["error"] == "already_accepted"
    assert "decision reaffirm aaaaaaaa" in outcome["message"]
    assert "remedy" not in json.loads(again.output)
    assert _signature(repo, "aaaa")["action"] == "accepted"


def test_reaffirm_refuses_a_candidate_and_points_at_confirm(repo: Path) -> None:
    result = _run(repo, "reaffirm", "aaaa")

    assert result.exit_code == 1, result.output
    assert "decision confirm" in _only(result)["message"]
    assert _signature(repo, "aaaa") is None


def test_an_agent_reaffirms_a_decision_signed_as_itself(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    result = _run(
        repo, "reaffirm", "aaaa", "--agent", "claude_code", "--reason", "re-read; still holds"
    )

    assert result.exit_code == 0, result.output
    assert _only(result)["action"] == "reaffirmed"
    signed = _signature(repo, "aaaa")
    assert (signed["action"], signed["kind"], signed["accepter"]) == (
        "reaffirmed",
        "agent",
        "claude_code",
    )


def test_deprecate_preview_writes_nothing(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    result = _run(repo, "deprecate", "aaaa", "--preview")

    assert result.exit_code == 0, result.output
    assert _only(result)["action"] == "would_deprecate"
    assert _signature(repo, "aaaa")["action"] == "accepted"


def test_decision_health_counts_a_deprecation(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    assert _run(repo, "deprecate", "aaaa", "--reason", "replaced by sessions").exit_code == 0

    summary = json.loads(_run(repo, "health").output)["summary"]
    assert (summary["deprecated"], summary["dismissed"]) == (1, 0)


def test_merging_into_a_candidate_names_the_next_step(repo: Path) -> None:
    result = _run(repo, "merge", "aaaa", "bbbb")

    assert result.exit_code == 1, result.output
    outcome = _only(result)
    assert outcome["error"] == "merge_refused"
    assert "repowise decision confirm bbbbbbbb" in outcome["message"]
    assert "merge the other way" in outcome["message"]


def test_merge_preview_writes_nothing(repo: Path) -> None:
    assert _run(repo, "confirm", "bbbb").exit_code == 0

    result = _run(repo, "merge", "aaaa", "bbbb", "--preview")

    assert result.exit_code == 0, result.output
    outcome = _only(result)
    assert (outcome["action"], outcome["merged_into"]) == ("would_merge", "b" * 32)
    assert _signature(repo, "bbbb")["action"] == "accepted"


def test_dismissing_a_candidate_records_who_did_it(repo: Path) -> None:
    result = _run(
        repo, "dismiss", "aaaa", "--agent", "claude_code", "--session", "s-1", "--reason", "noise"
    )
    assert result.exit_code == 0, result.output

    listed = json.loads(_run(repo, "candidates", "--state", "dismissed").output)["candidates"]
    assert [c["reviewed_by"] for c in listed] == [
        {"accepter": "claude_code", "kind": "agent", "session": "s-1", "reason": "noise"}
    ]
    signed = _signature(repo, "aaaa")
    assert (signed["verb"], signed["kind"], signed["session"]) == ("Dismissed", "agent", "s-1")


def test_a_split_request_is_signed(repo: Path) -> None:
    result = _run(repo, "split", "aaaa", "--agent", "claude_code", "--reason", "two choices")
    assert result.exit_code == 0, result.output

    listed = json.loads(_run(repo, "candidates", "--state", "needs_split").output)["candidates"]
    assert listed[0]["reviewed_by"]["accepter"] == "claude_code"
