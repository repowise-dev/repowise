"""``repowise decision pin`` / ``unpin``, the refusal to replace a pinned decision,
and the recent section of ``decision status``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

from .test_decision_cmd import _seed_wiki_db

_A = "a" * 32
_B = "b" * 32


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / ".repowise").mkdir()
    _seed_wiki_db(
        path,
        [
            {"id": _A, "title": "Use JWT", "status": "proposed", "source": "pr"},
            {"id": _B, "title": "Use Postgres", "status": "proposed", "source": "pr"},
        ],
    )
    return path


def _run(repo: Path, *args: str, fmt: str = "json"):
    tail = ["--format", "json"] if fmt == "json" else []
    return CliRunner().invoke(cli, ["decision", *args, str(repo), *tail])


def _only(result) -> dict:
    (outcome,) = json.loads(result.output)["results"]
    return outcome


def _show(repo: Path, decision_id: str) -> dict:
    return json.loads(_run(repo, "show", decision_id).output)["decision"]


def test_pin_and_unpin_signed_as_the_agent_a_person_asked(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    pinned = _run(repo, "pin", "aaaa", "--agent", "claude_code", "--reason", "law")
    assert pinned.exit_code == 0, pinned.output
    assert _only(pinned)["action"] == "pinned"
    shown = _show(repo, "aaaa")
    assert shown["pinned"] is True
    # Pinning is a mark: the decision still reads as accepted, by whoever did.
    assert shown["signature"]["action"] == "accepted"

    unpinned = _run(repo, "unpin", "aaaa")
    assert unpinned.exit_code == 0, unpinned.output
    assert _show(repo, "aaaa")["pinned"] is False


def test_pin_refuses_a_background_record_and_names_confirm(repo: Path) -> None:
    result = _run(repo, "pin", "aaaa")

    assert result.exit_code == 1, result.output
    outcome = _only(result)
    assert outcome["error"] == "acceptance_refused"
    assert "decision confirm aaaaaaaa" in outcome["message"]


def test_pin_says_so_on_the_terminal(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    result = _run(repo, "pin", "aaaa", fmt="table")

    assert result.exit_code == 0, result.output
    assert "pinned" in result.output and "asks a person before replacing it" in result.output


def test_an_agent_is_refused_replacing_a_pinned_decision_in_plain_words(repo: Path) -> None:
    for rid in ("aaaa", "bbbb"):
        assert _run(repo, "confirm", rid).exit_code == 0
    assert _run(repo, "pin", "aaaa").exit_code == 0

    result = _run(repo, "deprecate", "aaaa", "--superseded-by", "bbbb", "--agent", "claude_code")

    assert result.exit_code == 1, result.output
    outcome = _only(result)
    assert outcome["error"] == "acceptance_refused"
    assert "a person marked this decision as law. Ask them before replacing it" in outcome["message"]
    assert "repowise decision unpin aaaaaaaa" in outcome["message"]
    assert _show(repo, "aaaa")["pinned"] is True

    dismissed = _run(repo, "dismiss", "aaaa", "--agent", "claude_code", "--yes")
    assert dismissed.exit_code == 1 and "Ask them" in _only(dismissed)["message"]


def test_force_is_a_persons_and_replaces_a_pinned_decision(repo: Path) -> None:
    for rid in ("aaaa", "bbbb"):
        assert _run(repo, "confirm", rid).exit_code == 0
    assert _run(repo, "pin", "aaaa").exit_code == 0

    agent_force = _run(
        repo, "deprecate", "aaaa", "--superseded-by", "bbbb", "--agent", "claude_code", "--force"
    )
    assert agent_force.exit_code != 0
    assert "--force is for a person" in agent_force.output

    unforced = _run(repo, "deprecate", "aaaa", "--superseded-by", "bbbb")
    assert unforced.exit_code == 1
    assert "replace it anyway with --force" in _only(unforced)["message"]

    forced = _run(repo, "deprecate", "aaaa", "--superseded-by", "bbbb", "--force")
    assert forced.exit_code == 0, forced.output
    shown = _show(repo, "aaaa")
    assert (shown["signature"]["action"], shown["pinned"]) == ("superseded", False)


def test_status_lists_what_came_into_force_this_week(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa", "--evidence", "abc1234").exit_code == 0
    assert _run(repo, "pin", "aaaa").exit_code == 0

    report = json.loads(_run(repo, "status").output)

    recent = report["recent"]
    assert (recent["days"], recent["count"], recent["pinned"]) == (7, 1, 1)
    (row,) = recent["decisions"]
    assert (row["id"], row["title"], row["signer_kind"], row["commit"], row["pinned"]) == (
        _A, "Use JWT", "person", "abc1234", True,
    )

    text = _run(repo, "status", fmt="table").output
    assert "Recent: put in force in the last 7 days (1)" in text
    assert "Use JWT" in text and "abc1234" in text


def test_status_says_when_nothing_is_new(repo: Path) -> None:
    text = _run(repo, "status", fmt="table").output
    assert "Nothing new is in force." in text
