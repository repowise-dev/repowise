"""No single file may stall or exhaust the parse pool.

These run real spawn pools, so the task functions live at module level where a
child process can import them.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from repowise.core.pipeline import parse_pool
from repowise.core.pipeline.parse_pool import (
    PENDING,
    ParseTimeoutError,
    ParseWorkerCrashError,
    run_with_deadline,
)


def _task(item: str) -> str:
    if item == "hang":
        while True:
            time.sleep(1)
    if item == "crash":
        os._exit(3)
    if item == "boom":
        raise ValueError("boom")
    if item == "allocate":
        return str(len(bytearray(4 * 1024**3)))
    return item.upper()


def _run(items: list[str], *, workers: int = 2, timeout: float | None = 5.0) -> list:
    results = [PENDING] * len(items)
    seen: list[int] = []
    run_with_deadline(
        _task, items, workers, results, timeout=timeout, on_done=lambda i, _r: seen.append(i)
    )
    assert sorted(seen) == list(range(len(items))), "every task reports done exactly once"
    return results


def test_a_hung_task_times_out_and_the_rest_finish() -> None:
    items = ["a", "hang", "b", "c", "hang", "d", "e"]
    started = time.monotonic()
    results = _run(items)
    elapsed = time.monotonic() - started

    assert [r for i, r in enumerate(results) if items[i] != "hang"] == ["A", "B", "C", "D", "E"]
    for i, item in enumerate(items):
        if item == "hang":
            assert isinstance(results[i], ParseTimeoutError)
            assert str(results[i]) == "parse timed out after 5s"
    # Two workers, two hung tasks: one deadline each at most, plus spawn time.
    assert elapsed < 60


def test_every_worker_hung_at_once_still_finishes() -> None:
    results = _run(["hang", "hang", "a", "b"], workers=2, timeout=3.0)
    assert isinstance(results[0], ParseTimeoutError)
    assert isinstance(results[1], ParseTimeoutError)
    assert results[2:] == ["A", "B"]


def test_a_crashing_task_is_isolated() -> None:
    results = _run(["a", "b", "crash", "c", "d"], workers=2)
    assert results[:2] == ["A", "B"]
    assert isinstance(results[2], ParseWorkerCrashError)
    assert results[3:] == ["C", "D"]


def test_a_pool_that_cannot_start_raises_for_the_fallback(monkeypatch) -> None:
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    def dead_pool(workers: int) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=workers, mp_context=parse_pool.MP_SPAWN, initializer=os._exit, initargs=(1,)
        )

    monkeypatch.setattr(parse_pool, "_new_pool", dead_pool)
    results = [PENDING, PENDING]
    with pytest.raises(BrokenProcessPool):
        run_with_deadline(_task, ["a", "b"], 2, results, timeout=5.0)
    # No file is blamed: every slot is left for the in-process fallback.
    assert results == [PENDING, PENDING]


def test_resolved_slots_are_left_alone() -> None:
    results = ["done", PENDING]
    run_with_deadline(_task, ["hang", "x"], 1, results, timeout=5.0)
    assert results == ["done", "X"]


def test_a_task_exception_is_its_result() -> None:
    results = _run(["a", "boom", "b"], timeout=None)
    assert results[0] == "A"
    assert isinstance(results[1], ValueError)
    assert results[2] == "B"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_DATA is enforced on Linux")
def test_a_runaway_allocation_is_a_memory_error_for_that_file(monkeypatch) -> None:
    monkeypatch.setenv("REPOWISE_PARSE_WORKER_MEMORY_MB", "512")
    results = _run(["a", "allocate", "b"])
    assert results[0] == "A"
    assert isinstance(results[1], MemoryError)
    assert results[2] == "B"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 60.0), ("", 60.0), ("15", 15.0), ("0.5", 0.5), ("0", None), ("-3", 60.0), ("x", 60.0)],
)
def test_parse_timeout_env(monkeypatch, raw: str | None, expected: float | None) -> None:
    if raw is None:
        monkeypatch.delenv("REPOWISE_PARSE_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("REPOWISE_PARSE_TIMEOUT_S", raw)
    assert parse_pool.parse_timeout_s() == expected


def test_report_names_the_skipped_files() -> None:
    from datetime import datetime

    from repowise.core.ingestion.models import FileInfo
    from repowise.core.pipeline.phases.ingestion import report_parse_failures

    def fi(path: str) -> FileInfo:
        return FileInfo(
            path=path,
            abs_path=f"/repo/{path}",
            language="sql",
            size_bytes=0,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )

    to_parse = [(0, (fi("ok.sql"), b""), ""), (1, (fi("slow.sql"), b""), "")]
    assert report_parse_failures(to_parse, ["parsed", ("x", "syntax")]) is None
    message = report_parse_failures(
        to_parse, ["parsed", ParseTimeoutError("parse timed out after 60s")]
    )
    assert message is not None
    assert "slow.sql (parse timed out after 60s)" in message
    assert "REPOWISE_PARSE_TIMEOUT_S" in message
