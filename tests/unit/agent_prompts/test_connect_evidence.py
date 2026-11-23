"""The "Connect evidence" prompt: core is the source, the ui copy is held equal to it.

The ui side is read as text, like ``tests/unit/health/test_step_verify_parity.py``:
the values are string literals and a TypeScript parser is a dependency this
suite lacks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from repowise.core.agent_prompts import CONNECT_EVIDENCE, FLAVORS, render_connect_evidence
from repowise.core.agent_prompts.preamble import ROLE, WORKSPACE_ROLE

_PROMPTS = Path(__file__).resolve().parents[3] / "packages" / "ui" / "src" / "health" / "ai-prompts"


def _ts(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8")


def _literal(text: str) -> str:
    """A double-quoted TypeScript string literal's value."""
    return json.loads(f'"{text}"')


def test_the_ui_copy_is_the_core_text() -> None:
    match = re.search(r'export const CONNECT_EVIDENCE =\s*"((?:[^"\\]|\\.)*)";', _ts("setup-prompt.ts"))
    assert match, "CONNECT_EVIDENCE is not declared in setup-prompt.ts"
    assert _literal(match.group(1)) == CONNECT_EVIDENCE


@pytest.mark.parametrize(("name", "core"), [("REPO_ROLE", ROLE), ("WORKSPACE_ROLE", WORKSPACE_ROLE)])
def test_the_ui_roles_are_the_core_roles(name: str, core: dict[str, str]) -> None:
    block = re.search(
        rf"export const {name}: PromptRole = \{{(.*?)\}};", _ts("shared.ts"), re.S
    )
    assert block, f"{name} is not declared in shared.ts"
    roles = {
        key.strip('"'): _literal(value)
        for key, value in re.findall(r'([\w"-]+):\s*"((?:[^"\\]|\\.)*)"', block.group(1))
    }
    assert roles == core


@pytest.mark.parametrize("flavor", FLAVORS)
def test_the_prompt_runs_setup_and_follows_next_call_without_stack_commands(flavor: str) -> None:
    prompt = render_connect_evidence(flavor)

    assert prompt.startswith(ROLE[flavor] + "\n\n")
    assert "`repowise evidence setup --format json`" in prompt
    assert "`next_call`" in prompt
    for stack_command in ("pytest", "go test", "npm", "pyinstrument", "--cov"):
        assert stack_command not in prompt
