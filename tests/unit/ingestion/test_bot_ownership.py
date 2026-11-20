"""Automation accounts are not owners or contributors; coding agents still are.

``bot`` identities (dependabot, renovate, CI) are left out of the primary owner,
top authors, contributor count, bus factor and 90-day owner. An ``agent`` (a
coding agent's own identity) is kept exactly as before. Commit counts and the
hotspot score still count every commit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import git as gitpython

from repowise.core.ingestion.git_indexer.file_history import index_file

_BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
_DEV = "Dev <dev@acme.io>"
_AGENT = "copilot-swe-agent[bot] <198982749+Copilot@users.noreply.github.com>"


def _init(cwd: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)


def _commit(cwd: Path, author: str, n: int) -> None:
    (cwd / "a.py").write_text(f"x = {n}\n")
    subprocess.run(["git", "add", "a.py"], cwd=cwd, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Committer",
            "-c",
            "user.email=c@acme.io",
            "commit",
            "-q",
            "-m",
            f"change {n}",
            f"--author={author}",
        ],
        cwd=cwd,
        check=True,
    )


def _index(cwd: Path, authors: list[str]) -> dict:
    _init(cwd)
    for n, author in enumerate(authors):
        _commit(cwd, author, n)
    return index_file(
        gitpython.Repo(cwd),
        "a.py",
        repo_path=cwd,
        commit_limit=100,
        follow_renames=False,
        include_blame=False,
    )


def test_bot_majority_file_is_owned_by_the_human(tmp_path: Path) -> None:
    meta = _index(tmp_path, [_DEV, _BOT, _BOT, _BOT])

    assert meta["primary_owner_name"] == "Dev"
    assert meta["primary_owner_email"] == "dev@acme.io"
    assert meta["contributor_count"] == 1
    assert meta["bus_factor"] == 1
    assert [a["name"] for a in json.loads(meta["top_authors_json"])] == ["Dev"]
    assert meta["recent_owner_name"] == "Dev"
    assert meta["recent_owner_commit_pct"] == 0.25
    # Bot commits still count toward the file's history and churn.
    assert meta["commit_count_total"] == 4
    assert meta["primary_owner_commit_pct"] == 0.25
    assert meta["temporal_hotspot_score"] > 0


def test_bot_only_file_has_no_owner(tmp_path: Path) -> None:
    meta = _index(tmp_path, [_BOT, _BOT])

    assert meta["primary_owner_name"] is None
    assert meta["primary_owner_email"] is None
    assert meta["recent_owner_name"] is None
    assert meta["contributor_count"] == 0
    assert json.loads(meta["top_authors_json"]) == []
    assert meta["commit_count_total"] == 2
    assert meta["temporal_hotspot_score"] > 0


def test_agent_majority_file_keeps_the_agent_as_owner(tmp_path: Path) -> None:
    meta = _index(tmp_path, [_DEV, _AGENT, _AGENT, _AGENT])

    assert meta["primary_owner_name"] == "copilot-swe-agent[bot]"
    assert meta["contributor_count"] == 2
    assert [a["commit_count"] for a in json.loads(meta["top_authors_json"])] == [3, 1]
