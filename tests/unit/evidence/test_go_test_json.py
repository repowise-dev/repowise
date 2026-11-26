"""``go test -json`` output: sniffed, written as a v1 test-timing document.

``tests/fixtures/go_bench/test.json`` is ``go test -json -count=1 ./...`` over
the fixture module (go 1.26): ``TestJoin`` with two subtests, and ``TestSkipped``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats

FIXTURE = Path(__file__).parents[2] / "fixtures" / "go_bench"


def _tests(data: bytes, root: Path | None = FIXTURE) -> list[dict]:
    doc = profile_formats.get("go-test-json").to_v1(data, root)
    assert doc["kind"] == "test_timing"
    parse_evidence(json.dumps(doc))
    return doc["tests"]


def test_each_top_level_test_is_timed_at_its_function() -> None:
    data = (FIXTURE / "test.json").read_bytes()

    assert detect_profile_format(data[:4096]) == "go-test-json"
    tests = _tests(data)

    # Subtests are inside their parent's time, so only top-level tests count.
    assert [(t["id"], t["outcome"]) for t in tests] == [
        ("strs/join_test.go::TestJoin", "passed"),
        ("strs/join_test.go::TestSkipped", "skipped"),
    ]
    assert tests[0]["function"] == "TestJoin" and tests[0]["exact"] is True
    assert all(t["duration_s"] is not None for t in tests)
    assert [t["first_in_class"] for t in tests] == [True, False]


def test_the_utf16_powershell_writes_reads_the_same() -> None:
    data = (FIXTURE / "test.json").read_bytes()
    utf16 = "﻿".encode("utf-16-le") + data.decode("utf-8").encode("utf-16-le")

    assert detect_profile_format(utf16[:4096]) == "go-test-json"
    assert _tests(utf16) == _tests(data)


def test_a_failed_test_and_one_outside_the_checkout() -> None:
    lines = [
        {"Action": "run", "Package": "example.com/other", "Test": "TestGone"},
        {"Action": "fail", "Package": "example.com/other", "Test": "TestGone", "Elapsed": 1.5},
        {"Action": "fail", "Package": "example.com/other", "Elapsed": 1.6},
    ]
    data = "\n".join(json.dumps(line) for line in lines).encode()

    (test,) = _tests(data)

    assert test == {
        "id": "example.com/other::TestGone",
        "duration_s": 1.5,
        "outcome": "failed",
        "first_in_class": True,
    }


def test_benchmark_results_in_test2json_stay_benchmarks() -> None:
    data = (FIXTURE / "bench.json").read_bytes()

    # The head shows a benchmark run, so go-bench claims it; handed the file anyway,
    # this reader gives the same benchmark document.
    assert detect_profile_format(data[:4096]) == "go-bench"
    assert profile_formats.get("go-test-json").to_v1(data, FIXTURE) == profile_formats.get(
        "go-bench"
    ).to_v1(data, FIXTURE)


def test_a_run_that_built_nothing_says_so() -> None:
    data = json.dumps({"Action": "fail", "Package": "example.com/x", "Elapsed": 0}).encode()

    with pytest.raises(EvidenceFormatError, match="no go test results"):
        profile_formats.get("go-test-json").to_v1(data, FIXTURE)
