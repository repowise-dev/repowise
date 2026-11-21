"""Joining v1 entries to indexed symbols: exact, counted, never guessed."""

from __future__ import annotations

import json

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.ingest import (
    EvidenceCaps,
    IndexedSymbol,
    RunProvenance,
    cap_rows,
    join_entries,
    resolve_location,
    run_key,
    symbol_lookup,
)

_SYMBOLS = {
    "src/app.py": symbol_lookup(
        [
            IndexedSymbol("src/app.py::build", "build", "build", 10),
            IndexedSymbol("src/app.py::Reader.read", "Reader.read", "read", 30),
            IndexedSymbol("src/app.py::Writer.read", "Writer.read", "read", 50),
            IndexedSymbol("src/app.py::parse#1", "parse", "parse", 70),
            IndexedSymbol("src/app.py::parse#2", "parse", "parse", 80),
        ]
    ),
    "gen/pb.py": symbol_lookup([IndexedSymbol("gen/pb.py::decode", "decode", "decode", 1)]),
    "src/edited.py": symbol_lookup([IndexedSymbol("src/edited.py::f", "f", "f", 1)]),
}


def _loc(path: str, function: str, line: int | None = None) -> dict:
    return {"path": path, "function": function, "line": line}


def test_resolution_is_exact_and_refuses_to_guess() -> None:
    assert resolve_location(_loc("src/app.py", "build"), _SYMBOLS) == ("src/app.py::build", None)
    assert resolve_location(_loc("src/app.py", "Reader.read"), _SYMBOLS)[0] == (
        "src/app.py::Reader.read"
    )
    # A bare name shared by two methods is ambiguous; never the first row.
    assert resolve_location(_loc("src/app.py", "read"), _SYMBOLS) == (None, "ambiguous_symbol")
    # Overloads are told apart by their start line only.
    assert resolve_location(_loc("src/app.py", "parse", 80), _SYMBOLS)[0] == "src/app.py::parse#2"
    assert resolve_location(_loc("src/app.py", "parse"), _SYMBOLS) == (None, "ambiguous_symbol")
    assert resolve_location(_loc("src/app.py", "gone"), _SYMBOLS) == (None, "unknown_symbol")
    # A bare name settles on a line only when the line agrees.
    assert resolve_location(_loc("src/app.py", "read", 50), _SYMBOLS)[0] == "src/app.py::Writer.read"
    assert resolve_location(_loc("src/app.py", "read", 99), _SYMBOLS) == (None, "unknown_symbol")
    assert resolve_location(_loc("src/other.py", "f"), _SYMBOLS) == (None, "unknown_file")


def test_a_function_named_by_its_id_after_the_file_resolves_exactly() -> None:
    # How a test runner names a test: Python qualified names are module-dotted.
    symbols = {
        "benchmarks/test_io.py": symbol_lookup(
            [
                IndexedSymbol(
                    "benchmarks/test_io.py::TestCache::test_get", "benchmarks.test_io.TestCache.test_get",
                    "test_get", 5,
                ),
                IndexedSymbol(
                    "benchmarks/test_io.py::test_get", "benchmarks.test_io.test_get", "test_get", 20
                ),
            ]
        )
    }
    assert resolve_location(_loc("benchmarks/test_io.py", "TestCache::test_get"), symbols)[0] == (
        "benchmarks/test_io.py::TestCache::test_get"
    )
    # The id after the file wins over a method that shares the bare name.
    assert resolve_location(_loc("benchmarks/test_io.py", "test_get"), symbols)[0] == (
        "benchmarks/test_io.py::test_get"
    )
    assert resolve_location(_loc("benchmarks/test_io.py", "Other::test_get"), symbols) == (
        None, "unknown_symbol",
    )
    # An exact location never falls back to a bare name: with the module
    # function not indexed, the method that shares its name is not it.
    method_only = {
        "benchmarks/test_io.py": symbol_lookup(
            [IndexedSymbol("benchmarks/test_io.py::TestCache::test_get", "x.TestCache.test_get",
                           "test_get", 5)]
        )
    }
    exact = {**_loc("benchmarks/test_io.py", "test_get"), "exact": True}
    assert resolve_location(exact, method_only) == (None, "unknown_symbol")
    assert resolve_location(_loc("benchmarks/test_io.py", "test_get"), method_only)[0] == (
        "benchmarks/test_io.py::TestCache::test_get"
    )


def _profile(symbols: list[dict]) -> str:
    return json.dumps(
        {"format": "repowise-evidence-v1", "kind": "profile", "value_kind": "wall",
         "unit": "samples", "symbols": symbols}
    )


def test_join_counts_every_skip_by_reason() -> None:
    doc = parse_evidence(
        _profile(
            [
                {"path": "src/app.py", "function": "build", "total": 40, "self": 4},
                {"path": "src/app.py", "function": "read", "total": 9},
                {"path": "gen/pb.py", "function": "decode", "total": 30},
                {"path": "src/edited.py", "function": "f", "total": 2},
                {"path": "src/nowhere.py", "function": "f", "total": 2},
            ]
        )
    )
    joined = join_entries(
        doc,
        _SYMBOLS,
        {"gen/pb.py": "generated", "src/app.py": "production"},
        frozenset({"src/edited.py"}),
    )
    assert [r["symbol_id"] for r in joined.rows] == ["src/app.py::build"]
    assert joined.rows[0]["resolution"] == "declared"
    assert joined.matched == 1
    assert joined.skipped == {
        "ambiguous_symbol": 1, "generated": 1, "dirty_file": 1, "unknown_file": 1,
    }
    assert joined.unmatched_sample == [
        "src/app.py", "gen/pb.py", "src/edited.py", "src/nowhere.py",
    ]


def test_a_benchmark_without_a_resolvable_function_is_still_a_series() -> None:
    doc = parse_evidence(
        json.dumps(
            {"format": "repowise-evidence-v1", "kind": "benchmark", "benchmarks": [
                {"name": "BenchmarkBuild", "path": "src/app.py", "function": "build",
                 "median": 1.5},
                {"name": "BenchmarkOther", "path": "src/app.py", "function": "gone",
                 "median": 2.0},
            ]}
        )
    )
    joined = join_entries(doc, _SYMBOLS, {"gen/pb.py": "generated"}, frozenset({"src/app.py"}))
    # Edited since the commit: the series is kept, the link to the function is not.
    assert [(r["benchmark_key"], r["symbol_id"]) for r in joined.rows] == [
        ("BenchmarkBuild", None),
        ("BenchmarkOther", None),
    ]
    assert joined.skipped == {}
    assert joined.unlinked == {"dirty_file": 2}
    joined = join_entries(doc, _SYMBOLS, {}, frozenset())
    assert [(r["benchmark_key"], r["symbol_id"]) for r in joined.rows] == [
        ("BenchmarkBuild", "src/app.py::build"),
        ("BenchmarkOther", None),
    ]
    assert (joined.skipped, joined.unlinked) == ({}, {"unknown_symbol": 1})


def test_two_names_for_one_symbol_store_one_row() -> None:
    doc = parse_evidence(
        _profile(
            [
                {"path": "src/app.py", "function": "Reader.read", "total": 3},
                {"path": "src/app.py", "function": "read", "line": 30, "total": 3},
            ]
        )
    )
    joined = join_entries(doc, _SYMBOLS, {}, frozenset())
    assert len(joined.rows) == 1
    assert joined.skipped == {"duplicate": 1}


def test_a_profile_keeps_its_costliest_symbols_under_the_cap() -> None:
    rows = [
        {"symbol_id": f"a::{i}", "total_value": float(i % 4), "self_value": None}
        for i in range(8)
    ]
    caps = EvidenceCaps(max_symbols_per_run=3, max_rows_per_run=100, history_runs=1, max_runs=1)
    kept, dropped = cap_rows("profile", rows, caps)
    assert dropped == 5
    assert [r["symbol_id"] for r in kept] == ["a::3", "a::7", "a::2"]
    kept, dropped = cap_rows("test_timing", [{"test_id": str(i)} for i in range(5)], caps)
    assert (len(kept), dropped) == (5, 0)


def test_the_run_key_covers_the_provenance() -> None:
    base = RunProvenance("cli build", "entry_point", "a" * 40, "/repo", False)
    key = run_key("{}", base, "profile")
    # A later index joins the same file afresh rather than restamping it.
    assert run_key("{}", base, "profile", "b" * 40) != key
    assert key == run_key("{}", base, "profile")
    assert len(key) == 32
    for other in (
        RunProvenance("cli build", "entry_point", "a" * 40, "/repo", True),
        RunProvenance("cli test", "entry_point", "a" * 40, "/repo", False),
        RunProvenance("cli build", "entry_point", "a" * 40, "/repo", False, environment="ci"),
    ):
        assert run_key("{}", other, "profile") != key
    assert run_key("{ }", base, "profile") != key


def test_a_failed_test_count_is_part_of_the_run_and_its_key() -> None:
    base = RunProvenance("python tests", "unit_test", "a" * 40, "/repo", False)
    red = RunProvenance("python tests", "unit_test", "a" * 40, "/repo", False, tests_failed=2)
    redder = RunProvenance("python tests", "unit_test", "a" * 40, "/repo", False, tests_failed=3)
    zero = RunProvenance("python tests", "unit_test", "a" * 40, "/repo", False, tests_failed=0)
    # Every count is its own run, and a run that reports none differs from one that reports 0.
    keys = {run_key("{}", p, "profile") for p in (base, red, redder, zero)}
    assert len(keys) == 4
