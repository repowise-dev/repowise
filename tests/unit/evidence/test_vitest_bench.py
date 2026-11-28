"""Real Vitest 4.0.18 output from ``vitest bench --run --outputJson=vitest.json``.

The fixture runs two sorts in a suite and its nested suite. Only the
absolute directory in ``filepath`` was replaced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence.add import EvidenceRefusedError, input_format, prepare_evidence
from repowise.core.evidence.format_v1 import EVIDENCE_FORMAT
from repowise.core.evidence.profile_formats import detect_profile_format

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "vitest_bench.json"
COMMIT = "a" * 40


def test_the_report_is_sniffed_from_its_content() -> None:
    data = FIXTURE.read_bytes()
    compact = json.dumps(json.loads(data), separators=(",", ":")).encode()
    for sample in (data, b"\xef\xbb\xbf" + data, compact):
        assert detect_profile_format(sample) == "vitest-bench"
    assert detect_profile_format(b'{"files": [{"path": "sort.bench.ts"}]}') is None
    assert input_format(None, b'{"format": "repowise-evidence-v1", "kind": "benchmark"}') == (
        EVIDENCE_FORMAT
    )


def test_each_benchmark_is_one_series_value_in_milliseconds(tmp_path: Path) -> None:
    data = FIXTURE.read_bytes()
    prepared = prepare_evidence(data, name="run.json", checkout_root=tmp_path, commit=COMMIT)
    assert (prepared.format, prepared.commit) == ("vitest-bench", COMMIT)
    doc = prepared.doc
    assert doc is not None
    assert (doc.kind, doc.tool_name, doc.skipped) == ("benchmark", "vitest-bench", {})
    assert [entry["name"] for entry in doc.entries] == [
        "sorting.bench.mjs > sorting > numeric",
        "sorting.bench.mjs > sorting > nested > default",
    ]

    groups = json.loads(data)["files"][0]["groups"]
    for entry, group in zip(doc.entries, groups, strict=True):
        source = group["benchmarks"][0]
        assert (entry["unit"], entry["lower_is_better"], entry["location"]) == ("ms", True, None)
        for stat in ("mean", "median", "min", "max"):
            assert entry[stat] == source[stat]
        assert entry["rounds"] == source["sampleCount"]

    converted = json.loads(prepared.text)["benchmarks"]
    assert all(
        set(entry) == {"name", "unit", "lower_is_better", "mean", "median", "min", "max", "rounds"}
        for entry in converted
    )


@pytest.mark.parametrize("data", [b"{}", b'{"files": {}}', b"not JSON"])
def test_an_invalid_report_is_refused_with_a_remedy(data: bytes, tmp_path: Path) -> None:
    with pytest.raises(EvidenceRefusedError, match='no "files" list') as error:
        prepare_evidence(
            data,
            name="run.json",
            checkout_root=tmp_path,
            commit=COMMIT,
            input_format_name="vitest-bench",
        )
    assert error.value.code == "invalid_evidence"
    assert "vitest bench --outputJson=PATH" in error.value.hint


def test_a_repeated_name_keeps_each_series(tmp_path: Path) -> None:
    data = json.loads(FIXTURE.read_bytes())
    file = data["files"][0]
    group = file["groups"][0]
    original = group["benchmarks"][0]
    group["benchmarks"].append({**original, "mean": 2.5})
    data["files"].append({
        "filepath": "/other/sorting.bench.mjs",
        "groups": [{
            "fullName": group["fullName"],
            "benchmarks": [{**original, "name": "numeric (2)"}, {**original, "mean": 3.5}],
        }],
    })

    prepared = prepare_evidence(
        json.dumps(data).encode(), name="run.json", checkout_root=tmp_path, commit=COMMIT,
    )
    doc = prepared.doc
    assert doc is not None
    assert doc.skipped == {}
    assert [(entry["name"], entry["mean"]) for entry in doc.entries] == [
        ("sorting.bench.mjs > sorting > numeric", original["mean"]),
        ("sorting.bench.mjs > sorting > numeric (3)", 2.5),
        ("sorting.bench.mjs > sorting > nested > default",
         file["groups"][1]["benchmarks"][0]["mean"]),
        ("sorting.bench.mjs > sorting > numeric (2)", original["mean"]),
        ("sorting.bench.mjs > sorting > numeric (4)", 3.5),
    ]
