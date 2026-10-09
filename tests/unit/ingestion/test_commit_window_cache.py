"""The commit-window cache answers exactly what a fresh walk answers.

``git log --numstat`` over the window costs seconds per update on a repository
with wide commits, whatever the number of new commits. The cache keeps the
last walk's records and asks git only for the commits since. These pin that
the cached answer is the fresh one, in every shape of history the cache has
to survive.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.git_commit_index import _WINDOW_CACHE_NAME, load_commit_index


def _init(tmp_path: Path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    return repo


def _commit(repo, path: str, text: str, message: str, ts: int) -> str:
    root = Path(repo.working_dir)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")
    repo.index.add([path])
    stamp = f"{ts} +0000"
    return repo.index.commit(message, author_date=stamp, commit_date=stamp).hexsha


def _flat(index) -> dict[str, list[tuple]]:
    return {
        path: [(r.sha, r.ts, r.added, r.deleted, r.subject) for r in recs]
        for path, recs in index.items()
    }


def _walk(repo, files: set[str], cache_dir: Path | None, depth: int = 500):
    return _flat(load_commit_index(repo, depth, files, cache_dir=cache_dir))


def test_cache_appends_only_the_new_commits_and_matches_a_fresh_walk(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    files = {"a.py", "b.py"}
    base = 1_700_000_000
    _commit(repo, "a.py", "x = 1\n", "c0", base)
    _commit(repo, "b.py", "y = 1\n", "c1", base + 10)

    first = _walk(repo, files, cache_dir)
    assert first == _walk(repo, files, None)
    cached = json.loads((cache_dir / _WINDOW_CACHE_NAME).read_text(encoding="utf-8"))
    assert cached["head"] == repo.head.commit.hexsha
    assert len(cached["records"]) == 2

    _commit(repo, "a.py", "x = 2\n", "c2", base + 20)
    _commit(repo, "b.py", "y = 2\n", "c3", base + 30)

    assert _walk(repo, files, cache_dir) == _walk(repo, files, None)
    cached = json.loads((cache_dir / _WINDOW_CACHE_NAME).read_text(encoding="utf-8"))
    assert cached["head"] == repo.head.commit.hexsha
    assert len(cached["records"]) == 4


def test_cache_is_cut_to_the_depth_like_the_walk(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    base = 1_700_000_000
    for i in range(3):
        _commit(repo, "a.py", f"x = {i}\n", f"c{i}", base + i * 10)
    _walk(repo, {"a.py"}, cache_dir, depth=3)
    for i in range(3, 6):
        _commit(repo, "a.py", f"x = {i}\n", f"c{i}", base + i * 10)

    assert _walk(repo, {"a.py"}, cache_dir, depth=3) == _walk(repo, {"a.py"}, None, depth=3)
    cached = json.loads((cache_dir / _WINDOW_CACHE_NAME).read_text(encoding="utf-8"))
    assert len(cached["records"]) == 3


def test_a_merged_branch_with_older_commits_lands_where_the_walk_puts_it(tmp_path: Path) -> None:
    """New commits are not always newer: a merged branch carries its own dates."""
    repo = _init(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    files = {"a.py", "b.py"}
    base = 1_700_000_000
    _commit(repo, "a.py", "x = 1\n", "c0", base)
    main_head = repo.head.commit.hexsha
    _walk(repo, files, cache_dir)

    repo.git.checkout("-b", "side")
    _commit(repo, "b.py", "y = 1\n", "side older", base + 5)
    repo.git.checkout(repo.head.reference.name and "master" if "master" in repo.heads else "main")
    _commit(repo, "a.py", "x = 2\n", "main newer", base + 50)
    repo.git.merge("side", "--no-ff", "-m", "merge side")
    assert repo.head.commit.hexsha != main_head

    assert _walk(repo, files, cache_dir) == _walk(repo, files, None)


def test_a_rewritten_history_falls_back_to_the_full_walk(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    base = 1_700_000_000
    _commit(repo, "a.py", "x = 1\n", "c0", base)
    _commit(repo, "a.py", "x = 2\n", "c1", base + 10)
    _walk(repo, {"a.py"}, cache_dir)

    repo.git.reset("--hard", "HEAD~1")
    _commit(repo, "a.py", "x = 3\n", "c1 rewritten", base + 20)

    assert _walk(repo, {"a.py"}, cache_dir) == _walk(repo, {"a.py"}, None)


def test_a_cache_of_another_depth_or_version_is_ignored(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    base = 1_700_000_000
    _commit(repo, "a.py", "x = 1\n", "c0", base)
    _walk(repo, {"a.py"}, cache_dir, depth=5)

    stale = json.loads((cache_dir / _WINDOW_CACHE_NAME).read_text(encoding="utf-8"))
    stale["records"] = ["garbage"]
    (cache_dir / _WINDOW_CACHE_NAME).write_text(json.dumps(stale), encoding="utf-8")
    # Same depth, same head: the cache is trusted, so garbage yields nothing.
    assert _walk(repo, {"a.py"}, cache_dir, depth=5) == {}
    # Another depth: the cache is ignored and the walk answers.
    assert _walk(repo, {"a.py"}, cache_dir, depth=7) == _walk(repo, {"a.py"}, None, depth=7)
    stale["version"] = 0
    stale["depth"] = 7
    (cache_dir / _WINDOW_CACHE_NAME).write_text(json.dumps(stale), encoding="utf-8")
    assert _walk(repo, {"a.py"}, cache_dir, depth=7) == _walk(repo, {"a.py"}, None, depth=7)


def test_no_cache_dir_writes_nothing(tmp_path: Path) -> None:
    repo = _init(tmp_path)
    _commit(repo, "a.py", "x = 1\n", "c0", 1_700_000_000)
    _walk(repo, {"a.py"}, None)
    assert not list(tmp_path.glob("**/" + _WINDOW_CACHE_NAME))
