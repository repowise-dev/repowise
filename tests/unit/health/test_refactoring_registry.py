"""Registry-level gating: disabled detectors + the config confidence floor."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.refactoring import registry
from repowise.core.analysis.health.refactoring.models import (
    RefactoringContext,
    RefactoringSuggestion,
)
from repowise.core.analysis.health.refactoring.registry import (
    RefactoringDetector,
    detect_refactorings,
)


def _suggestion(confidence: str, name: str = "fake") -> RefactoringSuggestion:
    return RefactoringSuggestion(
        refactoring_type=name,
        file_path="a.py",
        target_symbol="X",
        line_start=1,
        line_end=2,
        plan={},
        evidence={},
        impact_delta=0.0,
        effort_bucket="S",
        blast_radius={},
        confidence=confidence,
    )


class _FakeDetector(RefactoringDetector):
    name = "fake_all_confidences"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        return [_suggestion("low"), _suggestion("medium"), _suggestion("high")]


@pytest.fixture
def _one_detector(monkeypatch):
    monkeypatch.setattr(registry, "_REGISTRY", [_FakeDetector()])


def _ctx() -> RefactoringContext:
    return RefactoringContext(file_path="a.py", language="python", nloc=10)


def test_no_floor_keeps_all(_one_detector):
    out = detect_refactorings(_ctx())
    assert [s.confidence for s in out] == ["low", "medium", "high"]


def test_medium_floor_drops_low(_one_detector):
    out = detect_refactorings(_ctx(), min_confidence="medium")
    assert [s.confidence for s in out] == ["medium", "high"]


def test_high_floor_keeps_only_high(_one_detector):
    out = detect_refactorings(_ctx(), min_confidence="high")
    assert [s.confidence for s in out] == ["high"]


def test_unknown_floor_is_no_floor(_one_detector):
    out = detect_refactorings(_ctx(), min_confidence="bogus")
    assert len(out) == 3


def test_disabled_detector_emits_nothing(_one_detector):
    out = detect_refactorings(_ctx(), disabled=["fake_all_confidences"])
    assert out == []
