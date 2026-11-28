"""CI renderings of a change's risk: markdown, GitHub annotations, the gate.

The gate is a size gate: it reads only ``risk_percentile``, the change's
repo-relative rank by diff size and spread (``risk_semantics.change_risk_authority``),
never the 0-10 diff-shape score. A change with no rank cannot be gated, which is
the caller's cue to exit "could not evaluate" with :func:`unranked_reason`.

Workflow-command and markdown mechanics come from :mod:`repowise.core.ci`,
shared with every other CI gate.
"""

from __future__ import annotations

import math
from typing import Literal

from ...ci import github
from ...ci.markdown import ROW_LIMIT, cell, more_line, plural
from ...ci.summary import FAIL, PASS, GateSummary
from .service import _MIN_BASELINE, ChangeRiskResult

PercentileGate = Literal["pass", "fail", "no_data", "not_set"]

#: What the rank cannot say, from docs/layers/CHANGE_RISK.md "Accuracy and limits".
LIMITS = "The rank is a diff-size statistic: it barely beats churn alone and cannot rank danger."


def percentile_gate(result: ChangeRiskResult, threshold: float | None) -> PercentileGate:
    """``fail`` when the change ranks above *threshold*; unrounded, like every gate."""
    if threshold is None:
        return "not_set"
    if result.percentile is None:
        return "no_data"
    return "fail" if result.percentile > threshold else "pass"


def unranked_reason(result: ChangeRiskResult, baseline: int) -> str:
    """Why the change has no percentile, and what would give it one."""
    if not baseline:
        return (
            "The change-risk gate ranks the change against recent commits, and "
            "--baseline 0 turns that off. Drop --baseline 0."
        )
    return (
        f"Only {plural(result.baseline_sample_size, 'recent commit')} could be sampled; "
        f"ranking a change needs at least {_MIN_BASELINE}. A shallow clone lacks the "
        "history: fetch it (fetch-depth: 0, or git fetch --unshallow)."
    )


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 93 -> '93rd', 11 -> '11th'."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _decimals(value: float) -> str:
    """*value* with the fewest decimals that keep it off a whole number (95.4, 95.001)."""
    for places in range(1, 10):
        text = f"{value:.{places}f}".rstrip("0").rstrip(".")
        if "." in text:
            return text
    return str(round(value))


def _rank_text(value: float) -> str:
    """An ordinal for a whole number (``95th``), else its decimals (``95.4th``)."""
    return ordinal(round(value)) if value == round(value) else f"{_decimals(value)}th"


def percentile_number(result: ChangeRiskResult, threshold: float | None = None) -> str | None:
    """The change's rank as a number, rounded unless rounding would contradict the gate."""
    return _percentile_number(result.percentile, threshold)


def _percentile_number(pct: float | None, threshold: float | None) -> str | None:
    if pct is None:
        return None
    shown = round(pct)
    # 95.4 rounds to 95, which reads as "not above the 95th percentile gate".
    if threshold is not None and (pct > threshold) != (shown > threshold):
        return _decimals(pct)
    return str(shown)


def size_text(percentile: float | None, threshold: float | None = None) -> str:
    """The lead line every surface shows: the one percentile, named for what it ranks.

    The rank reads as an ordinal (``97th``, or ``95.4th`` beside a 95 gate).
    """
    number = _percentile_number(percentile, threshold)
    if number is None:
        return "Size and spread: unranked (no baseline to rank it against)"
    rank = f"{number}th" if "." in number else ordinal(int(number))
    return f"Size and spread: {rank} percentile of recent commits (diff size, not a bug predictor)"


def headline(result: ChangeRiskResult, threshold: float | None, *, markdown: bool = True) -> str:
    """The verdict in words: which side of the gate, then how the change ranks."""
    bold = "**" if markdown else ""
    number = percentile_number(result, threshold)
    if number is None:
        # True for --baseline 0 and for a history of fewer than the minimum.
        return f"{bold}Size and spread: unranked{bold} · no baseline to rank it against"
    if "." not in number:
        # "Larger than N%" is a lower bound: floor it, and never claim all 100%.
        number = str(min(math.floor(result.percentile), 99))
    rank = f"larger and more spread out than {number}% of recent commits"
    gate = percentile_gate(result, threshold)
    if gate == "fail":
        return f"{bold}Above the {_rank_text(threshold)} percentile size gate{bold}: {rank}"
    if gate == "pass":
        return f"{bold}Within the {_rank_text(threshold)} percentile size gate{bold}: {rank}"
    return f"{bold}Size and spread{bold}: {rank}"


def gate_text(result: ChangeRiskResult, threshold: float | None) -> str:
    """``"above the 95th percentile size gate"``, ``"within ..."``, or ``""`` ungated."""
    gate = percentile_gate(result, threshold)
    if gate == "fail":
        return f"above the {_rank_text(threshold)} percentile size gate"
    if gate == "pass":
        return f"within the {_rank_text(threshold)} percentile size gate"
    return ""


def fix_history_text(result: ChangeRiskResult) -> str:
    """Where the change lands: the bug-fix record of the files it touches.

    No percentile: the size rank is the one percentile on screen, and the fix
    record reads plainest as the files and their recency-weighted fix counts.
    """
    if not result.fix_history_available:
        return "fix history unavailable (the git history walk failed)"
    if not result.hot_files:
        return "no bug-fix history in the files it touches"
    return "touches files with prior bug fixes"


def scope_line(result: ChangeRiskResult, *, markdown: bool = True) -> str:
    """The change, the sample it was ranked against, and its fix history."""
    ref = result.features.ref
    parts = [f"`{ref}`" if markdown else ref]
    if result.baseline_sample_size:
        parts.append(f"ranked against {plural(result.baseline_sample_size, 'recent commit')}")
    features = result.features
    parts.append(
        f"+{features.la} / -{features.ld} lines in {plural(features.nf, 'file')} "
        f"across {plural(features.nd, 'directory', 'directories')}"
    )
    parts.append(fix_history_text(result))
    return " · ".join(parts)


def render_markdown(result: ChangeRiskResult, threshold: float | None = None) -> str:
    """Markdown for a CI step summary or merge-request report."""
    out = [headline(result, threshold), "", scope_line(result)]
    hot = result.hot_files
    if hot:
        out += ["", "| File | Changed lines | Recent bug fixes |", "|---|---|---|"]
        out += [
            f"| `{cell(path)}` | {churn} | {pressure:.1f} |"
            for path, churn, pressure in hot[:ROW_LIMIT]
        ]
        if len(hot) > ROW_LIMIT:
            out += ["", more_line(len(hot) - ROW_LIMIT, "files with bug-fix history")]
        out += ["", "Bug fixes are recency-weighted: one from a year earlier counts a half."]
    out += ["", LIMITS]
    return "\n".join(out) + "\n"


def gate_summary(result: ChangeRiskResult, threshold: float | None = None) -> GateSummary:
    """The gate's row in the combined CI summary: the rank, with no findings."""
    verdict = FAIL if percentile_gate(result, threshold) == "fail" else PASS
    return GateSummary("risk", verdict, headline(result, threshold, markdown=False))


def github_annotations(result: ChangeRiskResult, threshold: float | None = None) -> list[str]:
    """An ``::error::`` when the gate fails, else a ``::notice::`` naming the rank."""
    text = headline(result, threshold, markdown=False)
    if percentile_gate(result, threshold) == "fail":
        return [github.error(text)]
    return [github.notice(text)]
