"""Dead-code git-age checks measure from the indexed commit, not the wall clock."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code import analyzer as analyzer_mod
from repowise.core.ingestion.git_indexer import file_history
from tests.unit.dead_code._helpers import _build_graph

_HEAD_DATE = "2020-06-01T00:00:00+00:00"
_CONFIG = {"detect_unused_exports": False, "detect_zombie_packages": False, "min_confidence": 0.0}


def _git_repo(root: Path) -> Path:
    env = {**os.environ, "GIT_AUTHOR_DATE": _HEAD_DATE, "GIT_COMMITTER_DATE": _HEAD_DATE}
    env.pop("REPOWISE_GIT_WINDOW_ANCHOR", None)
    run = {"cwd": root, "env": env, "check": True, "capture_output": True}
    subprocess.run(["git", "init", "-q"], **run)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], **run)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"], **run
    )
    return root


class _Clock(datetime):
    """A datetime whose ``now()`` is set by the test; dates built from it stay datetimes."""

    wall = datetime(2000, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.wall


def _confidence(root: Path | None) -> float:
    graph = _build_graph(nodes={"pkg/stale.py": {"symbol_count": 1, "symbols": []}})
    git_meta = {
        "pkg/stale.py": {
            "commit_count_90d": 0,
            # 152 days before HEAD: the 90-day rung, never the year rung.
            "last_commit_at": _Clock(2020, 1, 1, tzinfo=UTC),
            "age_days": 400,
        }
    }
    report = DeadCodeAnalyzer(graph, git_meta_map=git_meta, repo_root=root).analyze(_CONFIG)
    (finding,) = [f for f in report.findings if f.file_path == "pkg/stale.py"]
    return finding.confidence


def _scores_at(walls: tuple[datetime, ...], root: Path | None, monkeypatch) -> list[float]:
    monkeypatch.setattr(analyzer_mod, "datetime", _Clock)
    monkeypatch.setattr(file_history, "datetime", _Clock)
    seen = []
    for wall in walls:
        monkeypatch.setattr(_Clock, "wall", wall)
        seen.append(_confidence(root))
    return seen


_WALLS = (datetime(2020, 6, 2, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))


def test_same_commit_scores_the_same_on_different_days(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOWISE_GIT_WINDOW_ANCHOR", raising=False)
    root = _git_repo(tmp_path)
    assert _scores_at(_WALLS, root, monkeypatch) == [pytest.approx(0.8), pytest.approx(0.8)]


def test_without_a_repo_the_wall_clock_stands_in(monkeypatch):
    assert _scores_at(_WALLS, None, monkeypatch) == [pytest.approx(0.8), pytest.approx(1.0)]


def test_days_since_reads_naive_datetimes_as_utc():
    anchor = datetime(2020, 6, 1, tzinfo=UTC)
    analyzer = DeadCodeAnalyzer(_build_graph(nodes={}), as_of=anchor)
    assert analyzer.days_since(datetime(2020, 5, 1)) == 31
