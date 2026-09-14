"""An update's git index covers the same files a full index does.

``index_repo`` skips every path outside the source-extension allowlist.
``index_changed_files`` did not, so a changed workflow file got a row, and the
idle-decay refresh minted a row for every tracked config and markup file; the
health pass then scored them. A store that had taken updates held rows a fresh
index never writes.
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

    assert {r["file_path"] for r in rows} == {"a.py"}
    assert not {p for p in sink if p.endswith((".yaml", ".md"))}

    _summary, full_rows = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    full_paths = {r["file_path"] for r in full_rows}
    assert {r["file_path"] for r in rows} | set(sink) <= full_paths
