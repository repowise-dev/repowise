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


def _heading(detail: Mapping[str, Any]) -> str:
    path, line, symbol = detail["file_path"], detail.get("line"), detail.get("symbol")
    where = f"`{path}{f':{line}' if line else ''}`"
    return f"**{detail['marker_label']}** in " + (f"`{symbol}` at " if symbol else "") + where


def _why(detail: Mapping[str, Any]) -> str:
    basis = "measured by a profile run" if detail.get("measured") else "static analysis (inferred)"
    role = detail.get("role")
    return bullet_list(
        [
            f"Basis: {basis}",
            f"Runs as: {role}" if role and role != "unknown" else None,
            f"Effort estimate: {detail.get('effort') or 'M'}; "
            f"confidence in the facts: {detail.get('confidence') or 'medium'}",
        ]
    )


def _step(s: Mapping[str, Any]) -> str:
    at = f" (`{s['file_path']}:{s['line']}`)" if s.get("file_path") and s.get("line") else ""
    return f"{s['order']}. {s['action']}{at}"


def _steps(detail: Mapping[str, Any]) -> str:
    steps = [_step(s) for s in detail.get("steps") or ()]
    return "\n".join(steps) if steps else "Read the function and remove the repeated work the cause names."


def _look_closer(detail: Mapping[str, Any], flavor: str) -> str:
    if flavor != "claude-code-mcp" or not detail.get("opportunity_id"):
        return ""
    call = f'get_health(opportunity_id="{detail["opportunity_id"]}")'
    return join_sections(
        [
            "## Look closer (Repowise MCP)",
            "",
            f"- The full record, with its plan and the tests that reach it:\n  `{call}`",
            "",
        ]
    )


def render_perf_opportunity(
    detail: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    prerequisite = detail.get("prerequisite")
    return join_sections(
        [
            preamble(flavor, "perf_opportunity"),
            "",
            f"## Performance cause{repo_suffix(repo_name)}",
            "",
            _heading(detail),
            "",
            detail.get("rationale") or "",
            "## Why it is on the list",
            "",
            _why(detail),
            "",
            "## Steps",
            "",
            _steps(detail),
            "",
            *(["## Before you edit", "", prerequisite, ""] if prerequisite else []),
            _look_closer(detail, flavor),
            *closing_sections(_CONSTRAINTS, _EXPECTED),
        ]
    )

__all__ = ["render_perf_opportunity"]
