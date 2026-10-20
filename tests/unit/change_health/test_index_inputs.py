"""A few files scored from bytes read the index's history, tests and dependents."""

from __future__ import annotations

from repowise.core.analysis.change_health.analyzer import RevisionHealthAnalyzer
from repowise.core.analysis.change_health.index_inputs import IndexInputs

from .conftest import python_complex

_PATH = "src/hot.py"


def _kinds(index: IndexInputs | None) -> set[str]:
    run = RevisionHealthAnalyzer().analyze(
        {_PATH: python_complex("run", 3).encode()},
        subject_paths={_PATH},
        dependents={_PATH: 12},
        index=index,
    )
    return {f.biomarker_type for f in run.findings}


def test_index_history_reaches_the_score_and_its_test_pairing_holds():
    history = {_PATH: {"file_path": _PATH, "is_hotspot": True}}
    assert "untested_hotspot" not in _kinds(None)
    assert "untested_hotspot" in _kinds(IndexInputs(git_meta=history))
    # The index pairs a test the pass never saw, so the file is not untested.
    paired = IndexInputs(git_meta=history, tested_files=frozenset({_PATH}))
    assert "untested_hotspot" not in _kinds(paired)


def test_stored_dry_violation_replayed_when_partner_not_passed():
    import json
    from types import SimpleNamespace

    stored = SimpleNamespace(
        biomarker_type="dry_violation",
        severity="medium",
        function_name=None,
        line_start=1,
        line_end=5,
        details_json=json.dumps({"worst_clone_partner": "src/partner.py", "worst_clone_lines": 5}),
        health_impact=0.5,
        reason="clone",
    )
    index = IndexInputs(dry_findings={_PATH: [stored]})
    assert "dry_violation" in _kinds(index)
