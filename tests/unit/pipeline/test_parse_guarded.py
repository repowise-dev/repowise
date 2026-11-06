"""``parse_guarded``: pool results, the in-process fallback, and skipped-file reporting.

The pool is replaced by in-process fakes here, so every branch is measurable
in this process; ``test_parse_pool_deadline.py`` drives real spawn pools.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.pipeline.parse_pool import PENDING, ParseTimeoutError, ParseWorkerCrashError
from repowise.core.pipeline.phases import ingestion as ing


def _fi(path: str, root: str = "/repo") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"{root}/{path}",
        language="python",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _to_parse(*names: str) -> list[tuple]:
    return [(i, (_fi(n), f"def f_{i}():\n    return {i}\n".encode()), "") for i, n in enumerate(names)]


def test_pool_failure_falls_back_for_pending_slots_only(monkeypatch) -> None:
    from_pool = object()

    def pool_dies_after_one(_fn, _items, _workers, results, **_kw):
        results[0] = from_pool
        raise OSError("spawn failed")

    monkeypatch.setattr(ing, "run_with_deadline", pool_dies_after_one)
    fallbacks: list[Exception] = []
    done: list[int] = []
    results = ing.parse_guarded(
        _to_parse("a.py", "b.py", "c.py"), on_done=done.append, on_fallback=fallbacks.append
    )

    assert results[0] is from_pool, "a slot the pool resolved is not parsed again"
    assert all(isinstance(r, ParsedFile) for r in results[1:])
    assert PENDING not in results
    assert [str(e) for e in fallbacks] == ["spawn failed"]
    assert done == [1, 2], "only the fallback's own files tick"


def test_timeouts_and_crashes_come_back_and_are_named(monkeypatch) -> None:
    def pool(_fn, _items, _workers, results, **_kw):
        results[0] = ParseTimeoutError("parse timed out after 60s")
        results[1] = ParseWorkerCrashError("parse worker crashed")
        results[2] = "parsed"

    monkeypatch.setattr(ing, "run_with_deadline", pool)
    to_parse = _to_parse("slow.sql", "crash.sql", "fine.py")
    results = ing.parse_guarded(to_parse)

    assert isinstance(results[0], ParseTimeoutError)
    assert isinstance(results[1], ParseWorkerCrashError)
    message = ing.report_parse_failures(to_parse, results)
    assert message is not None
    assert "slow.sql (parse timed out after 60s)" in message
    assert "crash.sql (parse worker crashed)" in message
    assert "fine.py" not in message
    # Neither reaches the parsed set; both are logged, not silently dropped.
    parsed, _sources = ing._collect_parsed(
        [item for _i, item, _h in to_parse], dict(enumerate(results))
    )
    assert parsed == ["parsed"]


def test_nothing_to_parse_starts_no_pool(monkeypatch) -> None:
    def no_pool(*_a, **_kw):
        raise AssertionError("a pool was started for zero files")

    monkeypatch.setattr(ing, "run_with_deadline", no_pool)
    assert ing.parse_guarded([]) == []


@pytest.fixture
def small_repo(tmp_path: Path) -> Path:
    for name in ("good_a.py", "bad.py", "good_b.py"):
        (tmp_path / name).write_text(f"def {name[:-3]}():\n    return 1\n", encoding="utf-8")
    return tmp_path


def test_reparse_repo_skips_the_file_that_fails(monkeypatch, small_repo: Path) -> None:
    from repowise.core.ingestion import ASTParser
    from repowise.core.pipeline.reparse import reparse_repo

    def pool(_fn, items, _workers, results, **_kw):
        parser = ASTParser()
        for i, (fi, source) in enumerate(items):
            if fi.path.endswith("bad.py"):
                results[i] = ParseTimeoutError("parse timed out after 60s")
            else:
                results[i] = parser.parse_file(fi, source)

    monkeypatch.setattr(ing, "run_with_deadline", pool)
    parsed, source_map, _structure = reparse_repo(small_repo, [])

    paths = sorted(p.file_info.path for p in parsed)
    assert paths == ["good_a.py", "good_b.py"]
    assert sorted(source_map) == ["good_a.py", "good_b.py"]
