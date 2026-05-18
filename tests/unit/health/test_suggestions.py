"""Tests for ``health/suggestions.py`` — deterministic refactoring text."""

from __future__ import annotations

from repowise.core.analysis.health.suggestions import (
    annotate_finding,
    suggestion_for,
)


def test_suggestion_known_biomarker_is_actionable():
    text = suggestion_for("brain_method")
    assert "split" in text.lower() or "extract" in text.lower()


def test_suggestion_unknown_biomarker_falls_back():
    text = suggestion_for("not_a_real_biomarker")
    assert text  # never empty
    assert "health-rules.json" in text  # fallback hints at suppression


def test_annotate_finding_adds_suggestion_field():
    out = annotate_finding({"biomarker_type": "nested_complexity", "severity": "high"})
    assert out["biomarker_type"] == "nested_complexity"
    assert "suggestion" in out
    assert out["suggestion"] == suggestion_for("nested_complexity")
