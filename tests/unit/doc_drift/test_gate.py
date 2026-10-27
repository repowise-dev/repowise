"""The CI gate: threshold, baseline, and the shape it hands a caller."""

from __future__ import annotations

import json

from repowise.core.analysis.doc_drift.constants import HIGH_CONFIDENCE_THRESHOLD
from repowise.core.analysis.doc_drift.gate import GateResult, evaluate_gate
from repowise.core.analysis.doc_drift.serialize import derive_doc_drift_fingerprint


def _f(target: str, confidence: float, *, doc: str = "docs/a.md", line: int = 3) -> dict:
    return {
        "file_path": doc,
        "line_number": line,
        "kind": "path",
        "target": target,
        "confidence": confidence,
        "origin": "path_no_candidate",
        "reason": f"Document names {target}, which no longer exists.",
        "raw": target,
        "context": "",
    }


def test_default_threshold_is_the_high_tier():
    result = evaluate_gate([_f("a.py", 0.9), _f("b.py", 0.5)])
    assert result.fail_on == HIGH_CONFIDENCE_THRESHOLD
    assert not result.passed
    assert [f["target"] for f in result.failing] == ["a.py"]
    assert result.below_threshold == 1


def test_threshold_is_inclusive():
    assert not evaluate_gate([_f("a.py", 0.7)], fail_on=0.7).passed
    assert evaluate_gate([_f("a.py", 0.69)], fail_on=0.7).passed


def test_no_findings_passes():
    result = evaluate_gate([])
    assert result.passed
    assert result.failing == [] and result.baselined == [] and result.below_threshold == 0


def test_baseline_accepts_a_finding_whatever_its_line():
    fp = derive_doc_drift_fingerprint("docs/a.md", "path", "a.py")
    result = evaluate_gate(
        [_f("a.py", 0.9, line=40), _f("b.py", 0.9)], baseline=frozenset({fp})
    )
    assert [f["target"] for f in result.baselined] == ["a.py"]
    assert [f["target"] for f in result.failing] == ["b.py"]


def test_fully_baselined_run_passes():
    fp = derive_doc_drift_fingerprint("docs/a.md", "path", "a.py")
    assert evaluate_gate([_f("a.py", 0.9)], baseline=frozenset({fp})).passed


def test_a_carried_fingerprint_is_used_as_is():
    row = _f("a.py", 0.9) | {"fingerprint": "custom"}
    assert evaluate_gate([row], baseline=frozenset({"custom"})).passed


def test_input_dicts_are_not_mutated():
    row = _f("a.py", 0.9)
    evaluate_gate([row])
    assert "fingerprint" not in row


def test_to_dict_is_stable_json():
    result = evaluate_gate([_f("a.py", 0.9), _f("b.py", 0.1)], fail_on=0.5)
    out = result.to_dict()
    assert out == {
        "passed": False,
        "fail_on": 0.5,
        "failing_count": 1,
        "baselined_count": 0,
        "below_threshold_count": 1,
        "failing": [result.failing[0]],
    }
    assert out["failing"][0]["fingerprint"]
    json.dumps(out)


def test_result_is_frozen():
    result = evaluate_gate([])
    assert isinstance(result, GateResult)
    try:
        result.passed = False  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("GateResult must be frozen")
