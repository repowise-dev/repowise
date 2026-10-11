"""The primary owner's line share and commit share are two different numbers.

With blame, the top author of current lines becomes the primary owner. That
person need not be the top committer, so their share of lines must not be
reported as a share of commits.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import git as gitpython

from repowise.core.ingestion.git_indexer.file_history import index_file


def _init(cwd: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Setup"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.email", "setup@example.com"], cwd=cwd, check=True)


def _commit(cwd: Path, author: str, content: str, message: str) -> None:
    (cwd / "mod.py").write_text(content)
    subprocess.run(["git", "add", "mod.py"], cwd=cwd, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message, f"--author={author} <{author.lower()}@example.com>"],
        cwd=cwd,
        check=True,
    )


def test_top_blame_author_differs_from_top_commit_author(tmp_path: Path) -> None:
    _init(tmp_path)
    lines = [f"v{i} = {i}\n" for i in range(10)]
    # Ada writes all ten lines in one commit; Bob then edits one line three times.
    _commit(tmp_path, "Ada", "".join(lines), "feat: add mod")
    for n in range(3):
        lines[n] = f"v{n} = {n + 100}\n"
        _commit(tmp_path, "Bob", "".join(lines), f"fix: tweak v{n}")

    meta = index_file(
        gitpython.Repo(tmp_path),
        "mod.py",
        repo_path=tmp_path,
        commit_limit=100,
        follow_renames=False,
        include_blame=True,
    )

    # Bob made 3 of the 4 commits, but Ada still wrote 7 of the 10 lines.
    assert meta["primary_owner_name"] == "Ada"
    assert meta["primary_owner_line_pct"] == 0.7
    assert meta["primary_owner_commit_pct"] == 0.25
    top_committer = max(json.loads(meta["top_authors_json"]), key=lambda a: a["commit_count"])
    assert top_committer["name"] == "Bob"


def test_without_blame_the_owner_and_share_come_from_commits(tmp_path: Path) -> None:
    _init(tmp_path)
    _commit(tmp_path, "Ada", "a = 1\nb = 2\n", "feat: add mod")
    _commit(tmp_path, "Bob", "a = 1\nb = 3\n", "fix: b")
    _commit(tmp_path, "Bob", "a = 1\nb = 4\n", "fix: b again")

    meta = index_file(
        gitpython.Repo(tmp_path),
        "mod.py",
        repo_path=tmp_path,
        commit_limit=100,
        follow_renames=False,
        include_blame=False,
    )

    assert meta["primary_owner_name"] == "Bob"
    assert meta["primary_owner_commit_pct"] == 2 / 3
    assert meta["primary_owner_line_pct"] is None
