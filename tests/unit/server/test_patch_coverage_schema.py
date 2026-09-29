"""The REST model is ``PatchCoverage.to_dict()``, key for key, so they cannot drift."""

from __future__ import annotations

from repowise.core.analysis.health.coverage import file_coverage
from repowise.core.analysis.patch_coverage import PatchScope, compute_patch_coverage
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
        scope=PatchScope(
            label="main...HEAD",
            source_formats=("lcov",),
            reports=("lcov.info",),
            report_path_count=4,
            unmatched_report_path_count=1,
            mapping_partial=True,
            measured_commit="abc",
            freshness="stale",
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
    assert PatchCoverageResponse.model_validate(wire).model_dump() == wire
