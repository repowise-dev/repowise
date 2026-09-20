"""The capture prompt: the gates, not the wording.

An agent cannot decline a hook, so every gate is a cost control and each one
is tested on its own. The contract: off unless the repository switched it on,
at most once per session, only after a *fresh* commit in *this* repository,
only on a message carrying two or more decision signals, and never for a
commit a record already cites.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import decision_capture
from repowise.cli.commands.augment_cmd.decision_capture import commit_capture_notice

_MESSAGE = "refactor to one queue\n\nReplace the three ad-hoc queues; migrate callers."
_SHA = "abc12345deadbeefcafe0000111122223333aaaa"
_COMMIT = {"command": "git commit -m 'refactor to one queue'"}
_OK = {"exit_code": 0, "stdout": "", "stderr": ""}


@pytest.fixture(autouse=True)
def _no_cross_test_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """The emission claim is a temp-dir marker; keep tests independent of it."""
    from repowise.cli.commands.augment_cmd import command

    monkeypatch.setattr(command, "_claim_emission", lambda event, context: True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    _write_policy(root, capture_prompt=True)
    _write_wiki(root, evidence=[])
    _fake_git(monkeypatch, root, _SHA, _MESSAGE)
    return root


def _write_policy(root: Path, *, capture_prompt: bool) -> None:
    (root / ".repowise" / "config.yaml").write_text(
        "decisions:\n  capture_prompt: " + ("true" if capture_prompt else "false") + "\n",
        encoding="utf-8",
    )


def _write_wiki(root: Path, *, evidence: list[str]) -> None:
    conn = sqlite3.connect(root / ".repowise" / "wiki.db")
    conn.execute(
        "CREATE TABLE decision_records (id TEXT PRIMARY KEY, evidence_commits_json TEXT)"
    )
    for i, sha in enumerate(evidence):
        conn.execute("INSERT INTO decision_records VALUES (?, ?)", (str(i), json.dumps([sha])))
    conn.commit()
    conn.close()


def _fake_git(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    sha: str,
    message: str,
    *,
    age: float = 1.0,
    nested_root: Path | None = None,
    log_returncode: int = 0,
) -> None:
    """Stand in for both git calls: the work-tree root and HEAD.

    *nested_root* is what ``rev-parse`` answers for a cwd that is not the
    repowise root — the sibling-repo layout this tree actually has.
    """
    stamp = int(time.time() - age)

    def _run(args, **kwargs):
        if "rev-parse" in args:
            here = kwargs.get("cwd")
            where = nested_root if (nested_root and here != str(root)) else root
            return subprocess.CompletedProcess(args, 0, stdout=f"{where}\n", stderr="")
        return subprocess.CompletedProcess(
            args, log_returncode, stdout=f"{sha}\n{stamp}\n{message}", stderr=""
        )

    monkeypatch.setattr(decision_capture.subprocess, "run", _run)


def _fire(root: Path, session: str = "s1", **over) -> str | None:
    payload = {"tool_input": _COMMIT, "tool_output": _OK, "cwd": str(root), "session_id": session}
    payload.update(over)
    return commit_capture_notice(
        payload["tool_input"], payload["tool_output"], payload["cwd"], payload["session_id"]
    )


# --- it fires ---------------------------------------------------------------


def test_it_fires_on_a_commit_that_states_a_choice(repo: Path) -> None:
    notice = _fire(repo)
    assert notice is not None
    assert "repowise decision add" in notice
    # The sha is in the prompt so the record can cite it and suppress the ask.
    assert _SHA[:8] in notice
    assert "--evidence-commit" in notice


def test_it_fires_at_most_once_a_session(repo: Path) -> None:
    assert _fire(repo) is not None
    assert _fire(repo) is None


def test_a_second_session_gets_its_own_ask(repo: Path) -> None:
    assert _fire(repo, session="s1") is not None
    assert _fire(repo, session="s2") is not None


# --- the switch -------------------------------------------------------------


def test_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    _write_wiki(root, evidence=[])
    _fake_git(monkeypatch, root, _SHA, _MESSAGE)

    assert _fire(root) is None


def test_the_off_switch_turns_it_off(repo: Path) -> None:
    _write_policy(repo, capture_prompt=False)

    assert _fire(repo) is None


def test_an_absent_key_resolves_to_off(tmp_path: Path) -> None:
    """The parser's own default, not the dataclass field the parser overrides."""
    from repowise.core.analysis.decisions.policy_store import load_policy

    root = tmp_path / "unconfigured"
    (root / ".repowise").mkdir(parents=True)

    assert load_policy(root).policy.capture_prompt is False


def test_a_non_boolean_switch_warns_and_stays_off(tmp_path: Path) -> None:
    from repowise.core.analysis.decisions.policy_store import load_policy

    root = tmp_path / "typo"
    (root / ".repowise").mkdir(parents=True)
    (root / ".repowise" / "config.yaml").write_text(
        "decisions:\n  capture_prompt: yesplease\n", encoding="utf-8"
    )

    resolved = load_policy(root)
    assert resolved.policy.capture_prompt is False
    assert any("capture_prompt" in w for w in resolved.warnings)


def test_a_preset_does_not_switch_it_off(tmp_path: Path) -> None:
    """A preset names source membership; it must not reset an agent switch."""
    from dataclasses import replace

    from repowise.core.analysis.decisions.policy import preset_policy
    from repowise.core.analysis.decisions.policy_store import load_policy, write_policy

    root = tmp_path / "preset"
    (root / ".repowise").mkdir(parents=True)
    _write_policy(root, capture_prompt=True)
    current = load_policy(root).policy

    write_policy(
        root,
        replace(
            preset_policy("balanced"),
            harnesses=current.harnesses,
            agent_acceptance=current.agent_acceptance,
            capture_prompt=current.capture_prompt,
        ),
    )

    assert load_policy(root).policy.capture_prompt is True


# --- what counts as the commit ---------------------------------------------


def test_a_failed_commit_says_nothing(repo: Path) -> None:
    assert _fire(repo, tool_output={"exit_code": 1, "stdout": "", "stderr": ""}) is None


def test_a_status_key_is_read_as_an_exit_code(repo: Path) -> None:
    """One answer to "did that shell call succeed", shared with the staleness hook."""
    assert _fire(repo, tool_output={"status": 1, "stdout": "", "stderr": ""}) is None


def test_a_subject_that_says_error_still_fires(repo: Path, monkeypatch) -> None:
    """The old string scan read this as a failed commit. It is the target case."""
    _fake_git(
        monkeypatch, repo, _SHA, "refactor to one error path; remove the old fatal handler"
    )

    notice = _fire(repo, tool_output={"stdout": "[main abc1234] refactor to one error path"})

    assert notice is not None


def test_a_stale_head_is_not_this_commit(repo: Path, monkeypatch) -> None:
    """`git commit` with nothing staged prints no "error" and leaves HEAD alone."""
    _fake_git(monkeypatch, repo, _SHA, _MESSAGE, age=86_400)

    assert _fire(repo, tool_output={"stdout": "nothing to commit, working tree clean"}) is None


def test_a_dry_run_does_not_consume_the_ask(repo: Path) -> None:
    assert _fire(repo, tool_input={"command": "git commit --dry-run"}) is None
    assert _fire(repo) is not None


def test_a_non_commit_shell_call_says_nothing(repo: Path) -> None:
    assert _fire(repo, tool_input={"command": "git status"}) is None


def test_a_merge_is_not_a_commit_worth_asking_about(repo: Path) -> None:
    assert _fire(repo, tool_input={"command": "git merge --no-ff feature"}) is None


def test_a_commit_in_a_nested_repository_is_not_this_one(
    repo: Path, monkeypatch, tmp_path: Path
) -> None:
    """`backend/` and `frontend/` are separate git repos inside this tree."""
    nested = repo / "backend"
    nested.mkdir(parents=True, exist_ok=True)
    _fake_git(monkeypatch, repo, _SHA, _MESSAGE, nested_root=nested)

    assert _fire(repo, cwd=str(nested)) is None


def test_an_unidentified_session_says_nothing(repo: Path) -> None:
    """One shared state file, and the flag is never cleared: it would be permanent."""
    assert _fire(repo, session="") is None
    assert _fire(repo, session="s1") is not None


# --- what counts as a choice ------------------------------------------------


def test_one_signal_is_not_enough(repo: Path, monkeypatch) -> None:
    _fake_git(monkeypatch, repo, _SHA, "remove a stray newline")

    assert _fire(repo) is None


def test_a_commit_a_record_already_cites_says_nothing(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").unlink()
    _write_wiki(repo, evidence=[_SHA[:8]])

    assert _fire(repo) is None


def test_an_unreadable_store_stays_quiet_rather_than_guessing(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").write_text("not a database", encoding="utf-8")

    assert _fire(repo) is None


def test_a_missing_store_stays_quiet_rather_than_guessing(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").unlink()

    assert _fire(repo) is None


def test_a_git_failure_stays_quiet(repo: Path, monkeypatch) -> None:
    _fake_git(monkeypatch, repo, _SHA, _MESSAGE, log_returncode=128)

    assert _fire(repo) is None


def test_the_session_is_claimed_even_though_the_agent_may_ignore_it(repo: Path) -> None:
    _fire(repo)
    state = json.loads(
        next((repo / ".repowise" / "hook-sessions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert state["capture_prompted"] is True


def test_the_prompt_carries_no_console_markup(repo: Path) -> None:
    """`additionalContext` reaches the agent as text; rich markup would be literal."""
    notice = _fire(repo)

    assert "[dim]" not in notice and "[/dim]" not in notice


def test_a_commit_dated_ahead_of_this_clock_is_not_fresh(repo: Path, monkeypatch) -> None:
    """A one-sided age test passes a future stamp for free."""
    _fake_git(monkeypatch, repo, _SHA, _MESSAGE, age=-3600)

    assert _fire(repo) is None


def test_a_commit_and_a_slow_follow_up_still_fires(repo: Path, monkeypatch) -> None:
    """The hook fires when the whole shell call returns, not when git does."""
    _fake_git(monkeypatch, repo, _SHA, _MESSAGE, age=300)

    assert _fire(repo, tool_input={"command": "git commit -am wip && npm test"}) is not None


def test_two_unknown_git_roots_are_not_a_match(repo: Path, monkeypatch) -> None:
    """`None == None` read as "the roots match"."""

    def _run(args, **kwargs):
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="not a git repo")
        return subprocess.CompletedProcess(args, 0, stdout=f"{_SHA}\n{int(time.time())}\n{_MESSAGE}")

    monkeypatch.setattr(decision_capture.subprocess, "run", _run)

    assert _fire(repo) is None


def test_the_loser_of_the_claim_keeps_its_ask(repo: Path, monkeypatch) -> None:
    """Claiming after the state write burns the session on a prompt never shown."""
    from repowise.cli.commands.augment_cmd import command

    monkeypatch.setattr(command, "_claim_emission", lambda event, context: False)
    assert _fire(repo) is None

    monkeypatch.setattr(command, "_claim_emission", lambda event, context: True)
    assert _fire(repo) is not None
