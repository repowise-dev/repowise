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


def test_every_registered_biomarker_has_its_own_action():
    # The Findings tab shows this sentence as each row's action line, so a
    # marker without one would fall back to the generic "review this" text.
    from repowise.core.analysis.health.biomarkers.registry import registered_biomarkers
    from repowise.core.analysis.health.suggestions import _TEMPLATES

    names = {d.name for d in registered_biomarkers()}
    names |= {"ungoverned_hotspot", "stale_governance", "contradictory_decision"}
    assert sorted(n for n in names if n not in _TEMPLATES) == []


def test_annotate_finding_adds_suggestion_field():
    out = annotate_finding({"biomarker_type": "nested_complexity", "severity": "high"})
    assert out["biomarker_type"] == "nested_complexity"
    assert "suggestion" in out
    assert out["suggestion"] == suggestion_for("nested_complexity")
