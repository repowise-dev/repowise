"""Google Benchmark JSON: sniffed, written as a v1 benchmark document, never a profile.

The fixtures follow the layout of ``--benchmark_format=json`` (Google Benchmark
v1.x): a plain run over two benchmarks with different ``time_unit`` values,
and a ``--benchmark_repetitions=3`` run whose aggregate rows must fold into
the same series instead of adding new ones.
"""

from __future__ import annotations

import json

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats

PLAIN_RUN = {
    "context": {"date": "2026-11-19 12:00:00", "num_cpus": 8, "mhz_per_cpu": 3500},
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
            "name": "BM_Hash/64",
            "run_type": "iteration",
            "iterations": 1200000,
            "real_time": 480.2,
            "cpu_time": 478.9,
            "time_unit": "us",
        },
    ],
}

REPETITIONS_RUN = {
    "context": {"date": "2026-11-19 12:01:00", "num_cpus": 8},
    "benchmarks": [
        {"name": "BM_Sort/1024", "run_type": "iteration", "iterations": 5000,
         "real_time": 210.5, "cpu_time": 209.8, "time_unit": "ns"},
        {"name": "BM_Sort/1024", "run_type": "iteration", "iterations": 4900,
         "real_time": 214.0, "cpu_time": 213.0, "time_unit": "ns"},
        {"name": "BM_Sort/1024", "run_type": "iteration", "iterations": 4950,
         "real_time": 212.0, "cpu_time": 211.5, "time_unit": "ns"},
        {"name": "BM_Sort/1024/iteration", "run_type": "aggregate", "aggregate_name": None},
        {"name": "BM_Sort/1024/mean", "run_type": "aggregate", "aggregate_name": "mean",
         "iterations": 3, "real_time": 212.2, "cpu_time": 211.4, "time_unit": "ns"},
        {"name": "BM_Sort/1024/median", "run_type": "aggregate", "aggregate_name": "median",
         "iterations": 3, "real_time": 212.0, "cpu_time": 211.5, "time_unit": "ns"},
        {"name": "BM_Sort/1024/stddev", "run_type": "aggregate", "aggregate_name": "stddev",
         "iterations": 3, "real_time": 1.8, "cpu_time": 1.6, "time_unit": "ns"},
        {"name": "BM_Sort/1024/cv", "run_type": "aggregate", "aggregate_name": "cv",
         "iterations": 3, "real_time": 0.8, "cpu_time": 0.7, "time_unit": "ns"},
    ],
}


def _convert(data: bytes) -> dict:
    return profile_formats.get("google-benchmark").to_v1(data)


def _dump(run: dict) -> bytes:
    return json.dumps(run, indent=2).encode()


def _read(run: dict):
    return parse_evidence(json.dumps(_convert(_dump(run))))


def test_the_tool_output_is_sniffed_from_its_content() -> None:
    data = _dump(PLAIN_RUN)
    assert detect_profile_format(data[:4096]) == "google-benchmark"
    assert detect_profile_format(b"\xef\xbb\xbf" + data[:4096]) == "google-benchmark"
    assert detect_profile_format(json.dumps(PLAIN_RUN, separators=(",", ":")).encode()) == (
        "google-benchmark"
    )
    assert profile_formats.get("google-benchmark").read is None


def test_a_context_of_host_detail_still_leaves_the_format_sniffed() -> None:
    run = {**PLAIN_RUN, "context": {f"field_{i}": "x" * 40 for i in range(40)}}
    assert len(json.dumps(run["context"])) > 1500
    assert detect_profile_format(_dump(run)[:4096]) == "google-benchmark"


def test_a_plain_run_is_one_series_per_benchmark_with_its_own_unit() -> None:
    doc = _read(PLAIN_RUN)
    assert [entry["name"] for entry in doc.entries] == ["BM_Sort/1024", "BM_Hash/64"]
    sort_, hash_ = doc.entries
    assert sort_["unit"] == "ns"
    assert sort_["mean"] == 210.5
    assert sort_["iterations"] == 5000
    assert sort_["lower_is_better"] is True
    assert hash_["unit"] == "us"
    assert hash_["mean"] == 480.2
    assert hash_["iterations"] == 1200000
    assert all(entry["location"] is None for entry in doc.entries)


def test_repetition_aggregates_fold_into_the_same_series() -> None:
    doc = _read(REPETITIONS_RUN)
    assert [entry["name"] for entry in doc.entries] == ["BM_Sort/1024"]
    (entry,) = doc.entries
    assert entry["median"] == 212.0
    assert entry["stddev"] == 1.8
    # The last repetition's row is the value; the skipped aggregates add nothing.
    assert entry["mean"] == 212.0
    assert entry["iterations"] == 4950
    assert entry["unit"] == "ns"


def test_a_file_without_a_benchmarks_list_is_refused() -> None:
    for data in (b"not json", json.dumps({"context": {}}).encode(), b"[]"):
        try:
            _convert(data)
        except EvidenceFormatError as error:
            assert "Google Benchmark" in str(error)
        else:  # pragma: no cover - the refusal is the test
            raise AssertionError("the file was accepted")
