"""Tests for the coverage-intersection helper (test-impact query join)."""
from __future__ import annotations

from repowise.core.analysis.changed_lines import coverage_intersection


def test_intersection_finds_covered_changes():
    changes = {"a.py": {10, 11, 12}, "b.py": {3, 4}}
    coverage = {"a.py": {1, 2, 11}, "b.py": {3, 30}}
    out = coverage_intersection(changes, coverage)
    assert out == {"a.py": {11}, "b.py": {3}}


def test_intersection_omits_uncovered_files():
    changes = {"a.py": {1, 2}, "b.py": {5}}
    coverage = {"a.py": {9, 10}}  # b.py absent, a.py no overlap
    assert coverage_intersection(changes, coverage) == {}


def test_intersection_handles_empty_inputs():
    assert coverage_intersection({}, {"a.py": {1}}) == {}
    assert coverage_intersection({"a.py": {1}}, {}) == {}
    assert coverage_intersection({}, {}) == {}


def test_intersection_does_not_mutate_inputs():
    changes = {"a.py": {1, 2, 3}}
    coverage = {"a.py": {2}}
    before_changes = dict(changes)
    before_coverage = {k: set(v) for k, v in coverage.items()}
    out = coverage_intersection(changes, coverage)
    assert changes == before_changes
    assert coverage == before_coverage
    # The returned set is a copy, not a view into the input.
    out["a.py"].add(99)
    assert changes["a.py"] == {1, 2, 3}
