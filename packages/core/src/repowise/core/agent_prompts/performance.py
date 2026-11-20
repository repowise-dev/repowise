"""One performance cause the queue admits, handed to an agent: where the cost
comes from, the stored plan's steps, and how to show the change is safe.

Takes the cause's detail (``first_look.prompts.perf_detail`` builds it from the
stored opportunity and its plan), so the steps are the plan every surface shows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .preamble import preamble
from .sections import bullet_list, closing_sections, join_sections, repo_suffix

_CONSTRAINTS = (
    "**Confirm the cost before changing anything.** The analysis is static unless it says measured; read the loop and what it iterates.",
    "**Keep behaviour.** Same results, same order where order matters, same errors; run the tests that reach the function before and after.",
    "**Stay inside the function the cause names.** If the right fix is elsewhere (a caller, a query), stop and say where and why.",
    "**Say when it does not hold.** If the loop is bounded small in practice, or the fix costs more than it saves, report that rather than editing.",
)

_EXPECTED = (
    "1. Whether the cause held, and the change you made or why you left it.",
    "2. The tests you ran, before and after.",
    "3. Anything the change needs that you could not do (a characterization test, a measurement).",
)


def render_perf_opportunity(
    detail: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    path, line, symbol = detail["file_path"], detail.get("line"), detail.get("symbol")
    where = f"`{path}{f':{line}' if line else ''}`"
    steps = [
        f"{s['order']}. {s['action']}"
        + (f" (`{s['file_path']}:{s['line']}`)" if s.get("file_path") and s.get("line") else "")
        for s in detail.get("steps") or ()
    ]
    look_closer = (
        join_sections(
            [
                "## Look closer (Repowise MCP)",
                "",
                f"- The full record, with its plan and the tests that reach it:\n  `get_health(opportunity_id=\"{detail['opportunity_id']}\")`",
                "",
            ]
        )
        if flavor == "claude-code-mcp" and detail.get("opportunity_id")
        else ""
    )
    return join_sections(
        [
            preamble(flavor, "perf_opportunity"),
            "",
            f"## Performance cause{repo_suffix(repo_name)}",
            "",
            f"**{detail['marker_label']}** in " + (f"`{symbol}` at " if symbol else "") + where,
            "",
            detail.get("rationale") or "",
            "## Why it is on the list",
            "",
            bullet_list(
                [
                    f"Basis: {'measured by a profile run' if detail.get('measured') else 'static analysis (inferred)'}",
                    f"Runs as: {detail['role']}" if detail.get("role") and detail["role"] != "unknown" else None,
                    f"Effort estimate: {detail.get('effort') or 'M'}; confidence in the facts: {detail.get('confidence') or 'medium'}",
                ]
            ),
            "",
            "## Steps",
            "",
            "\n".join(steps) if steps else "Read the function and remove the repeated work the cause names.",
            "",
            *(["## Before you edit", "", detail["prerequisite"], ""] if detail.get("prerequisite") else []),
            look_closer,
            *closing_sections(_CONSTRAINTS, _EXPECTED),
        ]
    )


__all__ = ["render_perf_opportunity"]
