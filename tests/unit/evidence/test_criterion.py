"""Real Criterion 0.5.1 sort results, with flat (32) and linear (1024) sampling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.add import prepare_evidence_files
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats

FIXTURES = Path(__file__).parents[2] / "fixtures" / "evidence" / "criterion" / "sort"


def test_a_criterion_file_is_detected_from_its_five_top_level_keys() -> None:
    data = (FIXTURES / "1024/new/estimates.json").read_bytes()
    assert detect_profile_format(data) == "criterion"
    assert detect_profile_format(b"\xef\xbb\xbf" + data) == "criterion"
    estimates = json.loads(data)
    assert detect_profile_format(json.dumps({"other": estimates}).encode()) is None
    del estimates["slope"]
    assert detect_profile_format(json.dumps(estimates).encode()) is None


@pytest.mark.parametrize("size", [32, 1024])
def test_samples_with_and_without_a_slope_keep_only_the_three_point_estimates(size: int) -> None:
    path = FIXTURES / str(size) / "new/estimates.json"
    data = path.read_bytes()
    raw = json.loads(data)
    assert (raw["slope"] is None) == (size == 32)
    assert detect_profile_format(data) == "criterion"
    prepared = prepare_evidence_files([(path, data)], checkout_root=path.parent, commit="a" * 40)
    doc = prepared.doc
    assert doc is not None
    assert (prepared.format, doc.kind, doc.tool_name, doc.skipped) == (
        "criterion", "benchmark", "criterion", {},
    )
    (entry,) = doc.entries
    assert (entry["name"], entry["unit"], entry["lower_is_better"], entry["location"]) == (
        f"sort/{size}", "ns", True, None,
    )
    assert (entry["mean"], entry["median"], entry["stddev"]) == (
        raw["mean"]["point_estimate"],
        raw["median"]["point_estimate"],
        raw["std_dev"]["point_estimate"],
    )
    assert entry["min"] is entry["max"] is None


@pytest.mark.parametrize("metadata", [None, b"bad JSON", b'{"full_id": " ", "title": "sort"}'])
def test_missing_or_unusable_benchmark_metadata_uses_a_fixed_name(
    tmp_path: Path, metadata: bytes | None,
) -> None:
    path = tmp_path / "renamed.json"
    data = (FIXTURES / "32/new/estimates.json").read_bytes()
    path.write_bytes(data)
    if metadata is not None:
        path.with_name("benchmark.json").write_bytes(metadata)
    prepared = prepare_evidence_files([(path, data)], checkout_root=tmp_path, commit="a" * 40)
    assert prepared.doc is not None
    assert [e["name"] for e in prepared.doc.entries] == ["criterion benchmark"]


def test_a_display_name_never_reads_local_metadata() -> None:
    path = FIXTURES / "1024/new/estimates.json"
    data = path.read_bytes()
    assert detect_profile_format(data) == "criterion"
    prepared = prepare_evidence_files([(str(path), data)], checkout_root=path.parent, commit="a" * 40)
    assert prepared.doc is not None
    convert = profile_formats.get("criterion").to_v1
    assert convert is not None
    doc = parse_evidence(json.dumps(convert(data)))
    assert doc.entries == prepared.doc.entries
    assert [e["name"] for e in doc.entries] == ["criterion benchmark"]
    with pytest.raises(EvidenceFormatError, match="Criterion"):
        convert(path.with_name("benchmark.json").read_bytes())
