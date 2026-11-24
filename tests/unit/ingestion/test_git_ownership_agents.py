"""A coding agent's commits count for the person behind them, from git alone.

The person is the first human ``Co-authored-by`` trailer, else a human
committer. With neither the commit stays the agent's; bots stay excluded.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import git
import pytest

from repowise.core.author_identity import accountable_human
from repowise.core.ingestion.git_indexer.file_history import _add_ownership, _Authors, index_file
from repowise.core.ingestion.git_indexer.records import repo_people

_NR = "@users.noreply.github.com"
_CLAUDE = "Claude <noreply@anthropic.com>"
_COPILOT = f"Copilot <198982749+Copilot{_NR}>"
_BOT = f"dependabot[bot] <49699333+dependabot[bot]{_NR}>"
_JANE = "Jane Roe <jane@acme.io>"
_OMAR = "Omar Haddad <omar@acme.io>"
_DEV = "Dev Lee <dev@acme.io>"
_GITHUB = "GitHub <noreply@github.com>"


def _split(person: str) -> tuple[str, str]:
    name, _, email = person.partition(" <")
    return name, email.rstrip(">")


def _commit(repo: Path, path: str, author: str, committer: str, message: str) -> None:
    with (repo / path).open("a", encoding="utf-8") as fh:
        fh.write(f"{message.splitlines()[0]}\n")
    subprocess.run(["git", "add", path], cwd=repo, check=True)
    c_name, c_email = _split(committer)
    env = {**os.environ, "GIT_COMMITTER_NAME": c_name, "GIT_COMMITTER_EMAIL": c_email}
    subprocess.run(
        ["git", "commit", "-q", f"--author={author}", "-m", message],
        cwd=repo,
        check=True,
        env=env,
    )


def _trailer(person: str) -> str:
    return f"\n\nCo-authored-by: {person}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # Agent author, human co-author trailer (the committer is the agent too).
    _commit(tmp_path, "trailer.py", _CLAUDE, _CLAUDE, "add parser" + _trailer(_JANE))
    _commit(tmp_path, "trailer.py", _CLAUDE, _CLAUDE, "fix parser" + _trailer(_JANE))
    _commit(tmp_path, "trailer.py", _DEV, _DEV, "tweak parser")
    # Agent author, human committer, no trailer.
    _commit(tmp_path, "committer.py", _COPILOT, _OMAR, "add cache")
    _commit(tmp_path, "committer.py", _COPILOT, _OMAR, "fix cache")
    # Agent only: no human anywhere (a forge's web committer is not one).
    _commit(tmp_path, "agent.py", _CLAUDE, _CLAUDE, "add tool")
    _commit(tmp_path, "agent.py", _COPILOT, _GITHUB, "fix tool" + _trailer(_BOT))
    # A bot's commits stay out of ownership, whoever commits them.
    _commit(tmp_path, "bot.py", _BOT, _GITHUB, "bump a")
    _commit(tmp_path, "bot.py", _BOT, _GITHUB, "bump b")
    _commit(tmp_path, "bot.py", _DEV, _DEV, "pin a")
    # Humans only: unchanged.
    _commit(tmp_path, "human.py", _DEV, _DEV, "start")
    _commit(tmp_path, "human.py", _JANE, _OMAR, "rebased by Omar")
    _commit(tmp_path, "human.py", _DEV, _DEV, "more")
    # Bus factor: Jane twice by hand, twice through an agent, Dev twice.
    for n in range(2):
        _commit(tmp_path, "shared.py", _JANE, _JANE, f"jane {n}")
        _commit(tmp_path, "shared.py", _CLAUDE, _CLAUDE, f"agent {n}" + _trailer(_JANE))
        _commit(tmp_path, "shared.py", _DEV, _DEV, f"dev {n}")
    return tmp_path


def _index(repo_path: Path, path: str, repo_wide: bool) -> dict:
    repo = git.Repo(repo_path)
    return index_file(
        repo,
        path,
        repo_path=repo_path,
        commit_limit=100,
        follow_renames=False,
        resolve=repo_people(repo) if repo_wide else None,
    )


def _top(meta: dict) -> list[tuple[str, str, int]]:
    return [
        (a["name"], a["email"], a["commit_count"]) for a in json.loads(meta["top_authors_json"])
    ]


@pytest.mark.parametrize("repo_wide", [True, False])
def test_human_co_author_trailer_owns_the_agent_commit(repo: Path, repo_wide: bool) -> None:
    meta = _index(repo, "trailer.py", repo_wide)
    assert _top(meta) == [("Jane Roe", "jane@acme.io", 2), ("Dev Lee", "dev@acme.io", 1)]
    # Blame credits the agent commits' lines to Jane as well.
    assert meta["primary_owner_name"] == "Jane Roe"
    assert meta["primary_owner_line_pct"] == pytest.approx(2 / 3)
    assert meta["primary_owner_commit_pct"] == pytest.approx(2 / 3)
    assert meta["contributor_count"] == 2


@pytest.mark.parametrize("repo_wide", [True, False])
def test_human_committer_owns_the_agent_commit(repo: Path, repo_wide: bool) -> None:
    meta = _index(repo, "committer.py", repo_wide)
    assert _top(meta) == [("Omar Haddad", "omar@acme.io", 2)]
    assert meta["primary_owner_name"] == "Omar Haddad"
    assert meta["recent_owner_name"] == "Omar Haddad"


@pytest.mark.parametrize("repo_wide", [True, False])
def test_agent_only_commits_stay_the_agents(repo: Path, repo_wide: bool) -> None:
    meta = _index(repo, "agent.py", repo_wide)
    names = [name for name, _email, _n in _top(meta)]
    assert sorted(names) == ["Claude", "Copilot"]
    assert meta["primary_owner_name"] in {"Claude", "Copilot"}
    # Shown as an agent, never as a person.
    resolve = repo_people(git.Repo(repo))
    for a in json.loads(meta["top_authors_json"]):
        assert resolve.kind(resolve(a["name"], a["email"])) == "agent"


@pytest.mark.parametrize("repo_wide", [True, False])
def test_bot_commits_stay_excluded(repo: Path, repo_wide: bool) -> None:
    meta = _index(repo, "bot.py", repo_wide)
    assert _top(meta) == [("Dev Lee", "dev@acme.io", 1)]
    assert meta["contributor_count"] == 1
    assert meta["commit_count_total"] == 3


@pytest.mark.parametrize("repo_wide", [True, False])
def test_human_commits_are_unchanged(repo: Path, repo_wide: bool) -> None:
    meta = _index(repo, "human.py", repo_wide)
    # A human author keeps the commit even when someone else committed it.
    assert _top(meta) == [("Dev Lee", "dev@acme.io", 2), ("Jane Roe", "jane@acme.io", 1)]
    assert meta["primary_owner_name"] == "Dev Lee"


def test_bus_factor_and_owner_counts_before_and_after(repo: Path) -> None:
    from repowise.core.ingestion.git_commit_index import load_commit_index

    commits = load_commit_index(git.Repo(repo), 100, {"shared.py"})["shared.py"]
    after: dict = {}
    _add_ownership(after, _Authors.tally(commits, 0))
    # Without the credit (the old rule) the agent is a third contributor.
    before: dict = {}
    uncredited = [dataclasses.replace(c, credit=None) for c in commits]
    _add_ownership(before, _Authors.tally(uncredited, 0))

    assert (before["contributor_count"], before["bus_factor"]) == (3, 3)
    assert before["primary_owner_commit_pct"] == pytest.approx(2 / 6)
    assert (after["contributor_count"], after["bus_factor"]) == (2, 2)
    assert after["primary_owner_name"] == "Jane Roe"
    assert after["primary_owner_commit_pct"] == pytest.approx(4 / 6)
    assert _top(after) == [("Jane Roe", "jane@acme.io", 4), ("Dev Lee", "dev@acme.io", 2)]


@pytest.mark.parametrize(
    ("author", "committer", "body", "expected"),
    [
        # Human or bot authors are never re-credited.
        (_JANE, _OMAR, _trailer(_DEV), None),
        (_BOT, _OMAR, _trailer(_DEV), None),
        # The first human trailer wins over the committer and later trailers.
        (
            _CLAUDE,
            _OMAR,
            _trailer(_BOT) + "\nCo-authored-by: " + _JANE + "\n" + _trailer(_DEV),
            ("Jane Roe", "jane@acme.io"),
        ),
        # Trailer keys are case-insensitive.
        (_CLAUDE, _CLAUDE, "\n\nco-authored-by: " + _JANE, ("Jane Roe", "jane@acme.io")),
        # An agent co-author is no human signal; the committer is.
        (_CLAUDE, _OMAR, _trailer(_COPILOT), ("Omar Haddad", "omar@acme.io")),
        # A bot or forge committer is no human signal.
        (_COPILOT, _GITHUB, "", None),
        (_COPILOT, _COPILOT, "", None),
    ],
)
def test_accountable_human(
    author: str, committer: str, body: str, expected: tuple[str, str] | None
) -> None:
    assert accountable_human(*_split(author), *_split(committer), body) == expected
