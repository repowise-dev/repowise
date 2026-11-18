"""pytest-benchmark JSON: sniffed, written as a v1 benchmark document, never a profile.

The fixture is real ``pytest --benchmark-json`` output (pytest-benchmark
5.3.1) over a module function, a method and a parametrized function, with
machine details scrubbed and the raw timing arrays dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import (
    ProfileFormat,
    detect_profile_format,
    profile_formats,
)

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "pytest_benchmark.json"
COMMIT = "219544fa5513e179f3b9e4f5ee2de31dc88a39e5"


def _convert(data: bytes) -> dict:
    return profile_formats.get("pytest-benchmark").to_v1(data)


def test_the_runner_output_is_sniffed_from_its_content() -> None:
    data = FIXTURE.read_bytes()
    assert detect_profile_format(data[:4096]) == "pytest-benchmark"
    assert detect_profile_format(b"\xef\xbb\xbf" + data[:4096]) == "pytest-benchmark"
    assert detect_profile_format(b'{"benchmarks": []}') is None
    assert profile_formats.get("pytest-benchmark").read is None


def test_each_benchmark_is_one_series_value_naming_its_function() -> None:
    doc = parse_evidence(json.dumps(_convert(FIXTURE.read_bytes())))
    assert (doc.kind, doc.commit_sha, doc.dirty) == ("benchmark", COMMIT, False)
    assert (doc.tool_name, doc.tool_version, doc.skipped) == ("pytest-benchmark", "5.3.1", {})
    by_name = {e["name"]: e for e in doc.entries}
    assert list(by_name) == [
        "benchmarks/test_speed.py::test_sum",
        "benchmarks/test_speed.py::TestCache::test_get",
        "benchmarks/test_speed.py::test_sized[10]",
        "benchmarks/test_speed.py::test_sized[20]",
    ]
    method = by_name["benchmarks/test_speed.py::TestCache::test_get"]
    assert method["location"] == {
        "path": "benchmarks/test_speed.py", "function": "TestCache::test_get", "line": None,
        "exact": True,
    }
    # Parameters name the series, not the function.
    assert by_name["benchmarks/test_speed.py::test_sized[20]"]["location"]["function"] == (
        "test_sized"
    )
    raw = json.loads(FIXTURE.read_text())["benchmarks"][1]["stats"]
    assert (method["unit"], method["lower_is_better"]) == ("s", True)
    for stat in ("min", "max", "mean", "median", "stddev", "rounds", "iterations"):
        assert method[stat] == raw[stat]
    assert (method["allocs_per_op"], method["bytes_per_op"]) == (None, None)


def test_a_commit_that_is_not_a_full_sha_is_left_out() -> None:
    data = json.loads(FIXTURE.read_text())
    data["commit_info"] = {"id": "219544f", "dirty": True}
    doc = _convert(json.dumps(data).encode())
    assert "commit_sha" not in doc
    assert doc["dirty"] is True
    data["commit_info"] = {"error": "CalledProcessError(128, 'git')"}
    assert "commit_sha" not in _convert(json.dumps(data).encode())


def test_a_benchmark_without_stats_is_skipped_by_reason() -> None:
    data = json.loads(FIXTURE.read_text())
    data["benchmarks"][0]["stats"] = None
    data["benchmarks"][1]["fullname"] = "odd-id"
    doc = parse_evidence(json.dumps(_convert(json.dumps(data).encode())))
    assert doc.skipped == {"no_value": 1}
    # A node id it cannot read keeps the series without a function.
    assert [e["location"] for e in doc.entries if e["name"] == "odd-id"] == [None]


def test_a_file_that_is_not_the_runner_output_is_refused() -> None:
    with pytest.raises(EvidenceFormatError, match="pytest-benchmark"):
        _convert(b'{"machine_info": {}}')
    with pytest.raises(EvidenceFormatError):
        _convert(b"not json")


def test_a_format_has_exactly_one_reader() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ProfileFormat(lambda _h: False)
    with pytest.raises(ValueError, match="exactly one"):
        ProfileFormat(lambda _h: False, read=lambda _d: None, to_v1=lambda _d: {})


def test_a_benchmark_file_mentioning_speedscope_is_not_claimed_by_it() -> None:
    data = json.loads(FIXTURE.read_text())
    data["commit_info"]["message"] = "speedscope export for the speedscope viewer"
    head = json.dumps(data).encode()[:4096]
    assert b"speedscope" in head
    assert detect_profile_format(head) == "pytest-benchmark"
