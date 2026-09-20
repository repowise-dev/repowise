"""``decision confirm --agent``: the CLI half of acceptance provenance.

Without ``--agent`` the identity resolves to the repository's git name, so an
agent confirming a decision signed the maintainer's. These hold the flag's
contract: it is refused unless the repository turned agent acceptance on, and
when it is allowed the record says an agent signed it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli

from .test_decision_cmd import _seed_wiki_db

_ID = "a" * 32


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    (path / ".repowise").mkdir()
    _seed_wiki_db(
        path,
        [
            {"id": _ID, "title": "Use JWT", "status": "proposed", "source": "pr"},
            {"id": "b" * 32, "title": "Use Postgres", "status": "proposed", "source": "pr"},
        ],
    )
    return path


def _run(repo: Path, *args: str):
    return CliRunner().invoke(cli, ["decision", *args, str(repo), "--format", "json"])


def _allow_agents(repo: Path) -> None:
    result = _run(repo, "config", "agent-acceptance", "--on")
    assert result.exit_code == 0, result.output


def test_an_agent_is_refused_on_a_repository_that_did_not_allow_it(repo: Path) -> None:
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "may not record" in payload["message"]
    assert "agent-acceptance --on" in payload["remedy"]


def test_the_refusal_leads_with_the_switch_it_was_refused_by(repo: Path) -> None:
    """The flags remedy stays, because one run can refuse two ids for two
    reasons, but it must not be the first thing an agent reads: nothing is
    missing from this record."""
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code")

    remedy = json.loads(result.output)["remedy"]
    assert remedy.startswith("Allow it with")


def test_an_allowed_agent_signs_as_itself(repo: Path) -> None:
    _allow_agents(repo)

    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code", "--session", "s-1")
    assert result.exit_code == 0, result.output

    shown = json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"]
    assert (shown["kind"], shown["accepter"], shown["session"]) == (
        "agent",
        "claude_code",
        "s-1",
    )


def test_confirming_without_the_flag_still_signs_as_a_person(repo: Path) -> None:
    """Turning the switch on must not change what an unflagged confirm records."""
    _allow_agents(repo)

    assert _run(repo, "confirm", "aaaa").exit_code == 0

    shown = json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"]
    assert shown["kind"] == "person"


def test_a_candidate_reports_no_signer(repo: Path) -> None:
    assert json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"] is None


def test_a_batch_refusal_still_names_the_switch(repo: Path) -> None:
    """The remedy reached only the one-id path, so a scripted confirm of two
    ids was refused without ever being told which switch to turn on."""
    result = _run(repo, "confirm", "aaaa", "bbbb", "--agent", "claude_code")

    assert result.exit_code == 1, result.output
    assert "agent-acceptance --on" in json.loads(result.output)["remedy"]


def test_a_withdrawal_is_not_reported_as_an_acceptance(repo: Path) -> None:
    """The same log records both, so the verb comes from the action."""
    _allow_agents(repo)
    assert _run(repo, "confirm", "aaaa").exit_code == 0
    assert _run(repo, "dismiss", "aaaa", "--yes").exit_code == 0

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"]
    assert signed["verb"] == "Dismissed"
    assert signed["action"] == "dismissed"


def test_an_agent_signs_its_withdrawals_without_the_switch(repo: Path) -> None:
    """Withdrawing needs no policy, so it is the action an agent is most
    likely to take — and the one it would otherwise take under your name."""
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    result = _run(repo, "dismiss", "aaaa", "--yes", "--agent", "claude_code", "--session", "s-9")
    assert result.exit_code == 0, result.output

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"]
    assert (signed["kind"], signed["accepter"], signed["session"]) == (
        "agent",
        "claude_code",
        "s-9",
    )
    assert signed["verb"] == "Dismissed"


def test_an_agent_signs_a_supersession(repo: Path) -> None:
    assert _run(repo, "confirm", "aaaa").exit_code == 0
    assert _run(repo, "confirm", "bbbb").exit_code == 0

    result = _run(
        repo, "deprecate", "aaaa", "--superseded-by", "bbbb", "--agent", "claude_code"
    )
    assert result.exit_code == 0, result.output

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"]
    assert (signed["kind"], signed["verb"]) == ("agent", "Superseded")


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--agent", "Claude Code"), "not a well-formed agent slug"),
        (("--agent", "claude_code", "--as", "Raghav"), "not both"),
        (("--session", "s-1"), "pass --agent too"),
    ],
)
def test_the_flag_is_checked_before_anything_is_written(
    repo: Path, args: tuple[str, ...], expected: str
) -> None:
    result = _run(repo, "confirm", "aaaa", *args)

    assert result.exit_code != 0
    assert expected in result.output
    assert json.loads(_run(repo, "show", "aaaa").output)["decision"]["accepted_by"] is None
