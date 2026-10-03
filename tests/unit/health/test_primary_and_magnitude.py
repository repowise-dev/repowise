"""Golden pins for the per-file lead fold the code-health routes serve."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.routers.code_health import _leads_by_file, _primary_and_magnitude


def _f(path, biomarker, impact, reason="", severity="medium"):
    return SimpleNamespace(
        file_path=path,
        biomarker_type=biomarker,
        health_impact=impact,
        reason=reason,
        severity=severity,
    )


FINDINGS = [
    # Continuous marker has the larger impact but a discrete one leads.
    _f("a.py", "coverage_gradient", 2.4567, "71% uncovered"),
    _f("a.py", "complex_method", 1.1111, "ccn 18"),
    _f("a.py", "deep_nesting", 0.3334, "nests 5"),
    # Continuous marker alone still leads.
    _f("b.py", "coverage_gradient", 0.9, "40% uncovered"),
    # Advisory only: no lead, real zero magnitude.
    _f("c.py", "assertion_free_test", 0.0, "no asserts"),
    # Null impact counts as zero; a tie is decided by name, not input order.
    _f("d.py", "god_object", None, "big"),
    _f("d.py", "long_method", 0.1, "90 lines"),
    _f("d.py", "complex_method", 0.1, "ccn 11"),
]

GOLDEN = {
    "a.py": {
        "primary_biomarker": "complex_method",
        "primary_reason": "ccn 18",
        "total_deduction": 3.901,
    },
    "b.py": {
        "primary_biomarker": "coverage_gradient",
        "primary_reason": "40% uncovered",
        "total_deduction": 0.9,
    },
    "c.py": {"primary_biomarker": None, "primary_reason": None, "total_deduction": 0.0},
    "d.py": {
        "primary_biomarker": "complex_method",
        "primary_reason": "ccn 11",
        "total_deduction": 0.2,
    },
}


def test_leads_by_file_golden() -> None:
    assert _leads_by_file(FINDINGS) == GOLDEN


def test_primary_and_magnitude_golden_per_file() -> None:
    for path, expected in GOLDEN.items():
        assert _primary_and_magnitude([f for f in FINDINGS if f.file_path == path]) == expected


def test_primary_and_magnitude_empty() -> None:
    assert _primary_and_magnitude([]) == {
        "primary_biomarker": None,
        "primary_reason": None,
        "total_deduction": None,
    }


def test_route_names_are_the_core_fold() -> None:
    from repowise.core.analysis.health import aggregation

    assert _primary_and_magnitude is aggregation.primary_and_magnitude
    assert _leads_by_file is aggregation.primary_and_magnitude_by_file


def test_plain_dicts_fold_like_rows() -> None:
    from repowise.core.analysis.health.aggregation import primary_and_magnitude_by_file

    assert primary_and_magnitude_by_file([vars(f) for f in FINDINGS]) == GOLDEN


def test_primary_finding_prefers_code_shape_over_history() -> None:
    """A history marker names context, not an edit; a code-shape finding leads
    even when the history one deducts more, and history leads only alone."""
    from repowise.core.analysis.health.models import primary_finding

    shaped = _f("e.py", "complex_method", 0.4, "ccn 14")
    history = _f("e.py", "change_entropy", 2.5, "changed with 25 files")
    assert primary_finding([history, shaped]) is shaped
    assert primary_finding([history]) is history


def test_primary_finding_tie_does_not_depend_on_input_order() -> None:
    """Equal impacts must not let row order pick the lead: two indexes of one
    tree read the rows in different orders and disagreed on it."""
    from repowise.core.analysis.health.models import primary_finding

    cohesion = _f("f.java", "low_cohesion", 0.871, "lcom 0.9")
    complex_ = _f("f.java", "complex_method", 0.871, "ccn 30")
    assert primary_finding([cohesion, complex_]) is complex_
    assert primary_finding([complex_, cohesion]) is complex_

    # Severity breaks the tie before the name does.
    severe = _f("f.java", "low_cohesion", 0.871, "lcom 0.9", severity="high")
    assert primary_finding([complex_, severe]) is severe
    assert primary_finding([severe, complex_]) is severe
