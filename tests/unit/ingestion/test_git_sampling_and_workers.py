"""Regression tests for monotonic Git sampling and bounded fan-out."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

import pytest

from repowise.core.ingestion.git_commit_index import load_sampled_commit_index
from repowise.core.ingestion.git_indexer import GitIndexer
from repowise.core.ingestion.git_indexer.indexer import git_worker_count
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier


def _commit(repo, root, path: str, value: int, subject: str) -> None:
    target = root / path
    target.write_text(f"value = {value}\n")
    repo.index.add([path])
    repo.index.commit(subject)


def _build_crossing_repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Alice")
        config.set_value("user", "email", "alice@example.com")

    _commit(repo, tmp_path, "crossing.py", 1, "feat: add crossing")
    _commit(repo, tmp_path, "crossing.py", 2, "fix: crossing two")
    _commit(repo, tmp_path, "crossing.py", 3, "fix: crossing three")
    _commit(repo, tmp_path, "recent.py", 1, "feat: recent one")
    _commit(repo, tmp_path, "crossing.py", 4, "fix: crossing four")
    _commit(repo, tmp_path, "recent.py", 2, "feat: recent two")
    return repo


def test_per_file_history_is_monotonic_when_file_crosses_into_recent_window(tmp_path) -> None:
    repo = _build_crossing_repo(tmp_path)
    retained: list[list[str]] = []

    for limit in (1, 2, 4):
        commit_sink: list[dict] = []
        sample = load_sampled_commit_index(
            repo,
            limit,
            {"crossing.py", "recent.py"},
            deep_limit=100,
            deep_threshold=1,
            commit_sink=commit_sink,
        )
        retained.append([record.sha for record in sample.commits["crossing.py"]])
        assert sample.global_commits == len(commit_sink) == limit
        assert len({row["sha"] for row in commit_sink}) == limit
        assert all(row["changes"] for row in commit_sink)

    assert retained[1][: len(retained[0])] == retained[0]
    assert retained[2][: len(retained[1])] == retained[1]
    assert len(retained) == len({tuple(rows) for rows in retained})
    repo.close()


def test_underfilled_recent_file_keeps_deep_evidence_contract(tmp_path) -> None:
    repo = _build_crossing_repo(tmp_path)
    sample = load_sampled_commit_index(
        repo,
        4,
        {"crossing.py", "recent.py"},
        deep_limit=100,
        deep_threshold=1,
    )

    assert "crossing.py" in sample.recent_files
    assert "crossing.py" in sample.deep_files
    assert len(sample.commits["crossing.py"]) == 4
    assert sample.fallback_files == set()
    repo.close()


@pytest.mark.asyncio
async def test_summary_reports_achieved_coverage_and_commit_provenance(
    tmp_path, monkeypatch
) -> None:
    repo = _build_crossing_repo(tmp_path)
    repo.close()
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.indexer._DEEP_WALK_MIN_FALLBACK", 1
    )
    summary, rows = await GitIndexer(
        tmp_path, commit_limit=2, tier=GitIndexTier.ESSENTIAL, max_workers=2
    ).index_repo("repo")

    coverage = summary.history_coverage
    assert coverage is not None
    assert coverage.eligible_files == len(rows) == 2
    assert coverage.files_with_history == 2
    assert coverage.unavailable_files == 0
    assert coverage.retained_commits == sum(row["commit_count_total"] for row in rows)
    assert coverage.global_commits == len(summary.commit_rows) == 2
    assert coverage.recent_files == 2
    assert coverage.deep_files == 2
    assert coverage.fallback_files == 0
    assert coverage.workers <= 2
    assert all(row["sha"] and row["files_changed"] for row in summary.commit_rows)


def test_worker_resolution_is_deterministic_and_resource_bounded(monkeypatch) -> None:
    gib = 1024**3
    assert git_worker_count(100, cpu_count=2, available_memory_bytes=16 * gib) == 2
    assert git_worker_count(100, cpu_count=32, available_memory_bytes=gib) == 1
    assert (
        git_worker_count(
            100, requested=50, cpu_count=4, available_memory_bytes=16 * gib
        )
        == 4
    )
    assert git_worker_count(3, cpu_count=32, available_memory_bytes=16 * gib) == 3

    monkeypatch.setenv("REPOWISE_GIT_WORKERS", "2")
    assert git_worker_count(100, cpu_count=8, available_memory_bytes=16 * gib) == 2
    monkeypatch.setenv("REPOWISE_GIT_WORKERS", "invalid")
    assert git_worker_count(100, cpu_count=2, available_memory_bytes=16 * gib) == 2


@pytest.mark.asyncio
async def test_fallback_git_failure_is_reported_as_unavailable(tmp_path, monkeypatch) -> None:
    repo = _build_crossing_repo(tmp_path)
    repo.close()
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.indexer._DEEP_WALK_MIN_FALLBACK", 10**9
    )
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.file_history._parse_per_file_log",
        lambda *args, **kwargs: (None, None),
    )

    summary, rows = await GitIndexer(
        tmp_path, commit_limit=1, tier=GitIndexTier.ESSENTIAL, max_workers=2
    ).index_repo("repo")

    coverage = summary.history_coverage
    assert coverage is not None
    unavailable = [row for row in rows if "commit_count_total" not in row]
    assert {row["file_path"] for row in unavailable} == {"crossing.py"}
    assert coverage.unavailable_files == len(unavailable) == 1
    assert coverage.files_with_history == 1


@pytest.mark.asyncio
async def test_incremental_cancellation_closes_private_executor(tmp_path, monkeypatch) -> None:
    repo = _build_crossing_repo(tmp_path)
    repo.close()
    submitted = asyncio.Event()
    executors = []

    class BlockingExecutor:
        def __init__(self, *args, **kwargs):
            self.future = Future()
            self.shutdown_calls = []
            executors.append(self)

        def submit(self, fn, *args, **kwargs):
            submitted.set()
            return self.future

        def shutdown(self, wait=True, *, cancel_futures=False):
            self.shutdown_calls.append((wait, cancel_futures))
            self.future.cancel()

    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", BlockingExecutor)
    task = asyncio.create_task(
        GitIndexer(
            tmp_path, commit_limit=2, tier=GitIndexTier.ESSENTIAL, max_workers=1
        ).index_changed_files(["recent.py"])
    )
    await submitted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(executors) == 1
    assert executors[0].shutdown_calls == [(False, True)]
