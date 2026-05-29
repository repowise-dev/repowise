"""Tests for the class-level structural biomarkers: low_cohesion, god_class."""

from __future__ import annotations

from repowise.core.analysis.health.biomarkers import FileContext
from repowise.core.analysis.health.biomarkers.god_class import GodClassDetector
from repowise.core.analysis.health.biomarkers.low_cohesion import LowCohesionDetector
from repowise.core.analysis.health.complexity import ClassComplexity, FunctionComplexity


def _fn(name: str, *, nloc: int = 5, ccn: int = 1) -> FunctionComplexity:
    return FunctionComplexity(
        name=name, start_line=1, end_line=1 + nloc, ccn=ccn, max_nesting=0, cognitive=0, nloc=nloc
    )


def _cls(
    name: str,
    *,
    method_count: int,
    lcom4: int = 1,
    total_nloc: int = 50,
    methods: list[FunctionComplexity] | None = None,
    field_count: int = 0,
) -> ClassComplexity:
    methods = methods if methods is not None else [_fn(f"m{i}") for i in range(method_count)]
    return ClassComplexity(
        name=name,
        start_line=1,
        end_line=1 + total_nloc,
        method_count=method_count,
        total_nloc=total_nloc,
        methods=methods,
        lcom4=lcom4,
        max_method_ccn=max((m.ccn for m in methods), default=0),
        field_count=field_count,
    )


def _ctx(classes: list[ClassComplexity]) -> FileContext:
    return FileContext(
        file_path="src/example.py",
        language="python",
        nloc=200,
        has_test_file=False,
        module=None,
        class_metrics=classes,
    )


# ---- low_cohesion --------------------------------------------------------


def test_low_cohesion_flags_splintered_class():
    cls = _cls("Splintered", method_count=6, lcom4=3, field_count=4)
    out = LowCohesionDetector().detect(_ctx([cls]))
    assert len(out) == 1
    assert out[0].details["lcom4"] == 3
    assert out[0].severity == "high"  # lcom4 >= 3


def test_low_cohesion_severity_critical():
    cls = _cls("Massive", method_count=16, lcom4=5)
    out = LowCohesionDetector().detect(_ctx([cls]))
    assert out[0].severity == "critical"  # lcom4 >= 4 AND methods >= 15


def test_low_cohesion_skips_cohesive_class():
    cls = _cls("Cohesive", method_count=8, lcom4=1)
    assert LowCohesionDetector().detect(_ctx([cls])) == []


def test_low_cohesion_skips_tiny_class():
    # Splintered but too few methods to matter.
    cls = _cls("Tiny", method_count=3, lcom4=3)
    assert LowCohesionDetector().detect(_ctx([cls])) == []


def test_low_cohesion_no_classes_is_silent():
    assert LowCohesionDetector().detect(_ctx([])) == []


# ---- god_class -----------------------------------------------------------


def _brainy_methods() -> list[FunctionComplexity]:
    methods = [_fn(f"m{i}") for i in range(15)]
    methods.append(_fn("brain", nloc=90, ccn=12))  # the brain method
    return methods


def test_god_class_flags_large_complex_class():
    cls = _cls("God", method_count=16, total_nloc=320, methods=_brainy_methods())
    out = GodClassDetector().detect(_ctx([cls]))
    assert len(out) == 1
    assert out[0].details["total_nloc"] == 320
    assert out[0].severity == "high"  # total_nloc >= 300


def test_god_class_requires_a_brain_method():
    # Large + many methods but every method is flat → not a god class.
    flat = [_fn(f"m{i}", nloc=12, ccn=2) for i in range(16)]
    cls = _cls("BigButFlat", method_count=16, total_nloc=320, methods=flat)
    assert GodClassDetector().detect(_ctx([cls])) == []


def test_god_class_requires_size_and_method_count():
    # Has a brain method but is small / few methods.
    cls = _cls("Small", method_count=4, total_nloc=120, methods=[_fn("brain", nloc=90, ccn=12)])
    assert GodClassDetector().detect(_ctx([cls])) == []


def test_god_class_critical_severity():
    cls = _cls("Behemoth", method_count=26, total_nloc=450, methods=_brainy_methods())
    out = GodClassDetector().detect(_ctx([cls]))
    assert out[0].severity == "critical"  # total_nloc >= 400 AND methods >= 25
