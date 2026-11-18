"""hyperfine JSON: sniffed, written as a v1 benchmark document, never a profile.

The fixture follows the layout of ``hyperfine --export-json`` (hyperfine 1.x,
pretty-printed) over two commands, the second with a ``--parameter-list`` value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence import parse_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError
from repowise.core.evidence.profile_formats import detect_profile_format, profile_formats

FIXTURE = Path(__file__).parents[2] / "fixtures" / "evidence" / "hyperfine.json"


def _convert(data: bytes) -> dict:
    return profile_formats.get("hyperfine").to_v1(data)


def _read(data: dict):
    return parse_evidence(json.dumps(_convert(json.dumps(data).encode())))


def test_the_tool_output_is_sniffed_from_its_content() -> None:
    data = FIXTURE.read_bytes()
    assert detect_profile_format(data[:4096]) == "hyperfine"
    assert detect_profile_format(b"\xef\xbb\xbf" + data[:4096]) == "hyperfine"
    assert detect_profile_format(json.dumps(json.loads(data), separators=(",", ":")).encode()) == (
        "hyperfine"
    )
    assert profile_formats.get("hyperfine").read is None


def test_a_file_cut_off_before_its_timings_end_is_still_sniffed() -> None:
    long_run = json.loads(FIXTURE.read_text())
    long_run["results"][0]["times"] = [0.3] * 2000
    head = json.dumps(long_run, indent=2).encode()[:4096]
    assert b"exit_codes" not in head
    assert detect_profile_format(head) == "hyperfine"


def test_a_very_long_first_command_does_not_hide_the_format() -> None:
    data = json.loads(FIXTURE.read_text())
    data["results"][0]["command"] = "./build/app " + "--flag " * 1000
    head = json.dumps(data, indent=2).encode()[:4096]
    assert b'"mean"' not in head
    assert detect_profile_format(head) == "hyperfine"


def test_other_json_is_not_claimed() -> None:
    assert detect_profile_format(b'{"results": []}') is None
    assert detect_profile_format(b'{"results": [{"name": "a", "mean": 1}]}') is None
    assert detect_profile_format(b'{"benchmarks": [{"command": "a", "mean": 1}]}') is None
    assert detect_profile_format(b'{"results": [{"cmd": "a", "mean": 1}]}') is None
    assert detect_profile_format(b'{"results": [["command", "mean"]]}') is None
    assert detect_profile_format(b'{"data": {"results": [{"command": "a"}]}}') is None


def test_each_result_is_one_series_value_in_seconds() -> None:
    doc = parse_evidence(json.dumps(_convert(FIXTURE.read_bytes())))
    assert (doc.kind, doc.commit_sha, doc.tool_name, doc.skipped) == (
        "benchmark", None, "hyperfine", {},
    )
    raw = json.loads(FIXTURE.read_text())["results"]
    assert [e["name"] for e in doc.entries] == ["command 1", "command 2"]
    for entry, source in zip(doc.entries, raw, strict=True):
        assert (entry["unit"], entry["lower_is_better"], entry["location"]) == ("s", True, None)
        for stat in ("min", "max", "mean", "median", "stddev"):
            assert entry[stat] == source[stat]
        assert entry["rounds"] == len(source["times"])
        assert entry["iterations"] is None


def test_a_name_the_file_carries_names_the_series() -> None:
    data = json.loads(FIXTURE.read_text())
    data["results"][1]["command_name"] = "slow path"
    data["results"][0]["command_name"] = "  "
    assert [e["name"] for e in _read(data).entries] == ["command 1", "slow path (2)"]


def test_a_name_never_collides_with_a_position_or_another_name() -> None:
    data = json.loads(FIXTURE.read_text())
    data["results"][0]["command_name"] = "command 2"
    data["results"].append({**data["results"][1], "command_name": "command 2"})
    data["results"].append({**data["results"][1], "command_name": "command 2"})
    doc = _read(data)
    assert doc.skipped == {}
    names = [e["name"] for e in doc.entries]
    assert len(names) == len(set(names)) == 4
    assert names[0] == "command 2 (1)"
    assert names[1] == "command 2"


def test_no_command_line_or_parameter_reaches_the_document() -> None:
    data = json.loads(FIXTURE.read_text())
    data["results"][0]["command"] = "./build/app --token hunter2"
    text = json.dumps(_convert(json.dumps(data).encode()))
    for leaked in ("hunter2", "build/app", "sleep", "duration", "exit_codes", "parameters"):
        assert leaked not in text


def test_a_result_without_timings_is_skipped_and_counted() -> None:
    data = json.loads(FIXTURE.read_text())
    del data["results"][0]["mean"], data["results"][0]["median"]
    for stat in ("min", "max", "stddev"):
        del data["results"][0][stat]
    data["results"].append("odd")
    doc = _read(data)
    assert [e["name"] for e in doc.entries] == ["command 2"]
    assert doc.skipped == {"no_value": 1, "not_an_object": 1}


def test_a_failed_run_without_a_spread_keeps_what_it_measured() -> None:
    data = {"results": [{"command": "x", "mean": 1.5, "stddev": None, "median": 1.5,
                         "min": 1.5, "max": 1.5, "times": [1.5], "exit_codes": [1]}]}
    (entry,) = _read(data).entries
    assert (entry["mean"], entry["stddev"], entry["rounds"]) == (1.5, None, 1)


def test_a_file_that_is_not_the_tool_output_is_refused() -> None:
    with pytest.raises(EvidenceFormatError, match="hyperfine"):
        _convert(b'{"results": {}}')
    with pytest.raises(EvidenceFormatError):
        _convert(b"not json")
