"""``go test -bench -benchmem`` output: sniffed, written as a v1 benchmark document.

The fixture is a tiny Go module (``tests/fixtures/go_bench``) and its real
output from go 1.26: ``bench.txt`` is ``go test '-run=^$' -bench . -benchmem
-count=2 -benchtime=2000x ./...`` and ``bench.json`` the same with ``-json``
and ``-count=1``, the CPU name scrubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.add import prepare_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import (
    ProfileFormat,
    detect_profile_format,
    profile_formats,
)

FIXTURE = Path(__file__).parents[2] / "fixtures" / "go_bench"
PKG = "example.com/gobench/strs"


def _convert(data: bytes, root: Path | None = FIXTURE) -> list[dict]:
    doc = parse_evidence(json.dumps(profile_formats.get("go-bench").to_v1(data, root)))
    assert (doc.kind, doc.tool_name) == ("benchmark", "go")
    return list(doc.entries)


def test_text_json_and_powershell_output_are_sniffed() -> None:
    text = (FIXTURE / "bench.txt").read_bytes()
    assert detect_profile_format(text[:4096]) == "go-bench"
    assert detect_profile_format((FIXTURE / "bench.json").read_bytes()[:4096]) == "go-bench"
    # PowerShell 5's ``>`` writes UTF-16 with a byte-order mark.
    utf16 = text.decode().encode("utf-16")
    assert detect_profile_format(utf16[:4096]) == "go-bench"
    assert len(_convert(utf16)) == 3
    assert detect_profile_format(b"PASS\nok  \texample.com/x\t0.1s\n") is None
    # A header line deeper in another file does not claim it.
    assert detect_profile_format(b"main;work 3\ngoos: linux\n") != "go-bench"


def test_each_benchmark_is_one_series_with_exact_allocations() -> None:
    by_name = {e["name"]: e for e in _convert((FIXTURE / "bench.txt").read_bytes())}
    assert list(by_name) == [
        f"{PKG}.BenchmarkJoin",
        f"{PKG}.BenchmarkConcat/n=2",
        f"{PKG}.BenchmarkConcat/n=5",
    ]
    join = by_name[f"{PKG}.BenchmarkJoin"]
    # ``-count=2`` folds into one value; time is the repeats' statistics.
    assert (join["unit"], join["lower_is_better"], join["rounds"], join["iterations"]) == (
        "ns", True, 2, 2000,
    )
    assert (join["min"], join["max"]) == (50.75, 56.2)
    assert join["median"] == pytest.approx(53.475)
    assert (join["allocs_per_op"], join["bytes_per_op"]) == (1, 24)
    assert by_name[f"{PKG}.BenchmarkConcat/n=5"]["allocs_per_op"] == 4
    # Linked through go.mod to the _test.go file that declares the benchmark.
    assert join["location"] == {
        "path": "strs/join_test.go", "function": "BenchmarkJoin", "line": 10, "exact": True,
    }
    sub = by_name[f"{PKG}.BenchmarkConcat/n=2"]["location"]
    assert (sub["path"], sub["function"], sub["line"]) == ("strs/join_test.go", "BenchmarkConcat", 16)


def test_json_output_reads_like_the_text() -> None:
    entries = _convert((FIXTURE / "bench.json").read_bytes())
    assert [(e["name"], e["allocs_per_op"], e["bytes_per_op"], e["rounds"]) for e in entries] == [
        (f"{PKG}.BenchmarkJoin", 1, 24, 1),
        (f"{PKG}.BenchmarkConcat/n=2", 1, 3, 1),
        (f"{PKG}.BenchmarkConcat/n=5", 4, 40, 1),
    ]
    assert entries[0]["stddev"] is None
    assert entries[0]["location"]["path"] == "strs/join_test.go"


def _lines(*results: str, pkg: str = PKG) -> bytes:
    return "\n".join(["goos: linux", "goarch: amd64", f"pkg: {pkg}", *results, "PASS"]).encode()


def test_repeats_keep_the_fewest_allocations_and_a_shared_procs_suffix_is_dropped() -> None:
    entries = _convert(_lines(
        "BenchmarkJoin-8   1000   50.0 ns/op   24 B/op   2 allocs/op",
        "BenchmarkJoin-8   1000   60.0 ns/op   16 B/op   1 allocs/op",
        "BenchmarkConcat/size-64-8   500   10.0 ns/op   12.50 MB/s",
    ))
    by_name = {e["name"]: e for e in entries}
    assert list(by_name) == [f"{PKG}.BenchmarkJoin", f"{PKG}.BenchmarkConcat/size-64"]
    assert (by_name[f"{PKG}.BenchmarkJoin"]["allocs_per_op"],
            by_name[f"{PKG}.BenchmarkJoin"]["bytes_per_op"]) == (1, 16)
    # Without -benchmem there is time only, never a made-up zero.
    assert by_name[f"{PKG}.BenchmarkConcat/size-64"]["allocs_per_op"] is None


@pytest.mark.parametrize("results", [
    # GOMAXPROCS=1 prints no suffix: a sub-benchmark's own dash is its name.
    ("BenchmarkJoin/size-64  10  1 ns/op", "BenchmarkJoin/size-128  10  1 ns/op"),
    # A lone sub-benchmark: nothing shows its -64 is GOMAXPROCS.
    ("BenchmarkJoin/size-64  10  1 ns/op",),
    # -cpu 1,4: one series per procs value.
    ("BenchmarkJoin/size-64  10  1 ns/op", "BenchmarkJoin/size-64-4  10  1 ns/op"),
])
def test_names_stay_whole_when_the_suffix_is_not_one_shared_procs(results) -> None:
    names = [e["name"].removeprefix(f"{PKG}.") for e in _convert(_lines(*results))]
    assert names == [r.split()[0] for r in results]


def test_a_benchmark_it_cannot_place_keeps_its_series() -> None:
    data = _lines("BenchmarkJoin-8  1000  50 ns/op  0 B/op  0 allocs/op")
    assert _convert(data, None)[0]["location"] is None
    other = _lines("BenchmarkJoin-8  1000  50 ns/op", pkg="example.org/elsewhere")
    assert _convert(other)[0]["location"] is None
    missing = _lines("BenchmarkGone-8  1000  50 ns/op")
    assert _convert(missing)[0]["location"] is None


def test_failed_benchmarks_are_counted_and_no_results_are_refused() -> None:
    data = _lines(
        "--- FAIL: BenchmarkBroken-8",
        "BenchmarkJoin-8  1000  50 ns/op  0 B/op  0 allocs/op",
    )
    doc = parse_evidence(json.dumps(profile_formats.get("go-bench").to_v1(data, FIXTURE)))
    assert doc.skipped == {"failed": 1}
    with pytest.raises(EvidenceFormatError, match="a benchmark failed"):
        profile_formats.get("go-bench").to_v1(_lines("--- FAIL: BenchmarkBroken-8"), FIXTURE)
    with pytest.raises(EvidenceFormatError, match="no go test benchmark results"):
        profile_formats.get("go-bench").to_v1(b"goos: linux\nFAIL\n", FIXTURE)


def test_only_a_format_that_asks_gets_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A one-argument ``to_v1`` (the default contract) still ingests."""
    assert profile_formats.get("go-bench").needs_checkout
    assert not profile_formats.get("pytest-benchmark").needs_checkout
    doc = {"format": "repowise-evidence-v1", "kind": "benchmark",
           "benchmarks": [{"name": "b", "median": 1.0}]}

    def one_argument(data: bytes) -> dict:
        return doc

    monkeypatch.setitem(
        profile_formats._impls, "one-arg", ProfileFormat(lambda _h: False, to_v1=one_argument)
    )
    prepared = prepare_evidence(
        b"x", name="f", checkout_root=FIXTURE, commit="a" * 40, input_format_name="one-arg"
    )
    assert prepared.doc is not None and [e["name"] for e in prepared.doc.entries] == ["b"]


def test_a_sub_benchmarks_procs_suffix_needs_a_witness() -> None:
    # -v (and -json) print each name without the suffix on its === RUN line.
    verbose = _lines("=== RUN   BenchmarkJoin/size-64", "BenchmarkJoin/size-64-8  10  1 ns/op")
    assert [e["name"] for e in _convert(verbose)] == [f"{PKG}.BenchmarkJoin/size-64"]
    # A top-level result is a witness too: a Go identifier holds no "-".
    top = _lines("BenchmarkA-8  10  1 ns/op", "BenchmarkJoin/size-64-8  10  1 ns/op")
    assert [e["name"] for e in _convert(top)] == [f"{PKG}.BenchmarkA", f"{PKG}.BenchmarkJoin/size-64"]


def test_a_failed_sub_benchmark_counts_once_with_its_parent() -> None:
    data = _lines(
        "--- FAIL: BenchmarkBroken",
        "    --- FAIL: BenchmarkBroken/n=1",
        "    --- FAIL: BenchmarkBroken/n=2",
        "BenchmarkJoin-8  1000  50 ns/op",
    )
    doc = parse_evidence(json.dumps(profile_formats.get("go-bench").to_v1(data, FIXTURE)))
    assert doc.skipped == {"failed": 1}


def test_a_series_that_keeps_its_procs_suffix_still_links_its_function() -> None:
    entries = _convert(_lines("BenchmarkJoin  10  1 ns/op", "BenchmarkJoin-4  10  1 ns/op"))
    assert [(e["name"], e["location"]["function"]) for e in entries] == [
        (f"{PKG}.BenchmarkJoin", "BenchmarkJoin"), (f"{PKG}.BenchmarkJoin-4", "BenchmarkJoin"),
    ]


def test_a_result_split_by_what_the_benchmark_printed_is_joined_back() -> None:
    # Real go 1.26 output of a benchmark that calls fmt.Println and b.Log.
    data = _lines(
        "BenchmarkLog-32      \tprinted",
        "     100\t       243.0 ns/op\t     109 B/op\t       0 allocs/op",
        "--- BENCH: BenchmarkLog-32",
        "    l_test.go:6: hello",
        "BenchmarkQuiet-32    \t     100\t         1.000 ns/op\t       0 B/op\t       0 allocs/op",
    )
    entries = {e["name"]: e for e in _convert(data)}
    assert list(entries) == [f"{PKG}.BenchmarkLog", f"{PKG}.BenchmarkQuiet"]
    log = entries[f"{PKG}.BenchmarkLog"]
    assert (log["bytes_per_op"], log["median"]) == (109, 243)
