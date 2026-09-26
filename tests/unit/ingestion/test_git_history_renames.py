"""A rename keeps a file's history: commits made under its old path count for it."""

from __future__ import annotations

import json

import pytest

from repowise.core.ingestion.git_commit_index import load_sampled_commit_index
from repowise.core.ingestion.git_indexer import GitIndexer, RenameTrail
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier

_BODY = "".join(f"def f{i}(x):\n    return x + {i}\n\n" for i in range(12))


def _repo(root):
    import git as gitpython

    repo = gitpython.Repo.init(root)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    return repo


def _commit(repo, root, files: dict[str, str], msg: str, date: str, author: str = "Alice"):
    # The git CLI rather than gitpython's index, which would not see a staged ``git mv``.
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    repo.git.add("--", *files)
    email = f"{author.lower()}@example.com"
    env = {
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    repo.git.commit("-q", "-m", msg, env=env)
    return repo.head.commit


def _rename(repo, root, old: str, new: str, msg: str, date: str):
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    repo.git.mv(old, new)
    text = (root / new).read_text() + "# moved\n"
    return _commit(repo, root, {new: text}, msg, date)


def _build(root):
    """a.py and c.py change together as Bob, then a.py moves to src/b.py."""
    repo = _repo(root)
    for i in range(3):
        _commit(
            repo,
            root,
            {"a.py": _BODY + f"# rev {i}\n", "c.py": f"y = {i}\n"},
            "fix: null check in a" if i == 1 else f"feat: extend a {i}",
            f"2024-01-0{i + 1}T12:00:00",
            author="Bob",
        )
    _rename(repo, root, "a.py", "src/b.py", "refactor: move a into src", "2024-02-01T12:00:00")
    _commit(repo, root, {"src/b.py": _BODY + "# after\n"}, "feat: tune b", "2024-02-02T12:00:00")
    # A new, unrelated a.py after the move keeps only its own history.
    _commit(repo, root, {"a.py": "z = 1\n"}, "feat: new a", "2024-02-03T12:00:00")
    repo.close()


async def test_renamed_file_keeps_its_pre_rename_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_GIT_WINDOW_ANCHOR", "head")
    _build(tmp_path)

    _s, rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    meta = {row["file_path"]: row for row in rows}

    moved = meta["src/b.py"]
    assert moved["commit_count_total"] == 5
    assert moved["first_commit_at"].date().isoformat() == "2024-01-01"
    authors = {a["name"]: a["commit_count"] for a in json.loads(moved["top_authors_json"])}
    assert authors == {"Bob": 3, "Alice": 2}
    assert moved["prior_defect_raw_count"] == 1
    partners = {p["file_path"] for p in json.loads(moved["co_change_partners_json"])}
    assert "c.py" in partners

    assert meta["a.py"]["commit_count_total"] == 1
    assert meta["a.py"]["prior_defect_raw_count"] == 0


def test_deep_walk_continues_the_window_rename_trail(tmp_path) -> None:
    import git as gitpython

    repo = _repo(tmp_path)
    old = _commit(repo, tmp_path, {"a.py": _BODY}, "feat: add a", "2024-01-01T12:00:00")
    moved = _rename(repo, tmp_path, "a.py", "b.py", "refactor: a to b", "2024-01-02T12:00:00")
    _commit(repo, tmp_path, {"x.py": "x = 1\n"}, "feat: x", "2024-01-03T12:00:00")
    repo.close()

    repo = gitpython.Repo(tmp_path)
    try:
        # A two-commit window holds the rename; the deep walk reaches the add.
        sample = load_sampled_commit_index(
            repo, 2, {"b.py", "x.py"}, deep_limit=10, deep_threshold=1
        )
    finally:
        repo.close()

    assert [c.sha for c in sample.commits["b.py"]] == [moved.hexsha, old.hexsha]
    assert "b.py" not in sample.fallback_files


@pytest.mark.parametrize(
    ("renames", "path", "want"),
    [
        ([("b", "c"), ("a", "b")], "a", "c"),  # a -> b -> c, seen newest first
        ([("a", "b")], "b", "b"),
        ([("b", "a"), ("a", "b")], "a", "a"),  # moved away and back
    ],
)
def test_rename_trail_resolves_chains(renames, path, want) -> None:
    trail = RenameTrail()
    for old, new in renames:
        trail.record(old, new)
    assert trail.resolve(path) == want
