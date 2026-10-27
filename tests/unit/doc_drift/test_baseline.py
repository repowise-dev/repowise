"""The baseline file: round trip, deterministic bytes, and refusals."""

from __future__ import annotations

import json

import pytest

from repowise.core.analysis.doc_drift.baseline import (
    BaselineError,
    build_baseline,
    read_baseline,
    write_baseline,
)
from repowise.core.analysis.doc_drift.gate import evaluate_gate
from repowise.core.analysis.doc_drift.serialize import derive_doc_drift_fingerprint


def _f(doc: str, kind: str, target: str, line: int = 1) -> dict:
    return {
        "file_path": doc,
        "line_number": line,
        "kind": kind,
        "target": target,
        "confidence": 0.9,
        "origin": "path_no_candidate",
        "reason": "r",
        "raw": target,
        "context": "",
    }


FINDINGS = [
    _f("docs/z.md", "path", "b.py"),
    _f("docs/a.md", "path", "ü.py"),
    _f("docs/a.md", "anchor", "README.md#x"),
    _f("docs/a.md", "path", "ü.py", line=9),  # same fingerprint, other line
]


def test_round_trip_accepts_what_it_wrote(tmp_path):
    path = tmp_path / "drift-baseline.json"
    assert write_baseline(path, FINDINGS) == 3
    accepted = read_baseline(path)
    assert accepted == {
        derive_doc_drift_fingerprint(f["file_path"], f["kind"], f["target"])
        for f in FINDINGS
    }
    assert evaluate_gate(FINDINGS, baseline=accepted).passed


def test_entries_are_sorted_and_bytes_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_baseline(a, FINDINGS)
    write_baseline(b, list(reversed(FINDINGS)))
    assert a.read_bytes() == b.read_bytes()
    text = a.read_text(encoding="utf-8")
    assert text.endswith("}\n") and "ü.py" in text
    doc = json.loads(text)
    assert doc["version"] == 1 and doc["basis"]
    keys = [(e["file_path"], e["kind"], e["target"]) for e in doc["entries"]]
    assert keys == sorted(keys)
    assert set(doc["entries"][0]) == {"fingerprint", "file_path", "kind", "target"}


def test_empty_baseline_is_valid(tmp_path):
    path = tmp_path / "b.json"
    assert write_baseline(path, []) == 0
    assert read_baseline(path) == frozenset()
    assert build_baseline([])["entries"] == []


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ("not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        ('{"version": 2, "entries": []}', "version 2"),
        ('{"entries": []}', "version None"),
        ('{"version": 1}', "entries"),
        ('{"version": 1, "entries": [{"file_path": "a"}]}', "entry 0"),
        ('{"version": 1, "entries": ["x"]}', "entry 0"),
    ],
)
def test_invalid_files_say_what_is_wrong(tmp_path, content, needle):
    path = tmp_path / "b.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BaselineError, match=needle):
        read_baseline(path)


def test_missing_file_is_a_baseline_error(tmp_path):
    with pytest.raises(BaselineError, match="cannot read"):
        read_baseline(tmp_path / "nope.json")


def test_baseline_error_is_a_value_error():
    assert issubclass(BaselineError, ValueError)
