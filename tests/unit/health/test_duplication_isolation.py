"""Large duplication scans release their transient allocator arenas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication.isolation import (
    detect_clones_with_isolation,
)


@pytest.fixture
def shared_cache_key(monkeypatch):
    """One cache-seal key for this process and the workers it spawns.

    The key is cached per process, so a test that ran earlier on the same
    worker can leave this process sealing with a key that the spawned child,
    which reads the key afresh, does not share.
    """
    from repowise.core import cache_seal

    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "ab" * 32)
    cache_seal.reset_key_cache_for_tests()
    yield
    cache_seal.reset_key_cache_for_tests()


def _parsed(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        file_info=SimpleNamespace(
            path=path.name,
            abs_path=str(path),
            language="python",
        )
    )


def _key(report):
    return sorted(
        (
            pair.file_a,
            pair.file_b,
            pair.a_start_line,
            pair.a_end_line,
            pair.b_start_line,
            pair.b_end_line,
        )
        for pair in report.pairs
    )


def test_isolated_detection_matches_in_process(tmp_path: Path):
    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text(body)
    second.write_text(body)
    parsed = [_parsed(first), _parsed(second)]

    expected = detect_clones(parsed, window_tokens=20, min_lines=4)
    actual = detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        cache_dir=tmp_path / ".repowise",
        isolation_file_threshold=0,
    )

    assert _key(actual) == _key(expected)
    assert actual.duplication_pct == expected.duplication_pct
    assert (tmp_path / ".repowise" / "duplication_cache.pkl").exists()


def test_a_custom_reader_is_honoured_not_ignored(tmp_path: Path):
    """The reader's bytes win over what is on disk.

    The engine routes every health read through a ``SourceReader`` so a pass
    can analyse content that is not the working tree. If the isolation wrapper
    dropped the reader, those callers would silently get working-tree results
    labelled as the revision's.
    """
    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    # On disk the two files differ, so a disk read finds no clone at all.
    first.write_text(body)
    second.write_text("unique = 0\n")
    parsed = [_parsed(first), _parsed(second)]

    served = {str(first): body.encode(), str(second): body.encode()}
    report = detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        source_reader=lambda abs_path: served.get(abs_path),
        isolation_file_threshold=0,
    )

    assert report.pairs, "the reader's identical bodies should clone-match"
    assert _key(report) == _key(detect_clones(parsed, window_tokens=20, min_lines=4,
                                             source_reader=lambda p: served.get(p)))


def test_a_custom_reader_stays_in_process(tmp_path: Path, monkeypatch):
    """A non-working-tree reader must not be shipped to a spawned worker.

    It cannot be pickled in the general case, and the one such reader in the
    tree holds every file's bytes, which is the memory the isolation exists to
    avoid moving. Falling back to a disk read in the worker would be wrong
    rather than merely slow, so the whole scan stays here.
    """
    import repowise.core.analysis.health.duplication.isolation as isolation

    def _fail(*args, **kwargs):
        raise AssertionError("a custom reader must not reach the spawned worker")

    monkeypatch.setattr(isolation.multiprocessing, "get_context", _fail)

    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text(body)
    second.write_text(body)
    parsed = [_parsed(first), _parsed(second)]

    served = {str(first): body.encode(), str(second): body.encode()}
    report = isolation.detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        source_reader=lambda abs_path: served.get(abs_path),
        isolation_file_threshold=0,
    )
    assert report.pairs


def test_the_default_reader_still_isolates(tmp_path: Path, monkeypatch):
    """The working-tree case keeps the spawn, which is where the win was measured."""
    import repowise.core.analysis.health.duplication.isolation as isolation
    from repowise.core.analysis.health.source_reader import disk_source_reader

    spawned: list[bool] = []
    real = isolation.multiprocessing.get_context

    def _record(*args, **kwargs):
        spawned.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(isolation.multiprocessing, "get_context", _record)

    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text(body)
    second.write_text(body)
    parsed = [_parsed(first), _parsed(second)]

    for reader in (None, disk_source_reader):
        spawned.clear()
        report = isolation.detect_clones_with_isolation(
            parsed,
            window_tokens=20,
            min_lines=4,
            source_reader=reader,
            isolation_file_threshold=0,
        )
        assert spawned, f"reader {reader!r} should still take the isolated path"
        assert report.pairs


def test_an_update_splice_stays_in_process_and_matches_a_full_scan(
    tmp_path: Path, monkeypatch, shared_cache_key
):
    """A small update splices the persisted index here, without a worker.

    The spawn costs seconds of interpreter start-up for a splice that reads a
    few files; the report must still equal a from-scratch scan of the new tree.
    """
    import repowise.core.analysis.health.duplication.isolation as isolation

    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    files = [tmp_path / name for name in ("a.py", "b.py", "c.py")]
    for path in files:
        path.write_text(body)
    parsed = [_parsed(path) for path in files]
    cache_dir = tmp_path / ".repowise"
    isolation.detect_clones_with_isolation(
        parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir, isolation_file_threshold=0
    )

    def _fail(*args, **kwargs):
        raise AssertionError("a spliceable update must not spawn a worker")

    monkeypatch.setattr(isolation.multiprocessing, "get_context", _fail)
    files[2].write_text("unique = 0\n")
    report = isolation.detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        cache_dir=cache_dir,
        changed_files={"c.py"},
        isolation_file_threshold=0,
    )

    assert report.diagnostics.get("incremental") is True
    expected = detect_clones(parsed, window_tokens=20, min_lines=4)
    assert _key(report) == _key(expected)
    assert report.duplication_pct == expected.duplication_pct


def _record_spawns(isolation, monkeypatch) -> list[bool]:
    """The ``splice`` argument of every worker spawned, in order."""
    real_get_context = isolation.multiprocessing.get_context
    splice_args: list[bool] = []

    def _recording_context(method):
        context = real_get_context(method)

        class _Context:
            Pipe = staticmethod(context.Pipe)

            @staticmethod
            def Process(*, target, args, name):
                splice_args.append(args[-1])
                return context.Process(target=target, args=args, name=name)

        return _Context()

    monkeypatch.setattr(isolation.multiprocessing, "get_context", _recording_context)
    return splice_args


def _two_clones(tmp_path: Path) -> list[SimpleNamespace]:
    body = "\n".join(f"value_{index} = source_{index} + 1" for index in range(30))
    paths =[tmp_path / "a.py", tmp_path / "b.py"]
    for path in paths:
        path.write_text(body)
    return [_parsed(path) for path in paths]


def test_an_update_without_a_spliceable_index_still_isolates(
    tmp_path: Path, monkeypatch, shared_cache_key
):
    """A token cache but no pair index: the splice misses here, and the full
    scan runs in the worker, told not to repeat the miss."""
    import repowise.core.analysis.health.duplication.isolation as isolation

    spawns = _record_spawns(isolation, monkeypatch)
    parsed = _two_clones(tmp_path)
    cache_dir = tmp_path / ".repowise"
    cache_dir.mkdir()
    (cache_dir / "duplication_cache.pkl").write_bytes(b"unreadable")

    report = isolation.detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        cache_dir=cache_dir,
        changed_files={"a.py"},
        isolation_file_threshold=0,
    )
    assert spawns == [False]
    assert _key(report) == _key(detect_clones(parsed, window_tokens=20, min_lines=4))


def test_a_token_cache_past_the_bound_splices_in_the_worker(
    tmp_path: Path, monkeypatch, shared_cache_key
):
    """Past the size bound the load's retained pages cost more than the spawn."""
    import repowise.core.analysis.health.duplication.isolation as isolation

    parsed = _two_clones(tmp_path)
    cache_dir = tmp_path / ".repowise"
    isolation.detect_clones_with_isolation(
        parsed, window_tokens=20, min_lines=4, cache_dir=cache_dir, isolation_file_threshold=0
    )
    spawns = _record_spawns(isolation, monkeypatch)
    monkeypatch.setattr(isolation, "_IN_PROCESS_SPLICE_MAX_CACHE_BYTES", 0)

    report = isolation.detect_clones_with_isolation(
        parsed,
        window_tokens=20,
        min_lines=4,
        cache_dir=cache_dir,
        changed_files={"a.py"},
        isolation_file_threshold=0,
    )
    assert spawns == [True]
    assert report.diagnostics.get("incremental") is True
