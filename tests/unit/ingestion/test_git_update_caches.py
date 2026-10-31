"""Update-time git caches must leave every row exactly as an uncached run writes it.

The deep-walk record cache and the fix-shape cache only skip git work an
earlier run already did. After one new commit, a run with warm caches and a run
with no cache directory must agree on every changed row, every idle decay row
and every co-change partner.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from repowise.core.ingestion import git_commit_index
from repowise.core.ingestion.git_indexer import GitIndexer, prior_defects
from repowise.core.ingestion.git_indexer.file_history import (
    DECAY_REFRESH_KEYS,
    decay_meta,
    index_file,
)
from repowise.core.ingestion.git_indexer.fix_shape_cache import FixShapeCache

_FILES = ("a.py", "b.py", "c.py", "test_a.py", "README.md")


def _commit(repo, root: Path, changes: dict[str, str], message: str) -> None:
    for name, text in changes.items():
        (root / name).write_text(text)
    repo.index.add(list(changes))
    repo.index.commit(message)


def _build_repo(root: Path):
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    _commit(repo, root, {n: f"# {n}\n" for n in _FILES}, "feat: add the modules")
    _commit(repo, root, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, "fix: wrong default in a and b")
    _commit(repo, root, {"test_a.py": "def test(): pass\n"}, "fix: flaky test for a")
    _commit(repo, root, {"README.md": "# docs\nmore\n"}, "fix: typo in the readme")
    _commit(repo, root, {"c.py": "# c.py\n# comment\n"}, "fix: comment in c")
    _commit(repo, root, {"a.py": "x = 2\n", "c.py": "z = 1\n"}, "feat: change a and c together")
    return repo


async def _run(root: Path) -> tuple[list[dict], dict, dict]:
    # A small commit limit, so the deep walk below the window runs too.
    indexer = GitIndexer(root, commit_limit=2)
    co_change: dict = {}
    idle: dict = {}
    rows = await indexer.index_changed_files(
        ["b.py"],
        all_files={"a.py", "b.py", "c.py", "test_a.py"},
        co_change_sink=co_change,
        idle_decay_sink=idle,
    )
    for row in rows:
        row.pop("blame_index", None)
    return rows, idle, co_change


async def test_warm_caches_match_uncached_run_after_one_commit(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    repo = _build_repo(root)
    (root / ".repowise").mkdir()
    await _run(root)  # warms the window, deep and shape caches
    assert (root / ".repowise" / "commit_deep_cache.json").is_file()
    assert (root / ".repowise" / "fix_shape_cache.json").is_file()

    _commit(repo, root, {"b.py": "y = 2\n"}, "fix: off-by-one in b")
    new_sha = repo.head.commit.hexsha
    repo.close()

    diffed: list[str] = []
    real_parse = prior_defects._parse_fix_diffs
    monkeypatch.setattr(
        prior_defects,
        "_parse_fix_diffs",
        lambda r, shas: diffed.extend(shas) or real_parse(r, shas),
    )
    walks: list[tuple[str, ...]] = []
    real_log = git_commit_index._log_records
    monkeypatch.setattr(
        git_commit_index,
        "_log_records",
        lambda r, *revs: walks.append(revs) or real_log(r, *revs),
    )
    cached = await _run(root)
    # Only the new fix is diffed, and the deep region comes from the cache.
    assert diffed == [new_sha]
    assert not any(arg.startswith("--skip=") for revs in walks for arg in revs)

    shutil.rmtree(root / ".repowise")
    uncached = await _run(root)
    assert cached == uncached
    assert cached[1], "the idle refresh produced rows"


def test_decay_meta_matches_index_file_for_decay_fields(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _build_repo(root).close()
    indexer = GitIndexer(root)
    sample = git_commit_index.load_commit_index(indexer._get_repo(), 50, {"a.py", "c.py"})
    as_of = max(c.ts for commits in sample.values() for c in commits)
    for path, commits in sample.items():
        full = index_file(
            None,
            path,
            repo_path=root,
            commit_limit=50,
            follow_renames=False,
            include_blame=False,
            precomputed_commits=commits,
            as_of_ts=as_of,
        )
        fast = decay_meta(path, commits, as_of)
        assert {k: fast[k] for k in DECAY_REFRESH_KEYS} == {k: full[k] for k in DECAY_REFRESH_KEYS}


def test_shape_cache_from_another_classifier_is_discarded(tmp_path) -> None:
    cache = FixShapeCache.load(tmp_path)
    cache.update({"a" * 40: ("code_fix", 5)}, oldest_ts=0)
    cache.save()
    assert FixShapeCache.load(tmp_path).kinds() == {"a" * 40: "code_fix"}

    path = tmp_path / "fix_shape_cache.json"
    path.write_text(path.read_text().replace(cache._fingerprint, "stale"))
    assert FixShapeCache.load(tmp_path).shapes == {}


def _sampled(repo, cache_dir: Path | None) -> dict[str, list[tuple]]:
    sample = git_commit_index.load_sampled_commit_index(
        repo, 3, {"a.py"}, deep_limit=50, deep_threshold=1, cache_dir=cache_dir
    )
    return {p: [(r.sha, r.ts, r.added) for r in recs] for p, recs in sample.commits.items()}


def test_skewed_commit_times_fall_back_to_a_fresh_walk(tmp_path) -> None:
    """A child older than its parent puts git's walk out of time order."""
    import git as gitpython

    root = tmp_path / "repo"
    root.mkdir()
    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    cache_dir = root / ".repowise"
    cache_dir.mkdir()
    for i, ts in enumerate((100, 300, 200, 400)):  # the third commit is skewed
        (root / "a.py").write_text(f"x = {i}\n" * (i + 1))
        repo.index.add(["a.py"])
        stamp = f"{1_700_000_000 + ts} +0000"
        repo.index.commit(f"c{i}", author_date=stamp, commit_date=stamp)
    _sampled(repo, cache_dir)
    stamp = f"{1_700_000_500} +0000"
    (root / "a.py").write_text("y = 9\n")
    repo.index.add(["a.py"])
    repo.index.commit("c4", author_date=stamp, commit_date=stamp)

    assert _sampled(repo, cache_dir) == _sampled(repo, None)
    repo.close()
