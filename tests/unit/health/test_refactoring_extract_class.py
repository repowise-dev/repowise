"""Tests for the Extract Class refactoring detector and its LCOM4 data unlock.

Two layers:
- the walker now returns the LCOM4 connected components behind the ``lcom4``
  integer (``ClassComplexity.components``);
- the ``ExtractClassDetector`` turns a multi-component class into a structured
  ``RefactoringSuggestion`` with the concrete split groups.

Fixtures are small real-Python classes with a known split so the expected
groups are unambiguous and the assertions are deterministic.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity import ClassComplexity, FunctionComplexity
from repowise.core.analysis.health.complexity.walker import walk_file
from repowise.core.analysis.health.refactoring import (
    RefactoringContext,
    detect_refactorings,
    registered_detectors,
)
from repowise.core.analysis.health.refactoring.extract_class import ExtractClassDetector

# A class with two disjoint, stateful responsibilities: a "parse" cluster over
# fields ``a``/``a2`` and a "render" cluster over fields ``b``/``b2``. No
# constructor links them, so LCOM4 sees two field-bearing components — a real
# Extract Class shape (high field density).
_TWO_JOB_CLASS = b"""
class TwoJobs:
    def parse_a(self):
        return self.a + self.a2
    def parse_a2(self):
        return self.parse_a() + self.a
    def parse_a3(self):
        return self.a2 + self.parse_a2()
    def render_b(self):
        return self.b + self.b2
    def render_b2(self):
        return self.render_b() * self.b
    def render_b3(self):
        return self.b2 + self.render_b2()
"""

# A cohesive class: every method touches the shared field, so LCOM4 == 1.
_COHESIVE_CLASS = b"""
class Cohesive:
    def a(self):
        return self.shared
    def b(self):
        return self.shared + self.a()
    def c(self):
        return self.shared + self.b()
    def d(self):
        return self.shared + self.c()
    def e(self):
        return self.shared + self.d()
"""


def _walk(src: bytes) -> ClassComplexity:
    fc = walk_file("example.py", "python", src)
    assert fc.classes, "fixture produced no class"
    return fc.classes[0]


class _Finding:
    """Minimal HealthFindingData-like stand-in for a cohesion finding."""

    def __init__(self, name: str, impact: float, biomarker: str = "low_cohesion"):
        self.biomarker_type = biomarker
        self.function_name = name
        self.health_impact = impact
        self.details = {"class_name": name}


def _ctx(cls: ClassComplexity, findings: list | None = None, dependents: int = 0):
    return RefactoringContext(
        file_path="example.py",
        language="python",
        nloc=cls.total_nloc,
        classes=[cls],
        findings=findings or [],
        dependents_count=dependents,
    )


# ---- data unlock: LCOM4 components ---------------------------------------


def test_lcom4_returns_components_for_split_class():
    cls = _walk(_TWO_JOB_CLASS)
    assert cls.lcom4 == 2
    assert len(cls.components) == 2
    # Each component carries its methods + the field(s) it touches.
    groups = {tuple(g.methods): g.fields for g in cls.components}
    assert ["parse_a", "parse_a2", "parse_a3"] in [list(k) for k in groups]
    assert ["render_b", "render_b2", "render_b3"] in [list(k) for k in groups]
    for g in cls.components:
        assert g.fields  # each cluster touches at least one field


def test_cohesive_class_has_single_or_no_component():
    cls = _walk(_COHESIVE_CLASS)
    assert cls.lcom4 == 1
    # lcom4 == 1 means no real split to expose.
    assert len(cls.components) <= 1


def test_components_are_deterministic_and_source_ordered():
    a = _walk(_TWO_JOB_CLASS)
    b = _walk(_TWO_JOB_CLASS)
    sig_a = [(tuple(g.methods), tuple(g.fields)) for g in a.components]
    sig_b = [(tuple(g.methods), tuple(g.fields)) for g in b.components]
    assert sig_a == sig_b
    # Groups read top-to-bottom: the parse cluster (earlier lines) first.
    assert a.components[0].methods[0] == "parse_a"


# ---- detector ------------------------------------------------------------


def test_detector_registered():
    assert "extract_class" in [d.name for d in registered_detectors()]


def test_detector_emits_split_for_god_class():
    cls = _walk(_TWO_JOB_CLASS)
    findings = [_Finding("TwoJobs", 2.4)]
    sugs = detect_refactorings(_ctx(cls, findings, dependents=5))
    assert len(sugs) == 1
    s = sugs[0]
    assert s.refactoring_type == "extract_class"
    assert s.target_symbol == "TwoJobs"
    assert s.impact_delta == 2.4
    assert s.source_biomarker == "low_cohesion"
    assert s.blast_radius == {"dependents_count": 5}
    assert s.evidence["lcom4"] == 2
    assert s.evidence["method_count"] == 6
    assert s.evidence["wmc"] == sum(m.ccn for m in cls.methods)
    assert len(s.plan["groups"]) == 2
    assert all(g["name"] is None for g in s.plan["groups"])


def test_detector_silent_on_cohesive_class():
    cls = _walk(_COHESIVE_CLASS)
    assert detect_refactorings(_ctx(cls)) == []


def test_detector_silent_below_min_methods():
    # lcom4 >= 2 but only 4 methods — below the cohesion threshold, no suggestion.
    methods = [
        FunctionComplexity(
            name=f"m{i}", start_line=i, end_line=i, ccn=1, max_nesting=0, cognitive=0, nloc=2
        )
        for i in range(4)
    ]
    cls = ClassComplexity(
        name="Small",
        start_line=1,
        end_line=20,
        method_count=4,
        total_nloc=20,
        methods=methods,
        lcom4=2,
    )
    assert detect_refactorings(_ctx(cls)) == []


def test_detector_without_finding_still_emits_with_zero_impact():
    cls = _walk(_TWO_JOB_CLASS)
    sugs = detect_refactorings(_ctx(cls, findings=[]))
    assert len(sugs) == 1
    assert sugs[0].impact_delta == 0.0


def test_detector_output_is_stable_ordered():
    # Two splittable classes; output sorts by recovered impact desc, then name.
    src = (
        _TWO_JOB_CLASS
        + b"""
class OtherJobs:
    def load_x(self):
        return self.x + self.x2
    def load_x2(self):
        return self.load_x() + self.x
    def load_x3(self):
        return self.x2 + self.load_x2()
    def save_y(self):
        return self.y + self.y2
    def save_y2(self):
        return self.save_y() * self.y
    def save_y3(self):
        return self.y2 + self.save_y2()
"""
    )
    fc = walk_file("multi.py", "python", src)
    findings = [_Finding("TwoJobs", 1.0), _Finding("OtherJobs", 3.0)]
    ctx = RefactoringContext(
        file_path="multi.py",
        language="python",
        nloc=100,
        classes=fc.classes,
        findings=findings,
    )
    sugs = detect_refactorings(ctx)
    assert [s.target_symbol for s in sugs] == ["OtherJobs", "TwoJobs"]


def test_disabled_detector_yields_nothing():
    cls = _walk(_TWO_JOB_CLASS)
    sugs = detect_refactorings(_ctx(cls, [_Finding("TwoJobs", 2.0)]), disabled=["extract_class"])
    assert sugs == []


def test_detector_is_deterministic():
    cls = _walk(_TWO_JOB_CLASS)
    findings = [_Finding("TwoJobs", 2.4)]
    a = detect_refactorings(_ctx(cls, findings))
    b = detect_refactorings(_ctx(cls, findings))
    assert [(s.target_symbol, s.plan) for s in a] == [(s.target_symbol, s.plan) for s in b]


def test_confidence_high_for_strong_god_class():
    cls = _walk(_TWO_JOB_CLASS)
    # Bump method_count to cross the high-confidence threshold.
    cls.method_count = 16
    det = ExtractClassDetector()
    assert det._confidence(cls) == "high"
