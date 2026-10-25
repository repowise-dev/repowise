"""``decision confirm --agent``: the CLI half of acceptance provenance.

Without ``--agent`` the identity resolves to the repository's git name, so an
agent confirming a decision signed the maintainer's. These hold the flag's
contract: an agent accepts by default and the record says an agent signed it,
and it is refused only where the repository turned agent acceptance off.
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


def _forbid_agents(repo: Path) -> None:
    result = _run(repo, "config", "agent-acceptance", "--off")
    assert result.exit_code == 0, result.output


def test_an_agent_accepts_with_no_config(repo: Path) -> None:
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code")

    assert result.exit_code == 0, result.output
    shown = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert (shown["kind"], shown["accepter"]) == ("agent", "claude_code")


def test_an_agent_is_refused_on_a_repository_that_turned_it_off(repo: Path) -> None:
    _forbid_agents(repo)
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "may not record" in payload["results"][0]["message"]
    assert "agent-acceptance --on" in payload["remedy"]


def test_the_refusal_leads_with_the_switch_it_was_refused_by(repo: Path) -> None:
    """The flags remedy stays, because one run can refuse two ids for two
    reasons, but it must not be the first thing an agent reads: nothing is
    missing from this record."""
    _forbid_agents(repo)
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code")

    remedy = json.loads(result.output)["remedy"]
    assert remedy.startswith("Agent acceptance is off")


def test_an_agent_signs_as_itself(repo: Path) -> None:
    result = _run(repo, "confirm", "aaaa", "--agent", "claude_code", "--session", "s-1")
    assert result.exit_code == 0, result.output

    shown = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert (shown["kind"], shown["accepter"], shown["session"]) == (
        "agent",
        "claude_code",
        "s-1",
    )


def test_confirming_without_the_flag_still_signs_as_a_person(repo: Path) -> None:
    """Agent acceptance must not change what an unflagged confirm records."""
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    shown = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert shown["kind"] == "person"


def test_a_candidate_reports_no_signer(repo: Path) -> None:
    assert json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"] is None


def test_a_batch_refusal_still_names_the_switch(repo: Path) -> None:
    """The remedy reached only the one-id path, so a scripted confirm of two
    ids was refused without ever being told which switch to turn on."""
    _forbid_agents(repo)
    result = _run(repo, "confirm", "aaaa", "bbbb", "--agent", "claude_code")

    assert result.exit_code == 1, result.output
    assert "agent-acceptance --on" in json.loads(result.output)["remedy"]


def test_the_remedy_sends_the_agent_to_a_person(repo: Path) -> None:
    """The repository switched agent grants off on purpose, so the agent's next
    step is a person, not the command that undoes the switch."""
    _forbid_agents(repo)
    remedy = json.loads(_run(repo, "confirm", "aaaa", "--agent", "claude_code").output)["remedy"]

    assert "yourself" not in remedy
    assert "Ask a person" in remedy


def test_a_withdrawal_is_not_reported_as_an_acceptance(repo: Path) -> None:
    """The same log records both, so the verb comes from the action."""
    assert _run(repo, "confirm", "aaaa").exit_code == 0
    assert _run(repo, "dismiss", "aaaa", "--yes").exit_code == 0

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert signed["verb"] == "Dismissed"
    assert signed["action"] == "dismissed"


def test_an_agent_signs_its_withdrawals_without_the_switch(repo: Path) -> None:
    """Withdrawing needs no policy, so it is the action an agent is most
    likely to take — and the one it would otherwise take under your name."""
    _forbid_agents(repo)
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    result = _run(repo, "dismiss", "aaaa", "--yes", "--agent", "claude_code", "--session", "s-9")
    assert result.exit_code == 0, result.output

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
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

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert (signed["kind"], signed["verb"]) == ("agent", "Superseded")


def test_an_agent_signs_a_retirement_with_no_named_successor(repo: Path) -> None:
    """That path goes through ``deprecate_decision``, not
    ``supersede_decision``, and it still appends a withdrawal, logged as a
    deprecation rather than a dismissal."""
    assert _run(repo, "confirm", "aaaa").exit_code == 0

    assert _run(repo, "deprecate", "aaaa", "--agent", "claude_code").exit_code == 0

    signed = json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"]
    assert signed["kind"] == "agent"
    assert (signed["verb"], signed["action"]) == ("Deprecated", "deprecated")


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
    assert json.loads(_run(repo, "show", "aaaa").output)["decision"]["signature"] is None


# --- decision merge --agent --------------------------------------------------


def test_an_agent_signs_a_merge(repo: Path) -> None:
    assert _run(repo, "confirm", "bbbb").exit_code == 0

    result = CliRunner().invoke(
        cli,
        [
            "decision", "merge", "aaaa", "bbbb", str(repo),
            "--agent", "claude_code", "--session", "s-3", "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output

    signed = json.loads(_run(repo, "show", "bbbb").output)["decision"]["signature"]
    assert (signed["kind"], signed["accepter"], signed["session"], signed["action"]) == (
        "agent",
        "claude_code",
        "s-3",
        "merged",
    )


def test_an_agent_merge_is_refused_where_agent_acceptance_is_off(repo: Path) -> None:
    assert _run(repo, "confirm", "bbbb").exit_code == 0
    _forbid_agents(repo)

    result = CliRunner().invoke(
        cli,
        ["decision", "merge", "aaaa", "bbbb", str(repo), "--agent", "claude_code",
         "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert "agent acceptance is off" in payload["results"][0]["message"]
    assert payload["remedy"].startswith("Agent acceptance is off")


def test_a_merge_without_the_flag_signs_as_a_person(repo: Path) -> None:
    assert _run(repo, "confirm", "bbbb").exit_code == 0

    result = _run(repo, "merge", "aaaa", "bbbb")
    assert result.exit_code == 0, result.output

    signed = json.loads(_run(repo, "show", "bbbb").output)["decision"]["signature"]
    assert (signed["kind"], signed["action"]) == ("person", "merged")


# --- decision add --agent ----------------------------------------------------


def _add(repo: Path, *extra: str, size: int = 512):
    return _run(
        repo,
        "add",
        "--title", f"Bound the cache at {size}",
        "--decision", f"Cap the cache at {size} entries",
        "--rationale", "unbounded growth ran a worker out of memory",
        "--affects", "src/cache.py",
        *extra,
    )


def _grounding(repo: Path, decision_id: str) -> dict:
    import sqlite3

    conn = sqlite3.connect(repo / ".repowise" / "wiki.db")
    try:
        row = conn.execute(
            "SELECT grounding_json FROM decision_candidate_meta WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


def test_an_agent_records_a_decision_as_its_own(repo: Path) -> None:
    added = _add(repo, "--agent", "claude_code", "--session", "s-9")
    assert added.exit_code == 0, added.output
    decision_id = json.loads(added.output)["decision"]["id"]

    shown = json.loads(_run(repo, "show", decision_id).output)["decision"]
    assert shown["source"] == "agent"
    assert shown["status"] == "proposed"
    assert _grounding(repo, decision_id) == {"agent": "claude_code", "session": "s-9"}


def test_an_agent_record_gets_no_manual_entry_evidence_exemption(repo: Path) -> None:
    """A person's typed record is its own evidence; an agent's is not."""
    by_person = json.loads(_add(repo, size=256).output)["decision"]["id"]
    by_agent = json.loads(_add(repo, "--agent", "claude_code").output)["decision"]["id"]

    assert _run(repo, "confirm", by_person).exit_code == 0

    refused = _run(repo, "confirm", by_agent)
    assert refused.exit_code == 1, refused.output
    blockers = json.loads(refused.output)["results"][0]["blockers"]
    assert any("evidence" in b for b in blockers)

    accepted = _run(repo, "confirm", by_agent, "--evidence", "abc1234")
    assert accepted.exit_code == 0, accepted.output


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--session", "s-1"), "pass --agent too"),
        (("--agent", "Claude Code"), "not a well-formed agent slug"),
    ],
)
def test_add_checks_the_signing_flags_before_writing(
    repo: Path, args: tuple[str, ...], expected: str
) -> None:
    result = _add(repo, *args)

    assert result.exit_code != 0
    assert expected in result.output
    assert json.loads(_run(repo, "list").output)["total"] == 2


def test_add_with_only_the_agent_flag_is_refused_not_prompted(repo: Path) -> None:
    result = CliRunner().invoke(
        cli, ["decision", "add", "--agent", "claude_code", str(repo)], input=""
    )

    assert result.exit_code != 0
    assert "--title and --decision are both required" in result.output


# --- confirm / dismiss with no ids -------------------------------------------


@pytest.mark.parametrize("verb", ["confirm", "dismiss"])
def test_a_review_verb_with_no_ids_points_at_the_candidates(repo: Path, verb: str) -> None:
    result = _run(repo, verb)

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"] == "no_decision_ids"
    assert "repowise decision candidates" in payload["remedy"]


def test_a_locked_store_is_one_line_not_a_traceback(repo: Path, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    import repowise.core.persistence as persistence

    async def _locked(engine):
        raise OperationalError("PRAGMA", {}, Exception("database is locked"))

    monkeypatch.setattr(persistence, "init_db", _locked)
    result = CliRunner().invoke(cli, ["decision", "confirm", "aaaa", str(repo)])

    assert result.exit_code == 1
    assert "database is locked" in result.output
    assert "retry once it finishes" in result.output
    assert "Traceback" not in result.output
