"""The capture prompt: the gates, not the wording.

An agent cannot decline a hook, so every gate here is a cost control and each
one is tested on its own. The contract is: off unless the repository switched
it on, at most once per session, only after a successful `git commit`, only on
a message that carries two or more decision signals, and never for a commit a
record already cites.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import decision_capture
from repowise.cli.commands.augment_cmd.decision_capture import commit_capture_notice

_MESSAGE = "refactor to one queue\n\nReplace the three ad-hoc queues; migrate callers."
_COMMIT = {"command": "git commit -m 'refactor to one queue'"}
_OK = {"exit_code": 0, "stdout": "", "stderr": ""}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An indexed repo with the switch on and one commit that states a choice."""
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    (root / ".git").mkdir()
    _write_policy(root, capture_prompt=True)
    _write_wiki(root, evidence=[])
    _fake_head(monkeypatch, "abc12345deadbeef", _MESSAGE)
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
        conn.execute(
            "INSERT INTO decision_records VALUES (?, ?)", (str(i), json.dumps([sha]))
        )
    conn.commit()
    conn.close()


def _fake_head(monkeypatch: pytest.MonkeyPatch, sha: str, message: str) -> None:
    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"{sha}\n{message}", stderr="")

    monkeypatch.setattr(decision_capture.subprocess, "run", _run)


def _fire(root: Path, session: str = "s1", **over) -> str | None:
    payload = {"tool_input": _COMMIT, "tool_output": _OK, "cwd": str(root), "session_id": session}
    payload.update(over)
    return commit_capture_notice(
        payload["tool_input"], payload["tool_output"], payload["cwd"], payload["session_id"]
    )


def test_it_fires_on_a_commit_that_states_a_choice(repo: Path) -> None:
    notice = _fire(repo)
    assert notice is not None
    assert "repowise decision add" in notice


def test_it_fires_at_most_once_a_session(repo: Path) -> None:
    assert _fire(repo) is not None
    assert _fire(repo) is None


def test_a_second_session_gets_its_own_ask(repo: Path) -> None:
    assert _fire(repo, session="s1") is not None
    assert _fire(repo, session="s2") is not None


def test_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    (root / ".repowise").mkdir(parents=True)
    _write_wiki(root, evidence=[])
    _fake_head(monkeypatch, "abc12345", _MESSAGE)

    assert _fire(root) is None


def test_the_off_switch_turns_it_off(repo: Path) -> None:
    _write_policy(repo, capture_prompt=False)

    assert _fire(repo) is None


def test_a_failed_commit_says_nothing(repo: Path) -> None:
    assert _fire(repo, tool_output={"exit_code": 1, "stdout": "", "stderr": "nothing to commit"}) is None


def test_a_non_commit_shell_call_says_nothing(repo: Path) -> None:
    assert _fire(repo, tool_input={"command": "git status"}) is None


def test_a_merge_is_not_a_commit_worth_asking_about(repo: Path) -> None:
    assert _fire(repo, tool_input={"command": "git merge --no-ff feature"}) is None


def test_one_signal_is_not_enough(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_head(monkeypatch, "abc12345", "remove a stray newline")

    assert _fire(repo) is None


def test_a_commit_a_record_already_cites_says_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".repowise" / "wiki.db").unlink()
    _write_wiki(repo, evidence=["abc12345"])

    assert _fire(repo) is None


def test_an_unreadable_store_stays_quiet_rather_than_guessing(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").write_text("not a database", encoding="utf-8")

    assert _fire(repo) is None


def test_a_git_failure_stays_quiet(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero, but with a body: the exit code is the only thing that saves us."""

    def _run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args, 128, stdout=f"deadbeefcafe\n{_MESSAGE}", stderr="fatal: bad revision"
        )

    monkeypatch.setattr(decision_capture.subprocess, "run", _run)

    assert _fire(repo) is None


def test_a_missing_store_stays_quiet_rather_than_guessing(repo: Path) -> None:
    (repo / ".repowise" / "wiki.db").unlink()

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


def test_the_session_is_claimed_even_though_the_agent_may_ignore_it(repo: Path) -> None:
    _fire(repo)
    state = json.loads(
        next((repo / ".repowise" / "hook-sessions").glob("*.json")).read_text(encoding="utf-8")
    )
    assert state["capture_prompted"] is True
