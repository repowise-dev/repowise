"""An update's git index covers the same files a full index does.

``index_repo`` gives code files the full pass and other tracked files only the
history tier. ``index_changed_files`` once applied no allowlist, so a store that
had taken updates held rows a fresh index never writes. Both now split the
tiers the same way: a non-code row is ``history_only`` on either path.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier


def _repo(tmp_path: Path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 1\n")
    (tmp_path / ".github" / "workflows" / "ci.yaml").write_text("on: push\n")
    (tmp_path / "README.md").write_text("# r\n")
    repo.index.add(["a.py", "b.py", ".github/workflows/ci.yaml", "README.md"])
    repo.index.commit("feat: add files")
    for i in range(2, 4):
        (tmp_path / "a.py").write_text(f"x = {i}\n")
        (tmp_path / ".github" / "workflows" / "ci.yaml").write_text(f"on: push # {i}\n")
        repo.index.add(["a.py", ".github/workflows/ci.yaml"])
        repo.index.commit(f"chore: round {i}")
    repo.close()


async def test_changed_files_and_idle_refresh_follow_the_full_index_allowlist(tmp_path):
    _repo(tmp_path)
    all_files = {"a.py", "b.py", ".github/workflows/ci.yaml", "README.md"}
    sink: dict[str, dict] = {}

    rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_changed_files(
        ["a.py", ".github/workflows/ci.yaml"],
        all_files=all_files,
        co_change_sink={},
        idle_decay_sink=sink,
    )

    by_path = {r["file_path"]: r for r in rows}
    assert set(by_path) == {"a.py", ".github/workflows/ci.yaml"}
    assert by_path[".github/workflows/ci.yaml"]["history_only"] is True
    assert by_path["a.py"]["history_only"] is False

    _summary, full_rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    full = {r["file_path"]: r for r in full_rows}
    assert set(by_path) | set(sink) <= set(full)
    for path, row in by_path.items():
        assert row["history_only"] == full[path]["history_only"]
        assert row["commit_count_total"] == full[path]["commit_count_total"]
