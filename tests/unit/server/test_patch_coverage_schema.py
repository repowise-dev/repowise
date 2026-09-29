"""The REST model is ``PatchCoverage.to_dict()``, key for key, so they cannot drift."""

from __future__ import annotations

from dataclasses import replace

from repowise.core.analysis.health.coverage import PathGate, file_coverage
from repowise.core.analysis.patch_coverage import (
    FileRisk,
    IndirectCause,
    IndirectChange,
    PatchScope,
    ProjectDelta,
    ProjectTotals,
    TestHint,
    attach_hints,
    attach_risk,
    compute_patch_coverage,
)
from repowise.server.schemas.patch_coverage import PatchCoverageResponse


def test_every_to_dict_key_round_trips_through_the_response_model() -> None:
    coverage = {
        "a.py": file_coverage("a.py", [1], [1, 2, 3]),
        "b.py": file_coverage("b.py", [1], []),
        "c.py": file_coverage("c.py", [1], [1]),
    }
    changed = {
        "a.py": {1, 2, 3},
        "b.py": {1},
        "c.py": {9},
        "new.py": {1},
        "t/test_x.py": {1},
        "gen/out.py": {1},
    }
    pc = compute_patch_coverage(
        changed,
        coverage,
        threshold=80,
        min_coverable_lines=5,
        ignore=["gen/"],
        gates=[
            PathGate("a", ("a.py",), 80),
            PathGate("none", ("docs/",), None, informational=True),
        ],
        scope=PatchScope(
            label="main...HEAD",
            source_formats=("lcov",),
            reports=("lcov.info",),
            report_path_count=4,
            unmatched_report_path_count=1,
            mapping_partial=True,
            measured_commit="abc",
            freshness="stale",
            config_errors=("coverage.gates[2]: must be a mapping with name and paths.",),
        ),
    )
    wire = pc.to_dict()

    assert {f["status"] for f in wire["files"]} == {
        "measured",
        "no_line_data",
        "no_coverable_changes",
        "not_in_report",
    }
    assert (wire["gate"], wire["scope"]["ignored_file_count"]) == ("too_small", 1)
    assert [g["gate"] for g in wire["path_gates"]] == ["too_small", "not_set"]
    assert PatchCoverageResponse.model_validate(wire).model_dump() == wire

    # With risk attached, each row and the risky-file summary round-trip too.
    risks = {
        "a.py": FileRisk(2.5, 14, True, False, "git_and_index", True, ("hotspot",)),
        "new.py": FileRisk(fix_pressure=0.0, basis="git"),
    }
    risked = attach_risk(pc, risks, risky_threshold=90).to_dict()
    # 1 of 3 risky lines misses 90%, and the change is under min_coverable_lines.
    assert risked["risky"]["gate"] == "too_small"
    assert risked["files"][0]["risk"]["basis"] == "git_and_index"
    assert PatchCoverageResponse.model_validate(risked).model_dump() == risked

    # With hints attached: a measured row carries them, the others stay null.
    hint = TestHint((2, 3), "Auth.login", ("tests/test_a.py",), "per_test", 2)
    hinted = attach_hints(pc, {"a.py": (hint,)}).to_dict()
    assert hinted["files"][0]["hints"][0]["basis"] == "per_test"
    assert {f["file_path"] for f in hinted["files"] if f["hints"] is not None} == {"a.py"}
    assert PatchCoverageResponse.model_validate(hinted).model_dump() == hinted

    # With a project delta: totals, the gate and each outside-change row round-trip.
    project = ProjectDelta(
        base=ProjectTotals(80, 100),
        head=ProjectTotals(79, 100),
        basis="base_report",
        base_commit="b" * 40,
        head_commit="h" * 40,
        max_drop=0.5,
        outside_change=(
            IndirectChange(
                "b.py",
                ((4, 6),),
                1,
                50.0,
                40.0,
                "changed",
                (IndirectCause("test_deleted", "tests/test_b.py", "name"),),
            ),
            IndirectChange("c.py", (), 0, 100.0, None, "no_longer_measured"),
        ),
    )
    projected = replace(pc, project=project).to_dict()
    assert projected["project"]["gate"] == "fail"
    assert PatchCoverageResponse.model_validate(projected).model_dump() == projected
    assert wire["project"] is None
