"""A decision an agent records governs once its commit lands on the default branch.

Real ``git`` and the real CLI: what is under test is the identity between the
branch commit a record cites and the merge, squash or rebase that carried it,
and that nobody has to run anything between the merge and the next update.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd.persistence import accept_landed_decisions
from repowise.cli.main import cli

from .test_decision_cmd import _create_empty_index

_SUBJECT = "feat: sign sessions with JWT"


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    out = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def _commit(repo: Path, message: str, path: str, content: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text(".repowise/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _commit(root, "start", "src/auth.py", "start\n")
    _create_empty_index(root)
    return root


def _run(repo: Path, *args: str):
    result = CliRunner().invoke(cli, ["decision", *args, str(repo), "--format", "json"])
    return result


def _add(repo: Path, commit: str) -> str:
    result = _run(
        repo,
        "add",
        "--title",
        "Sessions are JWTs",
        "--decision",
        "Sign sessions as JWTs",
        "--rationale",
        "No shared session store across regions",
        "--affects",
        "src/auth.py",
        "--evidence-commit",
        commit,
        "--agent",
        "claude_code",
        "--session",
        "s-1",
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["decision"]["id"]


def _signature(repo: Path, decision_id: str) -> dict | None:
    result = _run(repo, "show", decision_id)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["decision"]["signature"]


def _branch_decision(repo: Path) -> tuple[str, str]:
    """A decision recorded with its commit on a topic branch; HEAD stays there."""
    _git(repo, "checkout", "-q", "-b", "topic")
    commit = _commit(repo, _SUBJECT, "src/auth.py", "jwt\n")
    return _add(repo, commit), commit


def _merge_commit(repo: Path) -> str:
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge topic", "topic")
    return _git(repo, "rev-parse", "HEAD")


def _squash(repo: Path) -> str:
    _git(repo, "checkout", "-q", "main")
    return _commit(repo, f"{_SUBJECT} (#7)", "src/auth.py", "jwt\n")


def _rebase(repo: Path) -> str:
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "docs: readme", "README.md", "hi\n")
    _git(repo, "checkout", "-q", "topic")
    _git(repo, "rebase", "-q", "main")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "topic")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("land", [_merge_commit, _squash, _rebase], ids=["merge", "squash", "rebase"])
def test_a_branch_decision_governs_after_its_branch_lands(repo: Path, land) -> None:
    decision_id, commit = _branch_decision(repo)

    assert accept_landed_decisions(repo) == 0
    assert _signature(repo, decision_id) is None

    landed = land(repo)
    assert landed != commit
    assert accept_landed_decisions(repo) == 1

    shown = _signature(repo, decision_id)
    assert shown["kind"] == "import"
    assert shown["accepter"] == "claude_code"
    assert shown["evidence"] == [commit, landed]
    # A second update finds nothing left to do.
    assert accept_landed_decisions(repo) == 0


def test_landing_on_the_default_branch_counts_while_head_is_elsewhere(repo: Path) -> None:
    """A fetch or a merge done elsewhere moves the default branch, not HEAD."""
    decision_id, _ = _branch_decision(repo)
    _merge_commit(repo)
    _git(repo, "checkout", "-q", "topic")

    assert accept_landed_decisions(repo) == 1
    assert _signature(repo, decision_id)["kind"] == "import"


def test_approval_mode_holds_a_landed_decision_for_a_person(repo: Path) -> None:
    assert _run(repo, "config", "approval", "--on").exit_code == 0
    decision_id, _ = _branch_decision(repo)
    _squash(repo)

    assert accept_landed_decisions(repo) == 0
    assert _signature(repo, decision_id) is None

    result = _run(repo, "confirm", decision_id[:8], "--agent", "claude_code")
    assert result.exit_code == 1, result.output
    assert "requires a person" in json.loads(result.output)["remedy"]

    assert _run(repo, "confirm", decision_id[:8], "--as", "maintainer").exit_code == 0
    assert _signature(repo, decision_id)["kind"] == "person"


def test_an_agent_accepting_before_the_merge_is_held_until_it_lands(repo: Path) -> None:
    decision_id, _ = _branch_decision(repo)

    result = _run(repo, "confirm", decision_id[:8], "--agent", "codex", "--session", "s-2")
    assert result.exit_code == 0, result.output
    outcome = json.loads(result.output)["results"][0]
    assert (outcome["action"], outcome["waits_for"]) == ("pending", "main")
    assert _signature(repo, decision_id) is None

    _squash(repo)
    assert accept_landed_decisions(repo) == 1
    shown = _signature(repo, decision_id)
    # The agent that accepted last is the one named.
    assert (shown["kind"], shown["accepter"]) == ("import", "codex")


def test_an_agent_accepting_after_the_merge_governs_at_once(repo: Path) -> None:
    decision_id, _ = _branch_decision(repo)
    _squash(repo)

    result = _run(repo, "confirm", decision_id[:8], "--agent", "claude_code")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["results"][0]["action"] == "accepted"
    assert _signature(repo, decision_id)["kind"] == "agent"


def test_a_decision_citing_no_commit_is_left_for_a_person_or_agent(repo: Path) -> None:
    result = _run(
        repo,
        "add",
        "--title",
        "Keep auth pure",
        "--decision",
        "No I/O in auth",
        "--rationale",
        "Testable",
        "--affects",
        "src/auth.py",
        "--agent",
        "claude_code",
    )
    decision_id = json.loads(result.output)["decision"]["id"]

    assert accept_landed_decisions(repo) == 0
    assert _signature(repo, decision_id) is None


def test_a_person_recorded_decision_does_not_govern_by_landing(repo: Path) -> None:
    commit = _git(repo, "rev-parse", "HEAD")
    result = _run(
        repo,
        "add",
        "--title",
        "Typed by a person",
        "--decision",
        "Use X",
        "--rationale",
        "Because",
        "--affects",
        "src/auth.py",
        "--evidence-commit",
        commit,
    )
    decision_id = json.loads(result.output)["decision"]["id"]

    assert accept_landed_decisions(repo) == 0
    assert _signature(repo, decision_id) is None


def test_the_export_carries_a_decision_that_governs_by_landing(repo: Path) -> None:
    decision_id, _ = _branch_decision(repo)
    landed = _squash(repo)
    accept_landed_decisions(repo)

    assert _run(repo, "export").exit_code == 0
    text = (repo / ".repowise" / "decisions.yaml").read_text(encoding="utf-8")
    assert decision_id in text
    assert landed in text


def test_a_preset_keeps_approval_on(repo: Path) -> None:
    assert _run(repo, "config", "approval", "--on").exit_code == 0
    assert _run(repo, "config", "preset", "balanced").exit_code == 0

    shown = json.loads(_run(repo, "config", "show").output)
    assert shown["policy"]["require_approval"] is True


def test_the_index_lifecycle_pass_accepts_a_landed_decision(repo: Path) -> None:
    """Init and the full update path reach it through the lifecycle step."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from repowise.core.persistence.models import Repository
    from repowise.core.pipeline.persist import apply_decision_lifecycle

    decision_id, _ = _branch_decision(repo)
    landed = _squash(repo)

    async def _run_pass() -> None:
        db = repo / ".repowise" / "wiki.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                from sqlalchemy import select

                repo_id = (await session.execute(select(Repository.id))).scalar_one()
                await apply_decision_lifecycle(session, repo_id, repo)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run_pass())
    assert landed in _signature(repo, decision_id)["evidence"]


def test_a_landing_reverted_since_puts_nothing_in_force(repo: Path) -> None:
    decision_id, _ = _branch_decision(repo)
    squash = _squash(repo)
    _git(repo, "revert", "--no-edit", squash)

    assert accept_landed_decisions(repo) == 0
    assert _signature(repo, decision_id) is None


def test_a_dismissed_agent_decision_stays_dismissed_when_it_lands(repo: Path) -> None:
    decision_id, _ = _branch_decision(repo)
    assert _run(repo, "dismiss", decision_id[:8], "--yes").exit_code == 0
    _squash(repo)

    assert accept_landed_decisions(repo) == 0
