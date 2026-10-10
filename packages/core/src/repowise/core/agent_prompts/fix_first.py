"""One Fix-first item, handed to an agent: the change, why it ranks, the steps in
order, and how to verify it.

Takes the item's wire dict (``FixItem.as_dict()``) and only arranges fields core
already worded; the sentences themselves come from ``fix_first.text``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .preamble import preamble
from .sections import and_more, bullet_list, closing_sections, join_sections, repo_suffix

_CONSTRAINTS = (
    "**Confirm before changing.** The analysis is stored, not live; the code may have moved since it ran.",
    "**Keep behaviour.** This is a structural or performance change; what the code returns must not change.",
    "**Run Verify before and after.** If no guarding test exists, write one that pins today's behaviour first.",
    "**Say when it is wrong.** If the problem does not hold, or the change costs more than it saves, report that and stop.",
)

_EXPECTED = (
    "1. Whether the problem held, and what you changed.",
    "2. The Verify run, before and after, with its result.",
    "3. Anything you left undone, and why.",
)


def _where(target: Mapping[str, Any]) -> str:
    start = target.get("line_start")
    loc = f"`{target['file_path']}{f':{start}' if start else ''}`"
    symbol = target.get("symbol")
    return f"{loc} (`{symbol.split('::')[-1]}`)" if symbol else loc


def render_verify_lines(item: Mapping[str, Any]) -> str:
    """The Verify block, worded the way the card words it."""
    verify = item["verify"]
    tests = verify["tests"]
    if not tests:
        return verify.get("prerequisite") or (
            "No guarding tests found. Write a test that pins the current behaviour before you change anything."
        )
    listed = "\n".join(
        f"- `{t['path']}`" + (f": {t['reason']}" if t.get("reason") else "") for t in tests
    )
    command = verify.get("command")
    return join_sections(
        [
            listed,
            and_more(verify["tests_total"] - len(tests), prefix="- "),
            f"\nRun: `{command}`" if command else "",
        ]
    )


def _step(s: Mapping[str, Any]) -> str:
    line = s.get("line")
    loc = f"{s['file_path']}{f':{line}' if line else ''}"
    tag = " [mechanical]" if s.get("mechanical") else " [judgment]"
    extra = [
        f"   {label}: `{s[key]}`"
        for key, label in (("signature", "Helper"), ("call", "Call"), ("command", "Check"))
        if s.get(key)
    ]
    return "\n".join([f"{s['order']}. {s['text']} (`{loc}`){tag}", *extra])


def render_fix_item(item: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None) -> str:
    use_mcp = flavor == "claude-code-mcp"
    call = item["next_call"]["mcp"]
    action = item["action"]
    steps = [_step(s) for s in action["steps"]]
    more_steps = action["steps_total"] - len(action["steps"])

    return join_sections(
        [
            preamble(flavor, "fix_first"),
            "",
            f"## Fix first{repo_suffix(repo_name)}",
            "",
            f"**{item['title']}**",
            "",
            item["why"],
            "",
            "## Facts",
            "",
            bullet_list(
                [
                    f"Target: {_where(item['target'])}",
                    f"Gain: {item['gain']['text']}",
                    f"Effort: {item['effort']['bucket']} ({item['effort']['basis']})",
                    f"Risk: {item['risk']['text']}",
                    f"Confidence: {item['confidence']['level']}. {item['confidence']['reason']}",
                    *(
                        f"{fact['label']}: {fact['value']}"
                        + ("" if fact["basis"] == "measured" else f" ({fact['basis']})")
                        for fact in item["facts"]
                    ),
                ]
            ),
            "",
            join_sections(
                [
                    "## Steps, in order",
                    "",
                    "\n".join(steps),
                    and_more(more_steps, suffix="; the lookup below returns them."),
                    "",
                ]
            )
            if steps
            else "",
            "## Verify",
            "",
            render_verify_lines(item),
            "",
            join_sections(["## Look closer (Repowise MCP)", "", f"`{call}`", ""]) if use_mcp else "",
            *closing_sections(_CONSTRAINTS, _EXPECTED),
        ]
    )
