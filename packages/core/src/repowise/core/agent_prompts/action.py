"""One next action, handed to an agent cold: the claim, the evidence, how to get
the rest, and what finished looks like.

Takes the action's wire dict (``Action.as_dict()``), so the evidence is already
capped the way every surface sees it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from repowise.core.analysis.health.biomarker_labels import biomarker_label

from .preamble import preamble
from .sections import and_more, bullet_list, closing_sections, count, join_sections, repo_suffix

_CONSTRAINTS = (
    "**Confirm each item before changing anything.** The analysis is stored, not live; the code may have moved since it ran.",
    "**Stay inside what the action names.** Work only on the files and functions listed. If the right fix is elsewhere, stop and say where and why.",
    "**Keep behaviour unless the action is about behaviour.** Refactors and test additions must not change what the code does; run the tests that cover each file before and after.",
    "**Say when an item is wrong.** If a finding does not hold, or its fix would cost more than it saves, report that rather than forcing an edit.",
)

_EXPECTED = (
    "1. For each item you looked at: whether it held, and what you changed or why you left it.",
    "2. The tests you ran, before and after.",
    "3. What remains against the done condition.",
)

_COMMIT = re.compile(r"[0-9a-f]{7,40}")


def _short(symbol: str) -> str:
    return symbol.split("::")[-1]


def _detail_line(d: Mapping[str, Any]) -> str:
    line = d.get("line")
    where = f"`{d['path']}{f':{line}' if line else ''}`"
    what = ", ".join(
        p
        for p in (
            f"`{_short(d['symbol'])}`" if d.get("symbol") else None,
            biomarker_label(d["marker"]) if d.get("marker") else None,
            d.get("severity"),
        )
        if p
    )
    ref = d.get("ref")
    commit = f" (commit {ref[:10]})" if ref and _COMMIT.fullmatch(ref) else ""
    reason = "\n  - " + d["reason"] if d.get("reason") else ""
    return f"- {where}{f': {what}' if what else ''}{commit}{reason}"


def render_action(action: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None) -> str:
    use_mcp = flavor == "claude-code-mcp"
    details = action["details"]
    total = action["details_total"]
    more = total - len(details)
    path, symbol = action["target"]["path"], action["target"].get("symbol")
    target = f"`{path}` (`{_short(symbol)}`)" if symbol else f"`{path}`" if path else None

    commands = []
    for c in action["commands"]:
        line = (c.get("mcp") or c.get("cli")) if use_mcp else (c.get("cli") or c.get("mcp"))
        if line:
            commands.append(f"- {c['purpose']}:\n  `{line}`")

    if details:
        evidence = join_sections(
            [
                f"## Evidence ({count(len(details))} of {count(total)}{', worst first' if more > 0 else ''})",
                "",
                "\n".join(_detail_line(d) for d in details),
                and_more(
                    more,
                    prefix="\n",
                    suffix='. The first call under "Look closer" returns them.',
                ),
                "",
            ]
        )
    elif action["includes"]:
        evidence = join_sections(
            ["## Files", "", "\n".join(f"- `{p}`" for p in action["includes"]), ""]
        )
    else:
        evidence = ""

    look_closer = (
        join_sections(
            [
                "## Look closer (Repowise MCP)" if use_mcp else "## Look closer (Repowise CLI)",
                "",
                "\n".join(commands),
                "",
            ]
        )
        if commands
        else ""
    )
    command = action.get("command")

    return join_sections(
        [
            preamble(flavor, "action"),
            "",
            f"## Action{repo_suffix(repo_name)}",
            "",
            f"**{action['title']}**",
            "",
            action["impact"],
            "",
            "## Why it is on the list",
            "",
            bullet_list(
                [
                    f"Target: {target}" if target else None,
                    *(
                        f"{w['label']}: {w['value']}"
                        + ("" if w["basis"] == "measured" else f" ({w['basis']})")
                        for w in action["why"]
                    ),
                    f"Effort estimate: {action['effort']}; confidence in the facts: {action['confidence']}",
                ]
            ),
            "",
            evidence,
            look_closer,
            "## Done when",
            "",
            action["done_when"] + (f"\n\nCommand: `{command}`" if command else ""),
            "",
            *closing_sections(_CONSTRAINTS, _EXPECTED),
        ]
    )
