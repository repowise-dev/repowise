"""First prompts: core is the source, the ui copy is held equal to it.

The ui side is read as text, like ``test_connect_evidence.py``: the values are
string literals and a TypeScript parser is a dependency this suite lacks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repowise.core.agent_prompts import FIRST_PROMPTS, first_prompt, first_prompts, workspace_prompt

_TS = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "ui"
    / "src"
    / "onboarding"
    / "first-prompts.ts"
)


def test_the_ui_templates_are_the_core_templates() -> None:
    block = re.search(
        r"export const FIRST_PROMPTS = \{(.*?)\} as const;", _TS.read_text(encoding="utf-8"), re.S
    )
    assert block, "FIRST_PROMPTS is not declared in first-prompts.ts"
    templates = {
        key: json.loads(f'"{value}"')
        for key, value in re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', block.group(1))
    }
    assert templates == FIRST_PROMPTS


def test_every_prompt_names_repowise() -> None:
    assert all("Repowise" in template for template in FIRST_PROMPTS.values())


def test_facts_pick_the_specific_questions() -> None:
    prompts = first_prompts("orbit", hotspot="src/api.py", decision="Use SQLite")
    assert prompts == [
        "Use Repowise to give me a tour of orbit: what it does, its main modules, "
        "and where I should start reading.",
        "Before I change src/api.py, use Repowise to tell me what depends on it, "
        "who knows it best, and what tends to break with it.",
        'Use Repowise to explain the decision "Use SQLite" in orbit: why it was made '
        "and which code follows it.",
    ]


def test_missing_facts_fall_back_to_questions_any_repo_answers() -> None:
    worst_only = first_prompts("orbit", worst="src/legacy.py")
    assert "src/legacy.py is the least healthy file in orbit" in worst_only[1]
    bare = first_prompts("orbit")
    assert bare[1] == FIRST_PROMPTS["risky"].format(repo="orbit")
    assert bare[2] == FIRST_PROMPTS["unused"].format(repo="orbit")


def test_the_one_first_prompt_is_the_hotspot_else_the_tour() -> None:
    assert first_prompt("orbit", hotspot="src/api.py") == FIRST_PROMPTS["hotspot"].format(
        path="src/api.py"
    )
    assert first_prompt("orbit") == FIRST_PROMPTS["tour"].format(repo="orbit")


def test_the_workspace_prompt_names_a_few_repos_and_counts_many() -> None:
    assert workspace_prompt(["api", "web", "worker"]) == (
        "Use Repowise to show me how api, web and worker depend on each other: "
        "the contracts between them and which changes ripple."
    )
    assert "how the 5 repos in this workspace depend" in workspace_prompt(list("abcde"))
    assert workspace_prompt(["api"]) == FIRST_PROMPTS["tour"].format(repo="api")
