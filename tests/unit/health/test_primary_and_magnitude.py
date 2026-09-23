"""Golden pins for the per-file lead fold the code-health routes serve."""

from __future__ import annotations

from types import SimpleNamespace

from repowise.server.routers.code_health import _leads_by_file, _primary_and_magnitude


def _f(path, biomarker, impact, reason=""):
    return SimpleNamespace(
        file_path=path, biomarker_type=biomarker, health_impact=impact, reason=reason
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
    # Null impact counts as zero; ties keep the first.
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
        "primary_biomarker": "long_method",
        "primary_reason": "90 lines",
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
