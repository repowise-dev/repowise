"""Git rows for an index of a subdirectory cover that subdirectory only.

Indexing one module of a large repository used to write a git row for every
tracked file of the whole repository, plus a commit-file row per file of every
bulk commit. For a 22-file module of a 40,000-file repository that was about
35 MiB of a 64 MiB database, for files nothing could look up.
"""

from __future__ import annotations

from pathlib import Path

import git as gitpython
import pytest

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.commit_rows import build_commit_file_rows

MODULE = "svc/module"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """One bulk commit over the module and a sibling tree, then a module-only edit."""
    r = gitpython.Repo.init(tmp_path)
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / MODULE).mkdir(parents=True)
    (tmp_path / "other").mkdir()
    names = [f"{MODULE}/a.py", f"{MODULE}/b.py"]
    names += [f"other/o{i}.py" for i in range(6)]
    for n in names:
        (tmp_path / n).write_text("x = 1\n", encoding="utf-8")
    r.index.add(names)
    r.index.commit("bulk import")
    (tmp_path / MODULE / "a.py").write_text("x = 2\n", encoding="utf-8")
    r.index.add([f"{MODULE}/a.py"])
    r.index.commit("edit module")
    r.close()
    return tmp_path


def _paths(rows: list[dict]) -> set[str]:
    return {r["file_path"] for r in rows}


async def test_a_module_index_records_git_rows_for_the_module_only(repo: Path) -> None:
    summary, meta = await GitIndexer(repo / MODULE).index_repo("module")

    assert _paths(meta) == {f"{MODULE}/a.py", f"{MODULE}/b.py"}
    assert _paths(summary.commit_file_rows) == {f"{MODULE}/a.py", f"{MODULE}/b.py"}


async def test_commit_rows_still_describe_the_whole_commit(repo: Path) -> None:
    summary, _ = await GitIndexer(repo / MODULE).index_repo("module")

    assert len(summary.commit_rows) == 2


async def test_the_repo_scope_switch_restores_whole_repository_rows(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOWISE_GIT_SCOPE", "repo")

    _, meta = await GitIndexer(repo / MODULE).index_repo("module")

    assert len(_paths(meta)) == 8


async def test_indexing_the_repo_root_is_unchanged(repo: Path) -> None:
    _, meta = await GitIndexer(repo).index_repo("repo")

    assert len(_paths(meta)) == 8


async def test_an_update_maps_module_paths_back_to_the_repo_key(repo: Path) -> None:
    meta = await GitIndexer(repo / MODULE).index_changed_files(["a.py"])

    assert [m["file_path"] for m in meta] == [f"{MODULE}/a.py"]
    # History was found: the path handed to git is the root-relative one.
    assert meta[0]["commit_count_total"] == 2


def test_file_rows_can_be_narrowed_to_a_prefix() -> None:
    commits = [
        {
            "sha": "a" * 40,
            "changes": [(f"{MODULE}/a.py", 1, 0), ("other/x.py", 2, 0), (None, 0, 0)],
        }
    ]

    assert _paths(build_commit_file_rows(commits, MODULE)) == {f"{MODULE}/a.py"}
    assert len(build_commit_file_rows(commits)) == 2
