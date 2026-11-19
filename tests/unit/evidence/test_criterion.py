"""Criterion ``estimates.json``: sniffed, written as a v1 benchmark document, never a profile.

The fixture follows the layout Criterion writes under
``target/criterion/<group>/<id>/new/``: ``estimates.json`` with the five
estimate keys, and the sibling ``benchmark.json`` that names the run.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats

FIXTURES = Path(__file__).parents[2] / "fixtures" / "evidence"
ESTIMATES = FIXTURES / "criterion_estimates.json"
BENCHMARK = FIXTURES / "criterion_benchmark.json"


def _convert(data: bytes, name: str | None = None) -> dict:
    return profile_formats.get("criterion").to_v1(data, name)


def _read(data: bytes, name: str | None = None):
    return parse_evidence(json.dumps(_convert(data, name)))


def _named(tmp_path: Path) -> str:
    """The fixture pair laid out the way Criterion writes it."""
    estimates = tmp_path / "new" / "estimates.json"
    estimates.parent.mkdir(parents=True)
    estimates.write_bytes(ESTIMATES.read_bytes())
    (estimates.parent / "benchmark.json").write_bytes(BENCHMARK.read_bytes())
    return str(estimates)


def test_the_tool_output_is_sniffed_from_its_content() -> None:
    data = ESTIMATES.read_bytes()
    assert detect_profile_format(data) == "criterion"
    assert detect_profile_format(b"\xef\xbb\xbf" + data) == "criterion"
    assert detect_profile_format(json.dumps(json.loads(data), separators=(",", ":")).encode()) == (
        "criterion"
    )
    assert profile_formats.get("criterion").read is None


def test_other_json_is_not_claimed() -> None:
    assert detect_profile_format(b'{"mean": {"point_estimate": 1}}') is None
    assert detect_profile_format(b'{"results": []}') is None
    assert detect_profile_format(b"not json") is None


def test_the_entry_takes_its_values_from_the_point_estimates(tmp_path: Path) -> None:
    doc = _read(ESTIMATES.read_bytes(), _named(tmp_path))
    assert (doc.kind, doc.tool_name, doc.skipped) == ("benchmark", "criterion", {})
    entry = doc.entries[0]
    estimates = json.loads(ESTIMATES.read_text())
    assert entry["name"] == "sort/1024"
    assert (entry["unit"], entry["lower_is_better"], entry["location"]) == ("ns", True, None)
    assert entry["mean"] == estimates["mean"]["point_estimate"]
    assert entry["median"] == estimates["median"]["point_estimate"]
    assert entry["stddev"] == estimates["std_dev"]["point_estimate"]


def test_a_null_slope_does_not_fail_the_file() -> None:
    data = json.loads(ESTIMATES.read_text())
    data["slope"] = None
    doc = _read(json.dumps(data).encode())
    assert doc.entries[0]["mean"] is not None


def test_a_missing_sibling_falls_back_to_a_fixed_name() -> None:
    doc = _read(ESTIMATES.read_bytes())
    assert doc.entries[0]["name"] == "criterion"
    doc = _read(ESTIMATES.read_bytes(), str(FIXTURES / "nowhere.json"))
    assert doc.entries[0]["name"] == "criterion"


def test_a_sibling_without_a_full_id_falls_back_to_a_fixed_name(tmp_path: Path) -> None:
    estimates = tmp_path / "estimates.json"
    estimates.write_bytes(ESTIMATES.read_bytes())
    (tmp_path / "benchmark.json").write_text(json.dumps({"full_id": ""}))
    doc = _read(ESTIMATES.read_bytes(), str(estimates))
    assert doc.entries[0]["name"] == "criterion"


def test_the_sibling_is_read_from_the_laid_out_path(tmp_path: Path) -> None:
    doc = _read(ESTIMATES.read_bytes(), _named(tmp_path))
    assert doc.entries[0]["name"] == "sort/1024"
