"""Split File builds the same weighted graph on a full index and on an update.

The shared-foreign-module fallback keys each foreign callee by its graph
community label. A path that hands the analyzer no labels keys by raw file path
instead, so the same file on the same commit gets a sparser graph, a different
partition and a different plan id depending on whether ``init`` or ``update``
wrote it.

The co-change signal has the same hazard: its commit sets came from a blame
index only a full index builds, so a re-score from stored git metadata dropped
the edges.
"""

from __future__ import annotations

import json
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
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
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


def _commit_group_history(path: Path, revisions: int = 5) -> None:
    # Each revision touches one line in every function of one group, so a
    # group's functions share their commits and the co-change edge forms.
    big = path / "big.py"
    for rev in range(1, revisions + 1):
        group = _GROUPS[rev % 2]
        lines = big.read_text(encoding="utf-8").split("\n")
        for n, line in enumerate(lines):
            if line.startswith(f"    y = {group}_step_"):
                lines[n] = f"{line.split(')')[0]}) + {rev}"
        big.write_text("\n".join(lines), encoding="utf-8")
        _git(path, "add", "big.py")
        _git(path, "commit", "-q", "-m", f"touch {group} ({rev})")


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


def _record_graphs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[str, str, float], ...]]:
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
    return graphs


_INIT_ARGS = (
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


def _use_store(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{repo / '.repowise' / 'wiki.db'}")


def _init_clone(
    runner: CliRunner, source: Path, full: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "clone", "-q", str(source), str(full)], check=True)
    _use_store(monkeypatch, full)
    _invoke(runner, full, *_INIT_ARGS)


def _cochange_edges(repo: Path) -> list[int]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as connection:
        rows = connection.execute(
            "SELECT evidence_json FROM refactoring_suggestions "
            "WHERE refactoring_type = 'split_file' AND status = 'open'"
        ).fetchall()
    return [json.loads(raw).get("cochange_edges", 0) for (raw,) in rows]


def test_split_file_graph_and_plan_match_across_init_and_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graphs = _record_graphs(monkeypatch)
    runner = CliRunner()

    incremental = tmp_path / "incremental" / "repo"
    _make_repo(incremental)
    _use_store(monkeypatch, incremental)
    _invoke(runner, incremental, *_INIT_ARGS)
    with (incremental / "big.py").open("a", encoding="utf-8") as source:
        source.write("\n# harmless incremental edit\n")
    _git(incremental, "add", "big.py")
    _git(incremental, "commit", "-q", "-m", "harmless edit")
    graphs.clear()
    _invoke(runner, incremental, "update", "--no-workspace", "--no-agents")
    update_graphs = list(graphs)

    full = tmp_path / "full" / "repo"
    graphs.clear()
    _init_clone(runner, incremental, full, monkeypatch)
    full_graphs = list(graphs)

    assert update_graphs, "the update never rebuilt big.py's split graph"
    assert full_graphs
    assert set(update_graphs) == set(full_graphs)
    full_plans = _split_plans(full)
    assert full_plans
    assert _split_plans(incremental) == full_plans


@pytest.mark.parametrize("stored_before_sets", [False, True], ids=["current", "upgraded"])
def test_split_file_cochange_matches_across_init_and_stored_rescore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored_before_sets: bool
) -> None:
    graphs = _record_graphs(monkeypatch)
    runner = CliRunner()

    repo = tmp_path / "rescore" / "repo"
    _make_repo(repo)
    _commit_group_history(repo)
    _use_store(monkeypatch, repo)
    _invoke(runner, repo, *_INIT_ARGS)
    if stored_before_sets:
        # A store written before the commit sets existed: the re-score has to
        # blame the candidate itself, or it mints ids that move again later.
        with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as connection:
            connection.execute("UPDATE git_function_blame SET commit_shas_json = NULL")
            connection.commit()
    # An analyzer change forces the full re-score, which reads git metadata
    # from the store and so has no blame index. big.py itself is untouched.
    state_path = repo / ".repowise" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION - 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8")
    _git(repo, "add", "NOTES.md")
    _git(repo, "commit", "-q", "-m", "notes")
    graphs.clear()
    _invoke(runner, repo, "update", "--no-workspace", "--no-agents")
    rescore_graphs = list(graphs)

    full = tmp_path / "full" / "repo"
    graphs.clear()
    _init_clone(runner, repo, full, monkeypatch)
    full_graphs = list(graphs)

    assert rescore_graphs, "the re-score never rebuilt big.py's split graph"
    assert full_graphs
    # The fixture's shared history must reach the graph, or this proves nothing.
    assert all(count > 0 for count in _cochange_edges(full))
    assert set(rescore_graphs) == set(full_graphs)
    full_plans = _split_plans(full)
    assert full_plans
    assert _split_plans(repo) == full_plans
    assert _cochange_edges(repo) == _cochange_edges(full)
    if stored_before_sets:
        # Written back, so the next re-score reads them instead of blaming again.
        with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as connection:
            filled = connection.execute(
                "SELECT COUNT(*) FROM git_function_blame "
                "WHERE file_path = 'big.py' AND commit_shas_json IS NOT NULL"
            ).fetchone()[0]
        assert filled > 0
