"""The scratch commit-graph speeds up blame without changing what it reports."""

from __future__ import annotations

import os
from pathlib import Path

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer import indexer as indexer_mod
from repowise.core.ingestion.git_indexer.blame_graph import BlameGraph


def _build_repo(path: Path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    lines: list[str] = []
    for i in range(8):
        lines.append(f"v{i} = {i}")
        (path / "a.py").write_text("\n".join(lines) + "\n")
        (path / "b.py").write_text(f"w = {i}\n")
        repo.index.add(["a.py", "b.py"])
        repo.index.commit(f"feat: step {i}")
    repo.close()


def _repo(path: Path):
    from repowise.core.git_repo import Repo

    return Repo(path)


def _build(repo) -> BlameGraph:
    target = BlameGraph.target(repo)
    return BlameGraph.write(*target) if target else BlameGraph()


def test_graph_leaves_blame_output_unchanged(tmp_path: Path) -> None:
    _build_repo(tmp_path)
    repo = _repo(tmp_path)
    try:
        graph = _build(repo)
        assert graph.env is not None
        scratch = Path(graph.env["GIT_OBJECT_DIRECTORY"])
        assert (scratch / "info" / "commit-graph").is_file()
        plain = repo.git.blame("--line-porcelain", "HEAD", "--", "a.py")
        with_graph = repo.git.blame("--line-porcelain", "HEAD", "--", "a.py", env=graph.env)
        assert with_graph == plain
        graph.close()
        assert not scratch.exists()
    finally:
        repo.close()


def test_a_dropped_graph_still_removes_its_scratch(tmp_path: Path) -> None:
    """A cancelled or failed run never calls close; the scratch must not outlive it."""
    import gc

    _build_repo(tmp_path)
    repo = _repo(tmp_path)
    try:
        graph = _build(repo)
    finally:
        repo.close()
    scratch = Path(graph.env["GIT_OBJECT_DIRECTORY"])
    assert scratch.is_dir()
    del graph
    gc.collect()
    assert not scratch.exists()


def test_graph_never_writes_into_the_repository(tmp_path: Path) -> None:
    _build_repo(tmp_path)
    info = tmp_path / ".git" / "objects" / "info"
    before = sorted(p.name for p in info.iterdir()) if info.is_dir() else []
    repo = _repo(tmp_path)
    try:
        _build(repo).close()
    finally:
        repo.close()
    after = sorted(p.name for p in info.iterdir()) if info.is_dir() else []
    assert after == before


def test_graph_steps_aside_for_a_caller_object_directory(tmp_path, monkeypatch) -> None:
    _build_repo(tmp_path)
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / ".git" / "objects"))
    repo = _repo(tmp_path)
    try:
        assert _build(repo).env is None
    finally:
        repo.close()


def test_graph_skips_a_failed_write(tmp_path, monkeypatch) -> None:
    """A write git refuses leaves the pass on plain blame and no scratch behind."""
    import subprocess

    from repowise.core.ingestion.git_indexer import blame_graph

    _build_repo(tmp_path)
    scratches: list[str] = []

    def _refuse(args, cwd=None, **kwargs):
        scratches.append(kwargs["env"]["GIT_OBJECT_DIRECTORY"])
        return subprocess.CompletedProcess(args, 128, "", "fatal: refused")

    monkeypatch.setattr(blame_graph, "run_git", _refuse)
    repo = _repo(tmp_path)
    try:
        assert _build(repo).env is None
    finally:
        repo.close()
    assert scratches and not os.path.exists(scratches[0])


async def test_full_index_rows_match_without_the_graph(tmp_path, monkeypatch) -> None:
    _build_repo(tmp_path)
    built: list[BlameGraph] = []
    real_write = BlameGraph.write

    def _record(*args):
        graph = real_write(*args)
        built.append(graph)
        return graph

    monkeypatch.setattr(BlameGraph, "write", staticmethod(_record))
    _summary, with_graph = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")
    assert built and built[0].env is not None
    assert not os.path.exists(built[0].env["GIT_OBJECT_DIRECTORY"])

    monkeypatch.setattr(indexer_mod.GitIndexer, "_start_blame_graph", lambda *_: None)
    _summary, without = await GitIndexer(tmp_path, tier=GitIndexTier.FULL).index_repo("r")

    def _comparable(rows: list[dict]) -> dict[str, dict]:
        out = {}
        for row in rows:
            row = dict(row)
            blame = row.pop("blame_index", None)
            row["blame_lines"] = sorted(blame.lines.items()) if blame else None
            out[row["file_path"]] = row
        return out

    assert _comparable(with_graph) == _comparable(without)
    assert _comparable(with_graph)["a.py"]["blame_lines"]
