"""Real BenchmarkDotNet 0.14.0 output from ``--exporters json``.

The fixture runs two benchmarks: one with [MemoryDiagnoser] (reporting exact
bytes allocated per op) and one without.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence.add import EvidenceRefusedError, input_format, prepare_evidence
from repowise.core.evidence.format_v1 import EVIDENCE_FORMAT
from repowise.core.evidence.profile_formats import detect_profile_format
from repowise.core.evidence.profile_formats.benchmarkdotnet import to_v1

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "benchmarkdotnet.json"
COMMIT = "a" * 40


def test_the_report_is_sniffed_from_its_content() -> None:
    data = FIXTURE.read_bytes()
    compact = json.dumps(json.loads(data), separators=(",", ":")).encode()
    for sample in (data, b"\xef\xbb\xbf" + data, compact):
        assert detect_profile_format(sample) == "benchmarkdotnet"
    assert detect_profile_format(b"{}") is None
    assert detect_profile_format(b'{"HostEnvironmentInfo": {}}') is None
    assert detect_profile_format(b'{"Benchmarks": []}') is None
    assert detect_profile_format(b'{"Title": "Bench"}') is None
    assert input_format(None, b'{"format": "repowise-evidence-v1", "kind": "benchmark"}') == (
        EVIDENCE_FORMAT
    )


def test_each_benchmark_is_one_series_with_medians_and_exact_bytes(tmp_path: Path) -> None:
    data = FIXTURE.read_bytes()
    prepared = prepare_evidence(data, name="run.json", checkout_root=tmp_path, commit=COMMIT)
    assert (prepared.format, prepared.commit) == ("benchmarkdotnet", COMMIT)
    doc = prepared.doc
    assert doc is not None
    assert (doc.kind, doc.tool_name, doc.skipped) == ("benchmark", "benchmarkdotnet", {})
    assert [entry["name"] for entry in doc.entries] == [
        "Shop.CartBench.Total",
        "Shop.CartBench.Parse",
    ]

    # Benchmark 0: with [MemoryDiagnoser] -> exact bytes_per_op
    total = doc.entries[0]
    assert total["unit"] == "ns"
    assert total["lower_is_better"] is True
    assert total["median"] == 123.45
    assert total["mean"] == 124.0
    assert total["min"] == 120.5
    assert total["max"] == 128.5
    assert total["stddev"] == 2.34
    assert total["rounds"] == 15
    assert total["bytes_per_op"] == 64.0

    # Benchmark 1: without [MemoryDiagnoser] -> bytes_per_op is None
    parse = doc.entries[1]
    assert parse["unit"] == "ns"
    assert parse["lower_is_better"] is True
    assert parse["median"] == 52.3
    assert parse["mean"] == 52.5
    assert parse["min"] == 50.0
    assert parse["max"] == 55.0
    assert parse["stddev"] == 1.5
    assert parse["rounds"] == 15
    assert parse["bytes_per_op"] is None

    converted = json.loads(prepared.text)
    assert converted["tool"] == {"name": "benchmarkdotnet", "version": "0.14.0"}
    assert converted["benchmarks"][0]["function"] == "Shop.CartBench.Total"
    assert converted["benchmarks"][1]["function"] == "Shop.CartBench.Parse"


@pytest.mark.parametrize("data", [b"[]", b"{}", b'{"Benchmarks": []}', b"not JSON"])
def test_an_invalid_report_is_refused_with_a_remedy(data: bytes, tmp_path: Path) -> None:
    with pytest.raises(EvidenceRefusedError) as error:
        prepare_evidence(
            data,
            name="run.json",
            checkout_root=tmp_path,
            commit=COMMIT,
            input_format_name="benchmarkdotnet",
        )
    assert error.value.code == "invalid_evidence"
    assert "BenchmarkDotNet's JsonExporter" in error.value.hint


def test_parameterized_cases_stay_separate_series() -> None:
    data = json.dumps({
        "HostEnvironmentInfo": {"BenchmarkDotNetVersion": "0.14.0"},
        "Benchmarks": [
            {
                "Namespace": "Shop",
                "Type": "CartBench",
                "Method": "Total",
                "FullName": "Shop.CartBench.Total(Size: 10)",
                "Statistics": {"Median": 100.0, "N": 10},
                "Memory": {"BytesAllocatedPerOperation": 32},
            },
            {
                "Namespace": "Shop",
                "Type": "CartBench",
                "Method": "Total",
                "FullName": "Shop.CartBench.Total(Size: 100)",
                "Statistics": {"Median": 500.0, "N": 10},
                "Memory": {"BytesAllocatedPerOperation": 128},
            },
        ],
    }).encode()
    v1 = to_v1(data)
    entries = v1["benchmarks"]
    assert len(entries) == 2
    assert entries[0]["name"] == "Shop.CartBench.Total(Size: 10)"
    assert entries[0]["function"] == "Shop.CartBench.Total"
    assert entries[0]["bytes_per_op"] == 32
    assert entries[0]["median"] == 100.0

    assert entries[1]["name"] == "Shop.CartBench.Total(Size: 100)"
    assert entries[1]["function"] == "Shop.CartBench.Total"
    assert entries[1]["bytes_per_op"] == 128
    assert entries[1]["median"] == 500.0
