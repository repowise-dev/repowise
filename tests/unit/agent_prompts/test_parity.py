"""Core's agent prompts render the bytes the web builders do.

``tests/fixtures/agent_prompts/golden.json`` is written by the web side
(``packages/ui/__tests__/health/agent-prompt-parity.test.ts``); this checks core
against the same file, so a wording change on one side fails on the other.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from repowise.core.agent_prompts import (
    FLAVORS,
    render_action,
    render_fix_item,
    render_verify_lines,
)

_DIR = pathlib.Path(__file__).resolve().parents[2] / "fixtures/agent_prompts"


def _load(name: str) -> dict:
    return json.loads((_DIR / name).read_text(encoding="utf-8"))


_GOLDEN = _load("golden.json")
_ACTIONS = _load("actions.json")["cases"]
_ITEMS = _load("fix_items.json")["cases"]


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
