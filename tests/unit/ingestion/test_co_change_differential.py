"""The co-change walk against brute-force pair counting, commit by commit.

The brute force asks git about one commit at a time (``git show --name-status
-M``), files each path under its name at HEAD, and counts every pair and every
file's commits by hand. The walk must agree on every pair's support, both
files' commit totals and the last shared date, with and without the repo's
``diff.renames`` turned off.
"""

from __future__ import annotations

import os
import random
import subprocess
from collections import Counter
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

import pytest

from repowise.core.ingestion.git_indexer._constants import _MIN_CO_CHANGE_SUPPORT
from repowise.core.ingestion.git_indexer.co_change import compute_co_changes_and_entropy

_FILES = [f"pkg/m{i}.py" for i in range(7)]


def _git(root: Path, *args: str, date: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@example.com",
        "GIT_COMMITTER_NAME": "A",
        "GIT_COMMITTER_EMAIL": "a@example.com",
        "HOME": str(root),
        "GIT_CONFIG_GLOBAL": os.devnull,
    }
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True, capture_output=True, text=True
    ).stdout


def _build(root: Path, renames_off: bool) -> None:
    rng = random.Random(7)
    _git(root, "init", "-q")
    if renames_off:
        _git(root, "config", "diff.renames", "false")
    live = list(_FILES)
    body = {p: "".join(f"line {p} {k}\n" for k in range(30)) for p in live}
    for n in range(40):
        if n == 25:
            # A pure move of one file, and a move-with-edit of another.
            (root / "lib").mkdir(exist_ok=True)
            _git(root, "mv", "pkg/m0.py", "lib/m0.py")
            _git(root, "mv", "pkg/m1.py", "lib/m1.py")
            body["lib/m0.py"] = body.pop("pkg/m0.py")
            body["lib/m1.py"] = body.pop("pkg/m1.py") + "edited\n"
            (root / "lib/m1.py").write_text(body["lib/m1.py"])
            live = sorted(body)
            touched = ["lib/m0.py", "lib/m1.py"]
        else:
            touched = rng.sample(live, rng.choice([1, 2, 2, 3, 4]))
            for path in touched:
                body[path] += f"rev {n}\n"
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(body[path])
        _git(root, "add", "-A")
        _git(
            root, "commit", "-q", "-m", f"c{n}", date=f"2024-01-{1 + n % 28:02d}T{n % 24:02d}:00:00"
        )


def _brute(root: Path, limit: int) -> tuple[Counter, Counter, dict]:
    shas = _git(root, "rev-list", "--no-merges", f"-{limit}", "HEAD").split()
    trail: dict[str, str] = {}
    file_commits: Counter = Counter()
    support: Counter = Counter()
    last: dict = {}
    tracked = set(_git(root, "ls-files").split())
    for sha in shas:
        ts = int(_git(root, "show", "-s", "--format=%ct", sha).strip())
        rows = _git(root, "show", "-M", "--name-status", "--format=", sha).splitlines()
        paths, moves = set(), []
        for row in rows:
            cols = row.split("\t")
            if not row.strip():
                continue
            path = cols[-1]
            if cols[0].startswith("R"):
                moves.append((cols[1], cols[2]))
            head = trail.get(path, path)
            if head in tracked:
                paths.add(head)
        for old, new in moves:
            trail[old] = trail.get(new, new)
        for path in paths:
            file_commits[path] += 1
        for a, b in combinations(sorted(paths), 2):
            support[(a, b)] += 1
            last[(a, b)] = max(last.get((a, b), 0), ts)
    return file_commits, support, last


@pytest.mark.parametrize("renames_off", [False, True])
def test_walk_matches_brute_force_pair_counts(tmp_path, renames_off) -> None:
    import git as gitpython

    _build(tmp_path, renames_off)
    tracked = set(_git(tmp_path, "ls-files").split())
    limit = 30  # shorter than the history, so the depth cap is exercised too

    repo = gitpython.Repo(tmp_path)
    try:
        walk = compute_co_changes_and_entropy(repo, tracked, limit, max_partners=100)
    finally:
        repo.close()
    file_commits, support, last = _brute(tmp_path, limit)

    expected = {pair for pair, n in support.items() if n >= _MIN_CO_CHANGE_SUPPORT}
    got: dict[tuple[str, str], dict] = {}
    for owner, records in walk.partners.items():
        for rec in records:
            got[tuple(sorted((owner, rec["file_path"])))] = rec | {"owner": owner}
    assert set(got) == expected
    assert expected, "fixture should produce pairs above support"
    for pair, rec in got.items():
        assert rec["frequency"] == support[pair]
        other = rec["file_path"]
        assert rec["self_commits"] == file_commits[rec["owner"]]
        assert rec["partner_commits"] == file_commits[other]
        day = datetime.fromtimestamp(last[pair], tz=UTC).strftime("%Y-%m-%d")
        assert rec["last_co_change"] == day
    # The moved files kept their pre-move history.
    assert file_commits["lib/m0.py"] > 1
