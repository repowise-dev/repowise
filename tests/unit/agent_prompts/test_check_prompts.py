"""Check-tool prompts: core is the source, the ui copy is held equal to it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repowise.core.agent_prompts import CHECK_PROMPTS
from repowise.core.savings.reporting import CHECK_TOOLS

_TS = (
    Path(__file__).resolve().parents[3] / "packages" / "ui" / "src" / "savings" / "check-prompts.ts"
)


def test_the_ui_prompts_are_the_core_prompts() -> None:
    block = re.search(
        r"export const CHECK_PROMPTS = \{(.*?)\} as const;", _TS.read_text(encoding="utf-8"), re.S
    )
    assert block, "CHECK_PROMPTS is not declared in check-prompts.ts"
    prompts = {
        key: json.loads(f'"{value}"')
        for key, value in re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', block.group(1))
    }
    assert prompts == CHECK_PROMPTS


def test_every_check_tool_has_an_ask_that_names_repowise() -> None:
    assert set(CHECK_PROMPTS) == {*CHECK_TOOLS, "switch_profile"}
    assert all("Repowise" in prompt for prompt in CHECK_PROMPTS.values())
