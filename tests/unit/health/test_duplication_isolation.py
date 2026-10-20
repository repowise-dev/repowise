"""Large duplication scans release their transient allocator arenas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.health.duplication import detect_clones
from repowise.core.analysis.health.duplication.isolation import (
    detect_clones_with_isolation,
)


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
