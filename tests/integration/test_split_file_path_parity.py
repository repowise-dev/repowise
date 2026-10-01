"""Split File builds the same weighted graph on a full index and on an update.

The shared-foreign-module fallback keys each foreign callee by its graph
community label. A path that hands the analyzer no labels keys by raw file path
instead, so the same file on the same commit gets a sparser graph, a different
partition and a different plan id depending on whether ``init`` or ``update``
wrote it.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.helpers import release_update_lock
from repowise.cli.main import cli
from repowise.core.analysis.change_health.analyzer import RevisionHealthAnalyzer
from repowise.core.analysis.health.refactoring import split_file

_GROUPS = ("ledger", "mailer")
_PER_GROUP = 5


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Split Parity",
        "GIT_AUTHOR_EMAIL": "split-parity@example.invalid",
        "GIT_COMMITTER_NAME": "Split Parity",
        "GIT_COMMITTER_EMAIL": "split-parity@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _helper_module(group: str, i: int) -> str:
    # Chain each package's modules by import so they land in one community.
    head = f"from {group}_pkg.m{i - 1} import step_{i - 1}\n\n\n" if i else ""
    body = f"    return step_{i - 1}(x) + {i}\n" if i else "    return x\n"
    return f"{head}def step_{i}(x):\n{body}"


def _package_init() -> str:
    return "".join(f"from .m{i} import step_{i}\n" for i in range(_PER_GROUP))


def _big_module() -> str:
    # Importing through the package re-export puts the ``imports`` edge on
    # ``__init__.py`` while each call lands in a submodule, so no symbol has an
    # imported-name surface toward its callee's file and the detector falls
    # back to the shared-foreign-module signal.
    out: list[str] = []
    for group in _GROUPS:
        aliases = ", ".join(f"step_{i} as {group}_step_{i}" for i in range(_PER_GROUP))
        out.append(f"from {group}_pkg import {aliases}")
    out.extend(["", ""])
    for group in _GROUPS:
        for i in range(_PER_GROUP):
            out.append(f"def {group}_task_{i}(x):")
            out.append(f"    y = {group}_step_{i}(x)")
            out.extend(f"    y = y * {k} + {i}" for k in range(1, 30))
            out.append("    return y")
            out.append("")
            out.append("")
    return "\n".join(out)


def _make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    for group in _GROUPS:
        pkg = path / f"{group}_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(_package_init(), encoding="utf-8")
        for i in range(_PER_GROUP):
            (pkg / f"m{i}.py").write_text(_helper_module(group, i), encoding="utf-8")
    (path / "big.py").write_text(_big_module(), encoding="utf-8")
    _git(path, "init", "-q")
    _git(path, "add", "big.py", *(f"{group}_pkg" for group in _GROUPS))
    _git(path, "commit", "-q", "-m", "initial")


def _invoke(runner: CliRunner, repo: Path, *args: str) -> None:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _split_plans(repo: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as connection:
        return connection.execute(
            "SELECT file_path, public_id FROM refactoring_suggestions "
            "WHERE refactoring_type = 'split_file' AND status = 'open' ORDER BY file_path"
        ).fetchall()


def test_split_file_graph_and_plan_match_across_init_and_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graphs: list[tuple[tuple[str, str, float], ...]] = []
    original = split_file._weighted_graph
    original_revision = RevisionHealthAnalyzer.analyze
    in_revision = [False]

    def _recording(*args: object, **kwargs: object):
        wg, counts = original(*args, **kwargs)
        # Revision deltas analyze a changed-files-only graph and store no plans.
        if wg is not None and not in_revision[0]:
            edges = sorted((a, b, round(d["weight"], 6)) for a, b, d in wg.edges(data=True))
            graphs.append(tuple(edges))
        return wg, counts

    def _revision(self: RevisionHealthAnalyzer, *args: object, **kwargs: object):
        in_revision[0] = True
        try:
            return original_revision(self, *args, **kwargs)
        finally:
            in_revision[0] = False

    monkeypatch.setattr(split_file, "_weighted_graph", _recording)
    monkeypatch.setattr(RevisionHealthAnalyzer, "analyze", _revision)
    init_args = (
        "init",
        "--no-prose",
        "--embedder",
        "mock",
        "--no-editor-setup",
        "--no-hook",
        "--no-onboarding",
        "--no-seed",
        "--no-agents",
        "--no-codex",
        "--no-claude-md",
        "--no-cost-tracking",
        "--no-workspace",
        "-y",
    )
    runner = CliRunner()

    incremental = tmp_path / "incremental" / "repo"
    _make_repo(incremental)
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *init_args)
    with (incremental / "big.py").open("a", encoding="utf-8") as source:
        source.write("\n# harmless incremental edit\n")
    _git(incremental, "add", "big.py")
    _git(incremental, "commit", "-q", "-m", "harmless edit")
    graphs.clear()
    _invoke(runner, incremental, "update", "--no-workspace", "--no-agents")
    update_graphs = list(graphs)

    full = tmp_path / "full" / "repo"
    subprocess.run(["git", "clone", "-q", str(incremental), str(full)], check=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{full / '.repowise' / 'wiki.db'}")
    graphs.clear()
    _invoke(runner, full, *init_args)
    full_graphs = list(graphs)

    assert update_graphs, "the update never rebuilt big.py's split graph"
    assert full_graphs
    assert set(update_graphs) == set(full_graphs)
    full_plans = _split_plans(full)
    assert full_plans
    assert _split_plans(incremental) == full_plans
