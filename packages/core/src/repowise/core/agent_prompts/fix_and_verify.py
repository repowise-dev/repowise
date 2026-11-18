"""The "Fix and verify" prompt, and the timing lines every Verify block shares.

A performance plan's ``verify.measure`` (``perf_measure.verify_measure``) says
how to time the change: ``repowise perf measure --save`` before the edit and
``--against`` after it, on the workload ``evidence setup`` names for the
plan's unit. :func:`measure_lines` words it once, for the plan prompt's and
the Fix-first item's Verify blocks; :func:`render_fix_and_verify` hands the
whole loop to an agent: measure, change, test, measure, report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .preamble import ROLE


def _where(measure: Mapping[str, Any]) -> str:
    cwd = measure.get("cwd")
    return f"from `{cwd}`" if cwd else "from the repository root"


def _workload(measure: Mapping[str, Any]) -> str:
    kind = str(measure.get("workload_class") or "workload").replace("_", " ")
    return f"`{measure['workload']}` ({kind})"


def _verdict_rule(measure: Mapping[str, Any]) -> str:
    """How to report the timing: the verdict with its interval, never "faster" on inconclusive."""
    return (
        "Report the verdict `perf measure` prints (improved, regressed or inconclusive) with "
        "its ratio and interval. Call the change faster only when the verdict is improved: "
        f"inconclusive means the {measure['metric']} did not move by more than "
        f"{measure['tolerance']:.0%} or twice the measured noise. The timing informs; "
        "the tests decide."
    )


def measure_lines(measure: Mapping[str, Any]) -> list[str]:
    """The timing part of a Verify block: before and after, then how to report it."""
    return [
        f"Time {_workload(measure)} {_where(measure)}. Before the edit:",
        "",
        "```",
        measure["before"],
        "```",
        "",
        "After it, once the tests pass:",
        "",
        "```",
        measure["after"],
        "```",
        "",
        _verdict_rule(measure),
    ]


def _fence(lines: Sequence[str]) -> str:
    return "\n".join(["```", *lines, "```"])


def render_fix_and_verify(check: Mapping[str, Any], flavor: str = "generic") -> str:
    """The prompt for one performance plan's check (``recipe.verify_check``),
    which must carry ``measure``: who the agent is, then the loop."""
    measure = check["measure"]
    commands = check.get("commands") or []
    tests = (
        f"3. Run the plan's tests; they must pass:\n\n{_fence(commands)}"
        if commands
        else "3. Run the tests that exercise the change; they must pass. Add one first if none does."
    )
    return "\n\n".join(
        [
            ROLE[flavor],
            f"Make the performance change and show whether it worked, timing {_workload(measure)}.",
            f"1. Before you edit, save a baseline {_where(measure)}:\n\n{_fence([measure['before']])}",
            "2. Make the change.",
            tests,
            f"4. Measure again against the baseline, from the same directory:\n\n"
            f"{_fence([measure['after']])}",
            f"5. {_verdict_rule(measure)} If it regressed, say so and stop.",
        ]
    )


__all__ = ["measure_lines", "render_fix_and_verify"]
