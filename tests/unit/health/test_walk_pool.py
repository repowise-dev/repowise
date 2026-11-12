"""The health walk gives the same answer in spawn workers as in threads.

The walk holds the GIL, so a large repo's walks run in processes; that must
change nothing about the report, and a pool that cannot start must fall back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis.health import engine as engine_mod
from repowise.core.ingestion import ASTParser, FileTraverser

_PY = b"""
def branchy(items, flag):
    total = 0
    for item in items:
        if item and flag:
            total += item
        elif item or not flag:
            total -= 1
    return total
"""

_SQL = b"CREATE VIEW v AS SELECT * FROM t;\nSELECT a.x FROM a, b;\n"


def _repo(tmp_path: Path) -> list:
    (tmp_path / "src").mkdir()
    (tmp_path / "db").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "a.py").write_bytes(_PY)
    (tmp_path / "src" / "b.py").write_bytes(_PY.replace(b"branchy", b"other"))
    (tmp_path / "db" / "views.sql").write_bytes(_SQL)
    (tmp_path / "tests" / "q.sql").write_bytes(_SQL)
    parser = ASTParser()
    return [
        parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        for fi in FileTraverser(tmp_path).traverse()
    ]


def _report(tmp_path: Path, parsed: list):
    analyzer = engine_mod.HealthAnalyzer(
        nx.DiGraph(), git_meta_map={}, parsed_files=parsed, repo_root=tmp_path
    )
    report = asyncio.run(analyzer.analyze_async(None))
    metrics = sorted((m.file_path, m.score, m.max_ccn, m.nloc) for m in report.metrics)
    findings = sorted(
        (f.file_path, f.biomarker_type, f.line_start, f.function_name) for f in report.findings
    )
    return metrics, findings


def test_walks_in_spawn_workers_match_walks_in_threads(tmp_path: Path, monkeypatch) -> None:
    parsed = _repo(tmp_path)
    used_pool: list[int] = []
    real_run = engine_mod.run_with_deadline

    def spying_run(fn, items, workers, results, **kwargs):
        used_pool.append(len(items))
        return real_run(fn, items, workers, results, **kwargs)

    monkeypatch.setattr(engine_mod, "run_with_deadline", spying_run)
    monkeypatch.setattr(engine_mod, "_WALK_POOL_MIN_FILES", 10_000)
    in_threads = _report(tmp_path, parsed)
    assert used_pool == []

    monkeypatch.setattr(engine_mod, "_WALK_POOL_MIN_FILES", 1)
    in_pool = _report(tmp_path, parsed)
    assert used_pool == [len(parsed)]
    assert in_pool == in_threads
    # Sanity: the walk produced real signal both ways.
    assert any(f[1] == "sql_cartesian_join" for f in in_pool[1])


def test_a_pool_that_cannot_start_falls_back_to_threads(tmp_path: Path, monkeypatch) -> None:
    parsed = _repo(tmp_path)
    monkeypatch.setattr(engine_mod, "_WALK_POOL_MIN_FILES", 10_000)
    in_threads = _report(tmp_path, parsed)

    def broken_pool(*_args, **_kwargs):
        raise OSError("cannot spawn")

    monkeypatch.setattr(engine_mod, "run_with_deadline", broken_pool)
    monkeypatch.setattr(engine_mod, "_WALK_POOL_MIN_FILES", 1)
    assert _report(tmp_path, parsed) == in_threads


@pytest.mark.parametrize("language", ["python", "sql"])
def test_a_walk_job_is_picklable(language: str) -> None:
    import pickle

    job = engine_mod._WalkJob("x", language, b"", frozenset(), None, True)
    assert pickle.loads(pickle.dumps(job)) == job


def test_a_walk_that_fails_in_a_worker_is_empty_and_never_cached(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.pipeline.parse_pool import ParseWorkerCrashError

    parsed = _repo(tmp_path)

    def crashing_pool(_fn, items, _workers, results, *, on_done, **_kwargs):
        for j in range(len(items)):
            results[j] = ParseWorkerCrashError("parse worker crashed")
            on_done(j, results[j])

    monkeypatch.setattr(engine_mod, "run_with_deadline", crashing_pool)
    monkeypatch.setattr(engine_mod, "_WALK_POOL_MIN_FILES", 1)
    (tmp_path / ".repowise").mkdir()
    analyzer = engine_mod.HealthAnalyzer(
        nx.DiGraph(),
        git_meta_map={},
        parsed_files=parsed,
        duplication_cache_dir=tmp_path / ".repowise",
        repo_root=tmp_path,
    )
    report = asyncio.run(analyzer.analyze_async(None))
    assert not any(f.biomarker_type.startswith("sql_") for f in report.findings)
    assert analyzer._walk_cache.misses == len(parsed)
    assert analyzer._walk_cache._fresh == {}
