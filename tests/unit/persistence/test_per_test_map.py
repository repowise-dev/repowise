"""The portable per-test map: what ``repowise coverage export`` writes and a
hosted upload carries. Round trip through storage, the cap rule matching
:func:`cap_test_map`, path remapping, and refusal of malformed bytes."""

from __future__ import annotations

import gzip
import json

import pytest

from repowise.core.analysis.health.coverage import TestCoverage
from repowise.core.persistence.crud import save_test_coverage
from repowise.core.persistence.crud.analysis.coverage_map import (
    PerTestMap,
    PerTestMapError,
    cap_per_test_map,
    cap_test_map,
    dump_per_test_map,
    get_per_test_map,
    is_per_test_map,
    load_per_test_map,
    per_test_map_from_records,
    remap_per_test_map,
)
from tests.unit.persistence.helpers import insert_repo

_LIMIT = 10_000_000


def _rec(test_id: str, source: str, lines: list[int], test_file: str | None = None):
    return TestCoverage(
        test_id=test_id,
        file_path=source,
        covered_lines=lines,
        source_format="coverage.py",
        test_file=test_file,
    )


_RECORDS = [
    _rec("tests/test_b.py::test_two", "src/foo.py", [3, 4, 5], "tests/test_b.py"),
    _rec("tests/test_a.py::test_one", "src/foo.py", [1, 2, 3], "tests/test_a.py"),
    _rec("tests/test_a.py::test_one", "src/bar.py", [10], "tests/test_a.py"),
    _rec("tests/test_a.py::test_one", "src/bar.py", [11], "tests/test_a.py"),
]


async def test_the_stored_map_exports_and_loads_back_unchanged(async_session) -> None:
    repo = await insert_repo(async_session)
    await save_test_coverage(
        async_session, repo.id, _RECORDS, source_format="coverage.py", ingested_commit_sha="abc"
    )
    await async_session.commit()

    stored = await get_per_test_map(async_session, repo.id)
    raw = dump_per_test_map(stored)
    loaded = load_per_test_map(raw, max_bytes=_LIMIT)

    assert is_per_test_map(raw)
    assert loaded == stored
    assert loaded.ingested_commit_sha == "abc"
    assert loaded.pair_count == 3
    # Built straight from the records, the same layout `coverage add` stored.
    assert per_test_map_from_records(_RECORDS, "coverage.py").sources == stored.sources
    assert dict(stored.sources)["src/bar.py"] == (1, '[[0,"10-11"]]')


async def test_a_repo_without_a_map_exports_nothing(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await get_per_test_map(async_session, repo.id) is None


def _many(tests: int, files_each: int) -> list[TestCoverage]:
    return [
        _rec(
            f"tests/test_{t % 7}.py::t{t}",
            f"src/m{(t * 3 + f) % 11}.py",
            [t + 1, t + 2],
            f"tests/test_{t % 7}.py",
        )
        for t in range(tests)
        for f in range(files_each)
    ]


@pytest.mark.parametrize("max_rows", [1, 5, 17, 40, 1000])
def test_the_cap_cuts_the_same_whole_tests_as_cap_test_map(max_rows: int) -> None:
    records = _many(30, 3)
    rows, _, dropped = cap_test_map(records, max_rows)

    capped, map_dropped = cap_per_test_map(per_test_map_from_records(records, "x"), max_rows)

    assert map_dropped == dropped
    assert capped.pair_count == len(rows)
    assert capped == per_test_map_from_records(rows, "x")


def test_remapping_drops_unknown_sources_and_merges_collisions() -> None:
    tmap = per_test_map_from_records(
        [
            _rec("t1", "build/src/foo.py", [1, 2], "ci/tests/test_a.py"),
            _rec("t1", "other/src/foo.py", [2, 3], "ci/tests/test_a.py"),
            _rec("t2", "vendored/x.py", [1], "ci/tests/test_b.py"),
            _rec("t3", "build/src/foo.py", [9], None),
        ],
        "coverage.py",
    )
    remapped = remap_per_test_map(
        tmap,
        {"build/src/foo.py": "src/foo.py", "other/src/foo.py": "src/foo.py"},
        {"ci/tests/test_a.py": "tests/test_a.py"},
    )

    # t2 covered only the dropped file, so it is gone and t3 is renumbered.
    assert remapped.tests == (("t3", None), ("t1", "tests/test_a.py"))
    assert remapped.sources == {"src/foo.py": (2, '[[0,"9"],[1,"1-3"]]')}


def test_remapping_merges_spans_without_expanding_them() -> None:
    """A wide span merges as a span, and a test whose spans cover nothing drops out."""
    tmap = PerTestMap(
        source_format="coverage.py",
        tests=(("t1", None), ("t2", None)),
        sources={
            "a/x.py": (2, '[[0,"1-5000000"],[1,"5-2"]]'),
            "b/x.py": (1, '[[0,"3,5000001-9999999"]]'),
        },
    )
    remapped = remap_per_test_map(tmap, {"a/x.py": "x.py", "b/x.py": "x.py"}, {})

    assert remapped.tests == (("t1", None),)
    assert remapped.sources == {"x.py": (1, '[[0,"1-9999999"]]')}
    assert load_per_test_map(dump_per_test_map(remapped), max_bytes=_LIMIT) == remapped


def _raw(doc: dict) -> bytes:
    return gzip.compress(json.dumps(doc).encode())


_GOOD = {
    "format": "repowise-test-map-v1",
    "source_format": "coverage.py",
    "tests": [["t1", "tests/test_a.py"], ["t2", None]],
    "sources": {"src/a.py": '[[0,"1-3"],[1,"7"]]'},
}


@pytest.mark.parametrize(
    "change",
    [
        {"format": "repowise-coverage-v1"},
        {"tests": {}},
        {"tests": [["", None]]},
        {"sources": {"src/a.py": '[[2,"1"]]'}},
        {"sources": {"src/a.py": '[[0,"1"],[0,"2"]]'}},
        {"sources": {"src/a.py": '[[0,"1;drop"]]'}},
        {"sources": {"src/a.py": "[]"}},
        {"sources": {"src/a.py": [[0, "1"]]}},
        {"sources": {"src/a.py": '[[0,"9-2"]]'}},
        {"sources": {"src/a.py": '[[0,"1-99999999999"]]'}},
        {"sources": {"src/a.py": f'[[{"9" * 5000},"1"]]'}},
    ],
)
def test_malformed_maps_are_refused(change: dict) -> None:
    with pytest.raises(PerTestMapError):
        load_per_test_map(_raw({**_GOOD, **change}), max_bytes=_LIMIT)


def test_a_map_that_expands_past_the_limit_is_refused_unexpanded() -> None:
    bomb = gzip.compress(b"{" + b" " * 5_000_000 + b"}")
    with pytest.raises(PerTestMapError, match="expands past"):
        load_per_test_map(bomb, max_bytes=1_000_000)


def test_plain_json_loads_and_unused_tests_are_dropped() -> None:
    doc = {**_GOOD, "tests": [*_GOOD["tests"], ["t3", None]]}
    loaded = load_per_test_map(json.dumps(doc).encode(), max_bytes=_LIMIT)
    assert [t for t, _ in loaded.tests] == ["t1", "t2"]
    assert loaded.pair_count == 2
    assert not is_per_test_map(b"SF:src/a.py\nDA:1,1\nend_of_record\n")
