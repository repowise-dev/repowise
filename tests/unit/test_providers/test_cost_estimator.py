"""Unit tests for cost estimator — verifies pricing for all current models."""

from __future__ import annotations

import pytest

from repowise.cli.cost_estimator import PageTypePlan, _lookup_cost, estimate_cost
from repowise.cli.cost_estimator.heuristics import heuristic_tokens

# ---------------------------------------------------------------------------
# Per-model pricing (input_rate, output_rate) per 1K tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected_input,expected_output",
    [
        # OpenAI GPT-5.4 family
        ("gpt-5.4-nano", 0.0002, 0.00125),
        ("gpt-5.4-mini", 0.00075, 0.0045),
        ("gpt-5.4", 0.0025, 0.015),
        # Gemini
        ("gemini-3.1-flash-lite-preview", 0.00025, 0.0015),
        ("gemini-3-flash-preview", 0.0005, 0.003),
        ("gemini-3.1-pro-preview", 0.002, 0.012),
        # Anthropic Claude
        ("claude-opus-4-6", 0.005, 0.025),
        ("claude-sonnet-4-6", 0.003, 0.015),
        ("claude-haiku-4-5", 0.001, 0.005),
        # Free/local models
        ("mock", 0.0, 0.0),
        ("llama3", 0.0, 0.0),
        ("codex_cli/default", 0.0, 0.0),
        ("codex_cli/gpt-5.5", 0.0, 0.0),
    ],
)
def test_lookup_cost(model, expected_input, expected_output):
    inp, out = _lookup_cost(model)
    assert inp == pytest.approx(expected_input, rel=1e-6)
    assert out == pytest.approx(expected_output, rel=1e-6)


# ---------------------------------------------------------------------------
# estimate_cost integration
# ---------------------------------------------------------------------------


def test_estimate_cost_gpt54_nano():
    plans = [PageTypePlan("repo_overview", 1, 6)]
    est = estimate_cost(plans, "openai", "gpt-5.4-nano")
    inp, out = heuristic_tokens("repo_overview")
    expected = (inp / 1000) * 0.0002 + (out / 1000) * 0.00125
    assert est.estimated_cost_usd == pytest.approx(expected, rel=1e-6)
    assert est.model_name == "gpt-5.4-nano"


def test_estimate_cost_claude_opus():
    plans = [PageTypePlan("repo_overview", 1, 6)]
    est = estimate_cost(plans, "anthropic", "claude-opus-4-6")
    inp, out = heuristic_tokens("repo_overview")
    expected = (inp / 1000) * 0.005 + (out / 1000) * 0.025
    assert est.estimated_cost_usd == pytest.approx(expected, rel=1e-6)


def test_estimate_cost_gemini_lite():
    plans = [PageTypePlan("file_page", 10, 2)]
    est = estimate_cost(plans, "gemini", "gemini-3.1-flash-lite-preview")
    inp, out = heuristic_tokens("file_page")
    expected = (inp * 10 / 1000) * 0.00025 + (out * 10 / 1000) * 0.0015
    assert est.estimated_cost_usd == pytest.approx(expected, rel=1e-6)


def test_estimate_cost_zero_for_mock():
    plans = [PageTypePlan("module_page", 5, 4)]
    est = estimate_cost(plans, "mock", "mock")
    assert est.estimated_cost_usd == 0.0
