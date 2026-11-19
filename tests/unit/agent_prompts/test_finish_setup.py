"""The finish-setup prompt: core is the source, the ui copy is held equal to it.

Read as text like ``test_connect_evidence.py``: the value is one string literal.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from repowise.core.agent_prompts import (
    FINISH_SETUP,
    FLAVORS,
    render_action,
    render_connect_evidence,
    render_finish_setup,
    render_finish_setup_workspace,
)
from repowise.core.agent_prompts.preamble import ROLE, WORKSPACE_ROLE
from repowise.core.analysis.actions.rules import setup as rules
from repowise.core.analysis.actions.setup import FoundFile, SetupFacts

_PROMPTS = Path(__file__).resolve().parents[3] / "packages" / "ui" / "src" / "health" / "ai-prompts"

#: Words the house style keeps out of anything a person or an agent reads.
_BANNED = ("say so", "rather", "tell me", "curious", "honestly", "—")


def test_the_ui_copy_is_the_core_text() -> None:
    text = (_PROMPTS / "setup-prompt.ts").read_text(encoding="utf-8")
    match = re.search(r'export const FINISH_SETUP =\s*"((?:[^"\\]|\\.)*)";', text)
    assert match, "FINISH_SETUP is not declared in setup-prompt.ts"
    assert json.loads(f'"{match.group(1)}"') == FINISH_SETUP


@pytest.mark.parametrize("flavor", FLAVORS)
def test_the_prompt_runs_the_next_loop_and_asks_at_most_once(flavor: str) -> None:
    prompt = render_finish_setup(flavor)
    assert prompt.startswith(ROLE[flavor] + "\n\n")
    assert "`repowise next --format json`" in prompt
    assert "`improve_signal`" in prompt
    assert "ask me one question, once" in prompt
    assert "at most six lines" in prompt
    assert "`repowise next --dismiss setup`" in prompt
    for stack_command in ("pytest", "npm", "--cov"):
        assert stack_command not in prompt


def test_the_copy_keeps_the_house_style() -> None:
    from repowise.core.setup_state import MCP_SETUP_LINE, session_setup_line

    for text in (FINISH_SETUP, MCP_SETUP_LINE, session_setup_line(4)):
        for word in _BANNED:
            assert word not in text.lower(), word


def test_the_workspace_form_names_each_member_then_runs_the_same_loop() -> None:
    prompt = render_finish_setup_workspace(["services/payments-api", "web-checkout"])
    assert prompt.startswith(WORKSPACE_ROLE["generic"] + "\n\n")
    assert "senior engineer in this repository" not in prompt
    assert "this workspace: `services/payments-api`, `web-checkout`." in prompt
    assert prompt.endswith(FINISH_SETUP)


def test_a_setup_row_prompt_is_its_command_not_a_code_change() -> None:
    facts = SetupFacts(sync_hook=False, unsynced_commits=3)
    action = rules.sync_hook_missing(facts).actions[0].as_dict()
    prompt = render_action(action, "claude-code", "orbit-api")
    assert prompt.startswith(ROLE["claude-code"])
    assert "(`orbit-api`): Keep the index in sync on commit." in prompt
    assert "run `repowise hook install`" in prompt
    assert "commits since last sync: 3" in prompt
    assert "Confirm each item" not in prompt


@pytest.mark.parametrize("rule", ["evidence_found", "runtime_missing"])
def test_an_evidence_row_hands_over_the_evidence_loop(rule: str) -> None:
    facts = SetupFacts(
        runtime_stored=False, evidence_files=(FoundFile("a.speedscope.json", 0, "speedscope"),)
    )
    action = {**rules.evidence_found(facts).actions[0].as_dict(), "rule": rule}
    assert render_action(action, "cursor") == render_connect_evidence("cursor")
