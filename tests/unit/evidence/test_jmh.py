"""Real JMH 1.37 output from ``java -jar benchmarks.jar -rf json -rff jmh.json``.

The fixture runs two benchmarks: a throughput benchmark (ops/s, higher is better)
and an average time benchmark (ns/op -> ns, lower is better).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence.add import EvidenceRefusedError, input_format, prepare_evidence
from repowise.core.evidence.format_v1 import EVIDENCE_FORMAT
from repowise.core.evidence.profile_formats import detect_profile_format
from repowise.core.evidence.profile_formats.jmh import to_v1

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "jmh.json"
COMMIT = "a" * 40


def test_the_report_is_sniffed_from_its_content() -> None:
    data = FIXTURE.read_bytes()
    compact = json.dumps(json.loads(data), separators=(",", ":")).encode()
    for sample in (data, b"\xef\xbb\xbf" + data, compact):
        assert detect_profile_format(sample) == "jmh"
    assert detect_profile_format(b'[]') is None
    assert detect_profile_format(b'[{"other": 1}]') is None
    assert detect_profile_format(b'[{"benchmark": "com.acme.Bench"}]') is None
    assert detect_profile_format(b'{"jmhVersion": "1.37", "benchmark": "com.acme.Bench"}') is None
    assert input_format(None, b'{"format": "repowise-evidence-v1", "kind": "benchmark"}') == (
        EVIDENCE_FORMAT
    )


def test_each_benchmark_is_one_series_with_direction_and_unit(tmp_path: Path) -> None:
    data = FIXTURE.read_bytes()
    prepared = prepare_evidence(data, name="run.json", checkout_root=tmp_path, commit=COMMIT)
    assert (prepared.format, prepared.commit) == ("jmh", COMMIT)
    doc = prepared.doc
    assert doc is not None
    assert (doc.kind, doc.tool_name, doc.skipped) == ("benchmark", "jmh", {})
    assert [entry["name"] for entry in doc.entries] == [
        "com.acme.CartBench.throughput:thrpt",
        "com.acme.CartBench.latency:avgt",
    ]

    # Entry 0: throughput (thrpt)
    thrpt = doc.entries[0]
    assert thrpt["unit"] == "ops/s"
    assert thrpt["lower_is_better"] is False
    assert thrpt["mean"] == 10500.0
    assert thrpt["median"] == 10500.0
    assert thrpt["rounds"] == 5
    assert thrpt["iterations"] == 5

    # Entry 1: average time (avgt)
    avgt = doc.entries[1]
    assert avgt["unit"] == "ns"
    assert avgt["lower_is_better"] is True
    assert avgt["mean"] == 42.5
    assert avgt["median"] == 42.0
    assert avgt["min"] == 40.0
    assert avgt["max"] == 46.0
    assert avgt["rounds"] == 5
    assert avgt["iterations"] == 5

    converted = json.loads(prepared.text)
    assert converted["tool"] == {"name": "jmh", "version": "1.37"}
    assert converted["benchmarks"][0]["function"] == "com.acme.CartBench.throughput"
    assert converted["benchmarks"][1]["function"] == "com.acme.CartBench.latency"


@pytest.mark.parametrize("data", [b"{}", b"[]", b'[{"other": 1}]', b"not JSON"])
def test_an_invalid_report_is_refused_with_a_remedy(data: bytes, tmp_path: Path) -> None:
    with pytest.raises(EvidenceRefusedError) as error:
        prepare_evidence(
            data,
            name="run.json",
            checkout_root=tmp_path,
            commit=COMMIT,
            input_format_name="jmh",
        )
    assert error.value.code == "invalid_evidence"
    assert "java -jar benchmarks.jar -rf json" in error.value.hint


def test_parameterized_benchmark_names_include_sorted_params() -> None:
    data = json.dumps([
        {
            "jmhVersion": "1.37",
            "benchmark": "com.acme.SortBench.sort",
            "mode": "avgt",
            "params": {"size": "100", "algo": "quick"},
            "primaryMetric": {"score": 15.0, "scoreUnit": "us/op"},
        }
    ]).encode()
    v1 = to_v1(data)
    entry = v1["benchmarks"][0]
    assert entry["name"] == "com.acme.SortBench.sort:avgt(algo=quick,size=100)"
    assert entry["unit"] == "us"
    assert entry["lower_is_better"] is True
    assert entry["function"] == "com.acme.SortBench.sort"


def test_modes_and_unit_conversion() -> None:
    data = json.dumps([
        {
            "jmhVersion": "1.37",
            "benchmark": "com.acme.Bench.sampleTest",
            "mode": "sample",
            "primaryMetric": {"score": 2.5, "scoreUnit": "ms/op"},
        },
        {
            "jmhVersion": "1.37",
            "benchmark": "com.acme.Bench.singleShot",
            "mode": "ss",
            "primaryMetric": {"score": 1.2, "scoreUnit": "s/op"},
        },
        {
            "jmhVersion": "1.37",
            "benchmark": "com.acme.Bench.opsPerMs",
            "mode": "thrpt",
            "primaryMetric": {"score": 500.0, "scoreUnit": "ops/ms"},
        },
    ]).encode()
    v1 = to_v1(data)
    entries = v1["benchmarks"]
    assert (entries[0]["unit"], entries[0]["lower_is_better"]) == ("ms", True)
    assert (entries[1]["unit"], entries[1]["lower_is_better"]) == ("s", True)
    assert (entries[2]["unit"], entries[2]["lower_is_better"]) == ("ops/ms", False)
