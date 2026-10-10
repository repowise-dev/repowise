"""Core's agent prompts render ``tests/fixtures/agent_prompts/golden.json``.

Core is the one renderer: the web shows these prompts from the server. The
golden pins every flavor's bytes, so a wording change shows up as a diff here.

Regenerate after a deliberate wording change:
    UPDATE_AGENT_PROMPT_GOLDENS=1 pytest tests/unit/agent_prompts/test_golden.py
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from repowise.core.agent_prompts import (
    FLAVORS,
    render_action,
    render_fix_item,
    render_opportunity,
    render_plan,
    render_verify_lines,
)
from repowise.core.agent_prompts.refactoring import render_plan_spec

_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures/agent_prompts"


def _load(name: str) -> dict:
    return json.loads((_DIR / name).read_text(encoding="utf-8"))


_ACTIONS = _load("actions.json")["cases"]
_ITEMS = _load("fix_items.json")["cases"]
_PLANS = _load("refactoring.json")["plans"]
_OPPORTUNITIES = _load("refactoring.json")["opportunities"]


def _render_all() -> dict:
    def per_flavor(render) -> dict[str, str]:
        return {flavor: render(flavor) for flavor in FLAVORS}

    return {
        "action": {
            c["name"]: per_flavor(lambda f, c=c: render_action(c["action"], f, c.get("repo_name")))
            for c in _ACTIONS
        },
        "fix_item": {
            c["name"]: per_flavor(lambda f, c=c: render_fix_item(c["item"], f, c.get("repo_name")))
            for c in _ITEMS
        },
        "verify": {c["name"]: render_verify_lines(c["item"]) for c in _ITEMS},
        "refactoring_plan": {
            c["name"]: per_flavor(lambda f, c=c: render_plan(c["detail"], f, c.get("repo_name")))
            for c in _PLANS
        },
        "refactoring_plan_spec": {c["name"]: render_plan_spec(c["detail"]) for c in _PLANS},
        "refactoring_opportunity": {
            c["name"]: per_flavor(
                lambda f, c=c: render_opportunity(c["detail"], f, c.get("repo_name"))
            )
            for c in _OPPORTUNITIES
        },
    }


if os.environ.get("UPDATE_AGENT_PROMPT_GOLDENS"):
    (_DIR / "golden.json").write_text(
        json.dumps(_render_all(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
_GOLDEN = _load("golden.json")


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("case", _ACTIONS, ids=lambda c: c["name"])
def test_action_matches_golden(case: dict, flavor: str) -> None:
    text = render_action(case["action"], flavor, case.get("repo_name"))
    assert text == _GOLDEN["action"][case["name"]][flavor]


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("case", _ITEMS, ids=lambda c: c["name"])
def test_fix_item_matches_golden(case: dict, flavor: str) -> None:
    text = render_fix_item(case["item"], flavor, case.get("repo_name"))
    assert text == _GOLDEN["fix_item"][case["name"]][flavor]


@pytest.mark.parametrize("case", _ITEMS, ids=lambda c: c["name"])
def test_verify_lines_match_golden(case: dict) -> None:
    assert render_verify_lines(case["item"]) == _GOLDEN["verify"][case["name"]]


def test_every_case_has_a_golden() -> None:
    assert set(_GOLDEN["action"]) == {c["name"] for c in _ACTIONS}
    assert set(_GOLDEN["fix_item"]) == {c["name"] for c in _ITEMS}
    assert set(_GOLDEN["refactoring_plan"]) == {c["name"] for c in _PLANS}
    assert set(_GOLDEN["refactoring_opportunity"]) == {c["name"] for c in _OPPORTUNITIES}


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("case", _PLANS, ids=lambda c: c["name"])
def test_refactoring_plan_matches_golden(case: dict, flavor: str) -> None:
    text = render_plan(case["detail"], flavor, case.get("repo_name"))
    assert text == _GOLDEN["refactoring_plan"][case["name"]][flavor]


@pytest.mark.parametrize("case", _PLANS, ids=lambda c: c["name"])
def test_refactoring_plan_spec_matches_golden(case: dict) -> None:
    text = render_plan_spec(case["detail"])
    assert text == _GOLDEN["refactoring_plan_spec"][case["name"]]


@pytest.mark.parametrize("flavor", FLAVORS)
@pytest.mark.parametrize("case", _OPPORTUNITIES, ids=lambda c: c["name"])
def test_refactoring_opportunity_matches_golden(case: dict, flavor: str) -> None:
    text = render_opportunity(case["detail"], flavor, case.get("repo_name"))
    assert text == _GOLDEN["refactoring_opportunity"][case["name"]][flavor]
