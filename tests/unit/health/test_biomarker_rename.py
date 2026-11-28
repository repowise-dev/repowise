"""A renamed biomarker keeps working under its old id everywhere a user can type one."""

from __future__ import annotations

from repowise.core.analysis.health.biomarker_labels import biomarker_label
from repowise.core.analysis.health.biomarkers.registry import registered_biomarkers
from repowise.core.analysis.health.config import HealthConfig
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.models import Severity
from repowise.core.analysis.health.refactoring.identity import refactoring_public_id

OLD = "bumpy_road"
NEW = "repeated_nesting"


def test_the_detector_registers_under_the_new_id():
    names = {b.name for b in registered_biomarkers()}
    assert NEW in names
    assert OLD not in names


def test_health_rules_written_with_the_old_id_read_as_the_new_one():
    cfg = HealthConfig.from_dict(
        {
            "disabled_biomarkers": [OLD],
            "severity_overrides": {OLD: "high"},
            "rules": [
                {
                    "path": "src/**",
                    "disabled_biomarkers": [OLD],
                    "severity_overrides": {OLD: "low"},
                }
            ],
        }
    )
    out = cfg.to_analyzer_config(["src/a.py"])
    assert out["disabled_biomarkers"] == [NEW]
    assert out["severity_overrides"] == {NEW: Severity.HIGH}
    assert out["per_file_disabled"] == {"src/a.py": {NEW}}
    assert out["per_file_severity_overrides"] == {"src/a.py": {NEW: Severity.LOW}}


def test_the_label_reads_the_same_for_both_ids():
    assert biomarker_label(NEW) == "Repeated nesting"
    assert biomarker_label(OLD) == "Repeated nesting"


def _finding_row(kind: str) -> dict:
    return {
        "biomarker_type": kind,
        "file_path": "src/a.py",
        "function_name": "walk",
        "line_start": 3,
        "line_end": 40,
        "dimension": "defect",
        "details": {"bumps": 4},
    }


def test_finding_ids_hold_across_the_rename():
    assert finding_public_id(_finding_row(NEW)) == finding_public_id(_finding_row(OLD))


def test_plan_ids_hold_across_the_rename():
    def plan(kind: str) -> dict:
        return {
            "refactoring_type": "extract_method",
            "file_path": "src/a.py",
            "target_symbol": "walk",
            "source_biomarker": kind,
            "line_start": 3,
            "line_end": 40,
        }

    assert refactoring_public_id(plan(NEW)) == refactoring_public_id(plan(OLD))


def test_a_refactoring_target_filter_on_the_old_id_keeps_the_renamed_findings():
    from types import SimpleNamespace

    from repowise.core.analysis.health.impact_effort import _finding_filter

    keep = _finding_filter(OLD, None, None)
    assert keep(SimpleNamespace(biomarker_type=NEW, severity="medium"))
    assert not keep(SimpleNamespace(biomarker_type="large_method", severity="medium"))
