"""Google Benchmark JSON: sniffed from content, one series per benchmark, aggregates folded.

The payloads are real ``--benchmark_format=json`` shapes (Google Benchmark
1.9): a plain run, and the same benchmark under
``--benchmark_repetitions=3``, whose ``median`` and ``stddev`` aggregate
rows fill the entry's fields without duplicating the series.
"""

from __future__ import annotations

import json

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import (
    ProfileFormat,
    detect_profile_format,
    profile_formats,
)

PLAIN = {
    "context": {"date": "2026-11-19T00:00:00Z", "num_cpus": 8},
    "benchmarks": [
        {
            "name": "BM_Sort/1024",
            "run_type": "iteration",
            "iterations": 5000,
            "real_time": 210.5,
            "cpu_time": 209.8,
            "time_unit": "ns",
        },
        {
            "name": "BM_Copy",
            "run_type": "iteration",
            "iterations": 120,
            "real_time": 5.75,
            "cpu_time": 5.7,
            "time_unit": "us",
        },
    ],
}

REPETITIONS = {
    "context": {"date": "2026-11-19T00:00:00Z"},
    "benchmarks": [
        {
            "name": "BM_Sort/1024",
            "run_type": "iteration",
            "repetitions": 0,
            "threads": 1,
            "iterations": 4980,
            "real_time": 209.1,
            "cpu_time": 208.4,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024",
            "run_type": "iteration",
            "repetitions": 1,
            "threads": 1,
            "iterations": 5010,
            "real_time": 211.9,
            "cpu_time": 210.8,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024",
            "run_type": "iteration",
            "repetitions": 2,
            "threads": 1,
            "iterations": 5005,
            "real_time": 210.4,
            "cpu_time": 210.1,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024_mean",
            "family_index": 0,
            "per_family_instance_index": 0,
            "run_type": "aggregate",
            "aggregate_name": "mean",
            "iterations": 3,
            "real_time": 210.46666666666665,
            "cpu_time": 209.76666666666665,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024_median",
            "family_index": 0,
            "per_family_instance_index": 0,
            "run_type": "aggregate",
            "aggregate_name": "median",
            "iterations": 3,
            "real_time": 210.4,
            "cpu_time": 210.1,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024_stddev",
            "family_index": 0,
            "per_family_instance_index": 0,
            "run_type": "aggregate",
            "aggregate_name": "stddev",
            "iterations": 3,
            "real_time": 1.4,
            "cpu_time": 1.2,
            "time_unit": "ns",
        },
        {
            "name": "BM_Sort/1024_cv",
            "family_index": 0,
            "per_family_instance_index": 0,
            "run_type": "aggregate",
            "aggregate_name": "cv",
            "iterations": 3,
            "real_time": 0.6654557212267083,
            "cpu_time": 0.5720859752601617,
            "time_unit": "ns",
        },
    ],
}


def _convert(payload: dict) -> dict:
    return profile_formats.get("google-benchmark").to_v1(
        json.dumps(payload).encode()
    )


def _parse(payload: dict):
    return parse_evidence(json.dumps(_convert(payload)))


def test_the_runner_output_is_sniffed_from_its_content() -> None:
    data = json.dumps(PLAIN).encode()
    assert detect_profile_format(data[:4096]) == "google-benchmark"
    assert detect_profile_format(b"\xef\xbb\xbf" + data[:4096]) == "google-benchmark"
    assert detect_profile_format(b'{"benchmarks": []}') is None
    fmt = profile_formats.get("google-benchmark")
    assert isinstance(fmt, ProfileFormat) and fmt.read is None


def test_each_benchmark_is_one_series_value_with_its_own_unit() -> None:
    doc = _parse(PLAIN)
    assert doc.kind == "benchmark"
    assert (doc.tool_name, doc.skipped) == ("google-benchmark", {})
    by_name = {e["name"]: e for e in doc.entries}
    assert list(by_name) == ["BM_Sort/1024", "BM_Copy"]
    sort = by_name["BM_Sort/1024"]
    assert (sort["unit"], sort["lower_is_better"], sort["mean"], sort["iterations"]) == (
        "ns", True, 210.5, 5000
    )
    copy = by_name["BM_Copy"]
    assert (copy["unit"], copy["mean"], copy["iterations"]) == ("us", 5.75, 120)
    # The file carries no source path, so no entry claims a function.
    assert all(e["location"] is None for e in doc.entries)


def test_repetition_aggregates_fill_fields_without_duplicate_series() -> None:
    doc = _parse(REPETITIONS)
    entries = doc.entries
    assert [e["name"] for e in entries] == ["BM_Sort/1024"]
    sort = entries[0]
    assert (sort["unit"], sort["lower_is_better"]) == ("ns", True)
    # The last iteration row's timing and count; the median and stddev
    # aggregates fill their own fields, and mean/cv add no series.
    assert sort["mean"] == 210.4
    assert sort["iterations"] == 5005
    assert sort["median"] == 210.4
    assert sort["stddev"] == 1.4
    assert "cv" not in sort


def test_a_file_without_a_benchmarks_list_is_refused() -> None:
    with pytest.raises(EvidenceFormatError):
        _convert({"context": {"num_cpus": 8}})
    with pytest.raises(EvidenceFormatError):
        profile_formats.get("google-benchmark").to_v1(b"not json at all")
    with pytest.raises(EvidenceFormatError):
        _convert({"context": {}, "benchmarks": [{"run_type": "iteration"}]})
