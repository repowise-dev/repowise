"""A shallow clone's boundary commit must not swallow earlier lines' authors (#3055).

``git blame`` marks the oldest commit it can see on a line's history as
``boundary``. On a full clone that is the true root commit; on a shallow
clone it is wherever the fetch was cut off, and every line written before the
cutoff reads as authored by whoever happens to own the boundary commit.
Ownership must exclude boundary lines on a shallow clone, and must NOT
exclude them on a full clone, where the boundary commit's authorship is real.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repowise.core.ingestion.git_indexer.file_history import index_file
from repowise.core.ingestion.git_indexer.function_blame import (
    BlameIndex,
    _parse_porcelain,
    owner_in_range,
    ownership_from_blame,
)

_PORCELAIN_WITH_BOUNDARY = (
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
    "author Boundary\n"
    "author-time 1700000000\n"
    "boundary\n"
    "summary first\n"
    "filename foo.py\n"
    "\tdef alpha():\n"
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2 2 1\n"
    "author Carol\n"
    "author-time 1750000000\n"
    "summary second\n"
    "filename foo.py\n"
    "\t    return 1\n"
)


def test_parse_porcelain_captures_the_boundary_sha():
    lines, authors, boundary_shas = _parse_porcelain(_PORCELAIN_WITH_BOUNDARY)
    assert boundary_shas == {"a" * 40}
    assert set(lines.keys()) == {1, 2}
    assert authors["a" * 40][0] == "Boundary"


def test_ownership_from_blame_excludes_boundary_lines_when_shallow():
    lines, authors, boundary_shas = _parse_porcelain(_PORCELAIN_WITH_BOUNDARY)
    idx = BlameIndex(lines=lines, authors=authors, boundary_shas=boundary_shas, is_shallow=True)
    name, _email, share = ownership_from_blame(idx)
    assert name == "Carol"
    assert share == 1.0


def test_ownership_from_blame_keeps_boundary_lines_on_a_full_clone():
    """Same data, ``is_shallow=False``: the boundary commit is the true root,
    and its author is a real ownership signal, not an artifact to drop."""
    lines, authors, boundary_shas = _parse_porcelain(_PORCELAIN_WITH_BOUNDARY)
    idx = BlameIndex(lines=lines, authors=authors, boundary_shas=boundary_shas, is_shallow=False)
    name, _email, share = ownership_from_blame(idx)
    assert name in ("Boundary", "Carol")  # tie, either is a valid top author
    assert share == 0.5


def test_ownership_from_blame_falls_back_to_none_when_every_line_is_boundary():
    """All blamed lines are boundary shas on a shallow clone: no attributable
    signal, so the caller falls back to the commit-based owner."""
    lines, authors, boundary_shas = _parse_porcelain(_PORCELAIN_WITH_BOUNDARY)
    only_boundary = {k: v for k, v in lines.items() if v[0] == "a" * 40}
    idx = BlameIndex(
        lines=only_boundary, authors=authors, boundary_shas=boundary_shas, is_shallow=True
    )
    assert ownership_from_blame(idx) == (None, None, None)


def test_owner_in_range_excludes_boundary_lines_when_shallow():
    lines, authors, boundary_shas = _parse_porcelain(_PORCELAIN_WITH_BOUNDARY)
    idx = BlameIndex(lines=lines, authors=authors, boundary_shas=boundary_shas, is_shallow=True)
    assert owner_in_range(idx, 1, 2) == ("Carol", None, 1.0)


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str, email: str, when: str, message: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": when,
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env
    )


def test_shallow_clone_blame_ownership_matches_the_full_clone(tmp_path: Path) -> None:
    """The issue's own repro, shrunk: a shallow clone must not hand ownership
    of Alice's lines to Carol, the boundary commit's author.

    Depth 2 keeps only the last two commits (Carol's, then Bob's); Alice's
    three commits fall outside the fetch, so every line she wrote is blamed
    to Carol's commit, the new boundary, unless boundary lines are excluded.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)

    app = origin / "app.py"
    app.write_text("")
    for i in range(1, 4):
        with app.open("a") as f:
            for j in range(1, 11):
                f.write(f"def f_{i}_{j}(): return {i}{j}\n")
        _git(["add", "app.py"], origin)
        _commit(origin, "Alice", "alice@example.com", f"2026-0{i}-01T12:00:00Z", f"feat: alice part {i}")

    with app.open("a") as f:
        f.write("def carol_helper(): return 'carol'\n")
    _git(["add", "app.py"], origin)
    _commit(origin, "Carol", "carol@example.com", "2026-06-15T12:00:00Z", "feat: carol helper")

    with app.open("a") as f:
        f.write("def bob_helper(): return 'bob'\n")
    _git(["add", "app.py"], origin)
    _commit(origin, "Bob", "bob@example.com", "2026-07-01T12:00:00Z", "feat: bob helper")

    full = tmp_path / "full"
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", str(origin), str(full)], check=True)
    subprocess.run(["git", "clone", "-q", "--depth", "2", str(origin), str(shallow)], check=True)

    import git as gitpython

    full_meta = index_file(
        gitpython.Repo(full), "app.py", repo_path=full, commit_limit=500,
        follow_renames=False, include_blame=True,
    )
    shallow_meta = index_file(
        gitpython.Repo(shallow), "app.py", repo_path=shallow, commit_limit=500,
        follow_renames=False, include_blame=True,
    )

    assert full_meta["primary_owner_name"] == "Alice"
    assert full_meta["primary_owner_line_pct"] > 0.9
    # Before this fix, the shallow clone named Carol the owner with a
    # near-100% line share (0.989 in the issue's own repro) — the boundary
    # commit's author credited with 90 lines she never wrote.
    assert shallow_meta["primary_owner_name"] != "Carol"
