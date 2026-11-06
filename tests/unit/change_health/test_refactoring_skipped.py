"""Revision analysis reads findings and metrics only, so it builds no suggestions.

Skipping them must not move the delta: each case compares the shipped run with
one that still builds suggestions, over a long method and a perf finding.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.change_health import ChangeHealthDeltaService, DeltaRequest
from repowise.core.analysis.health import file_evaluator
from repowise.core.analysis.health.engine import HealthAnalyzer

from .conftest import LANGUAGES, python_io_in_loop


def _delta(repo, monkeypatch, *, suggestions: bool):
    calls: list[int] = []
    real_suggestions = file_evaluator.FileEvaluator._suggestions
    real_analyze = HealthAnalyzer.analyze

    def counting(self, *args, **kwargs):
        calls.append(1)
        return real_suggestions(self, *args, **kwargs)

    def analyze(self, *, config=None, **kwargs):
        if suggestions:
            config = {**(config or {}), "refactoring_enabled": True}
        return real_analyze(self, config=config, **kwargs)

    monkeypatch.setattr(file_evaluator.FileEvaluator, "_suggestions", counting)
    monkeypatch.setattr(HealthAnalyzer, "analyze", analyze)
    service = ChangeHealthDeltaService(repo_path=str(repo.path), cache_dir=False)
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD"))
    delta.timing_ms = 0.0
    return delta, len(calls)


@pytest.mark.parametrize("language", sorted(LANGUAGES))
def test_delta_is_unchanged_and_no_suggestions_are_built(make_repo, monkeypatch, language):
    name, complex_source = LANGUAGES[language]
    repo = make_repo(language)
    repo.commit("base", {name: complex_source("run", 2)})
    repo.commit("head", {name: complex_source("run", 18)})

    shipped, shipped_calls = _delta(repo, monkeypatch, suggestions=False)
    reference, reference_calls = _delta(repo, monkeypatch, suggestions=True)

    assert shipped.findings
    assert shipped == reference
    assert shipped_calls == 0
    assert reference_calls > 0


def test_delta_with_a_perf_finding_is_unchanged(make_repo, monkeypatch):
    repo = make_repo("perf")
    repo.commit("base", {"app.py": python_io_in_loop(in_loop=False)})
    repo.commit("head", {"app.py": python_io_in_loop(in_loop=True)})

    shipped, shipped_calls = _delta(repo, monkeypatch, suggestions=False)
    reference, _ = _delta(repo, monkeypatch, suggestions=True)

    assert shipped == reference
    assert shipped_calls == 0
