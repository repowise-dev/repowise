"""Workspace init must persist what the run degraded on (issue #1369, path #2108 missed).

The single-repo flow folds its generation and persist callbacks into one
``run_warnings`` list, which lands in ``state.json`` as ``degraded``. The workspace
flow builds its own generation progress bar after the pipeline callback is gone, so
a silent downgrade to keyless vectors wrote a clean ``state.json``: semantic search
off, with nothing in the record saying so.

These tests pin the fold, and the two mistakes the first attempt at it made:

  * ``state["degraded"] = [...]`` assigns rather than appends, dropping anything an
    earlier phase recorded;
  * a list passed by value instead of by identity, so the caller reads back nil.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from repowise.cli.commands.init_cmd import workspace as ws


def _capture_run_repo_generation(monkeypatch: Any) -> dict[str, Any]:
    """Patch ``run_repo_generation`` and capture the kwargs it was called with.

    ``_run_workspace_generation`` estimates cost before it generates, which walks
    a real pipeline result; stub that gate so these tests stay about the warnings
    list rather than about a fixture that satisfies the estimator.
    """
    captured: dict[str, Any] = {}

    def fake_run_repo_generation(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return ["page1"]

    monkeypatch.setattr(ws, "run_repo_generation", fake_run_repo_generation)
    monkeypatch.setattr(
        ws,
        "estimate_generation",
        lambda *_a, **_k: ([], SimpleNamespace(cost_range=None, estimated_cost_usd=0.0)),
    )
    monkeypatch.setattr(ws, "cost_gate_declined", lambda *_a, **_k: False)
    return captured


def test_deterministic_generation_threads_the_same_warnings_list(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Identity, not equality: the caller reads the list back after the call."""
    captured = _capture_run_repo_generation(monkeypatch)
    mine: list[str] = []

    ws._run_workspace_deterministic_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        embedder_name_resolved="mock",
        embedder_was_requested=False,
        concurrency=8,
        resume=False,
        onboarding=True,
        wiki_style="comprehensive",
        language="en",
        warnings=mine,
    )

    assert captured["warnings"] is mine


def test_llm_generation_threads_the_same_warnings_list(
    tmp_path: Any, monkeypatch: Any
) -> None:
    captured = _capture_run_repo_generation(monkeypatch)
    mine: list[str] = []

    ws._run_workspace_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        provider=SimpleNamespace(provider_name="openai", model_name="gpt-x"),
        embedder_name_resolved="mock",
        concurrency=8,
        yes=True,
        resume=False,
        skip_tests=False,
        skip_infra=False,
        test_run=False,
        warnings=mine,
    )

    assert captured["warnings"] is mine


def test_warnings_are_optional_for_callers_that_do_not_pass_them(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """Omitting ``warnings`` stays legal, so older callers are unaffected."""
    captured = _capture_run_repo_generation(monkeypatch)

    ws._run_workspace_generation(
        repo_path=tmp_path,
        result=SimpleNamespace(repo_name="demo"),
        provider=SimpleNamespace(provider_name="openai", model_name="gpt-x"),
        embedder_name_resolved="mock",
        concurrency=8,
        yes=True,
        resume=False,
        skip_tests=False,
        skip_infra=False,
        test_run=False,
    )

    assert captured["warnings"] is None

def test_generation_warnings_land_in_the_degraded_list() -> None:
    """The whole point: an agent reading state.json sees the downgrade."""
    state: dict[str, Any] = {}
    ws._fold_generation_warnings(state, ["configured 'openai' could not be built"])

    assert state["degraded"] == ["configured 'openai' could not be built"]


def test_fold_extends_and_does_not_clobber_an_earlier_phase() -> None:
    """Assigning would drop an entry an earlier phase already recorded.

    This is the bug the first attempt at this fix shipped: ``state["degraded"] =
    [w]`` replaces the list, so a header-probe degradation recorded before
    generation ran disappeared from the record. The single-repo path builds one
    list for the whole run for exactly this reason.
    """
    state: dict[str, Any] = {"degraded": ["header probe already degraded"]}
    ws._fold_generation_warnings(state, ["generation build degraded too"])

    assert state["degraded"] == [
        "header probe already degraded",
        "generation build degraded too",
    ]


def test_no_warnings_leaves_state_untouched() -> None:
    """A clean run must not write an empty ``degraded`` key."""
    state: dict[str, Any] = {}
    ws._fold_generation_warnings(state, [])

    assert "degraded" not in state
