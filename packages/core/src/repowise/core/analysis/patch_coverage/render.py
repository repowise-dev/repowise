"""Human renderings of :class:`PatchCoverage`: markdown, plain text, CI annotations.

Markdown is what a CI step summary or a pull-request comment shows, so it is
written to be read once and acted on: the verdict in words on the first line
with its denominator, the diff, report and file counts on the second, and
detail only for what needs attention. A passing change with no gaps is two
lines. A file the report never named reads "not in report", never 0%.

Workflow-command and markdown mechanics (escaping, caps, collapsed lists)
come from :mod:`repowise.core.ci`, shared with every other CI gate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ...ci import github
from ...ci.markdown import ROW_LIMIT, cell, details, more_line
from .compute import FilePatchCoverage, PatchCoverage

#: Uncovered ranges shown per file before "+N more".
RANGE_LIMIT = 8

#: How a file that could not be measured reads, wherever it is listed.
STATUS_TEXT = {
    "not_in_report": "not in report",
    "no_line_data": "report has no line data",
    "no_coverable_changes": "no executable lines changed",
}


def headline(pc: PatchCoverage, *, markdown: bool = True) -> str:
    """One-sentence verdict. Bold on the figure only in markdown."""
    bold = "**" if markdown else ""
    pct = pc.patch_coverage_pct
    if pct is None:
        text = f"{bold}Patch coverage: no measurable changed lines.{bold} {_no_data_reason(pc)}"
        if pc.threshold is not None:
            text += f" The {fmt_pct(pc.threshold)} gate was not applied."
        return text
    text = (
        f"{bold}Patch coverage {fmt_pct(pct)}{bold} ({pc.covered_line_count} of "
        f"{pc.coverable_line_count} changed executable lines covered)"
    )
    if pc.gate == "fail":
        text += f" · below the {fmt_pct(pc.threshold)} gate"
    elif pc.gate == "pass":
        text += f" · meets the {fmt_pct(pc.threshold)} gate"
    return text


def scope_line(pc: PatchCoverage, *, markdown: bool = True) -> str:
    """The diff, the reports and how many changed files were measured."""
    scope = pc.scope
    parts = []
    if scope.label:
        parts.append(f"`{scope.label}`" if markdown else scope.label)
    if scope.source_formats:
        parts.append(", ".join(scope.source_formats))
    measured = len(pc.with_status("measured"))
    files = f"{measured} of {pc.changed_file_count} changed files measured"
    if pc.out_of_scope_count:
        files += f", {pc.out_of_scope_count} out of scope"
    parts.append(files)
    if scope.freshness == "stale":
        at = f" at {scope.measured_commit[:7]}" if scope.measured_commit else ""
        parts.append(f"coverage was measured{at}, not at this change's head")
    if scope.unmatched_report_path_count:
        parts.append(
            f"{scope.unmatched_report_path_count} of {scope.report_path_count} report paths "
            "did not match a file in this repository"
        )
    return " · ".join(parts)


def attention_rows(pc: PatchCoverage) -> list[FilePatchCoverage]:
    """Files a reader should look at: uncovered changes first, then unmeasurable ones."""
    gaps = sorted(
        (f for f in pc.with_status("measured") if f.uncovered_line_count),
        key=lambda f: (-f.uncovered_line_count, f.file_path),
    )
    return gaps + pc.with_status("not_in_report") + pc.with_status("no_line_data")


def render_markdown(pc: PatchCoverage) -> str:
    """Markdown for a CI step summary or PR comment."""
    out = [headline(pc), "", scope_line(pc)]
    gaps = [f for f in attention_rows(pc) if f.status == "measured"]
    if gaps:
        out += ["", "| File | Uncovered changed lines | Covered |", "|---|---|---|"]
        out += [
            f"| `{cell(f.file_path)}` | {format_ranges(f.uncovered_ranges, RANGE_LIMIT)} | "
            f"{f.covered_line_count} of {f.coverable_line_count} |"
            for f in gaps[:ROW_LIMIT]
        ]
        if len(gaps) > ROW_LIMIT:
            out += ["", more_line(len(gaps) - ROW_LIMIT, "files with uncovered changed lines")]
    out += _details(pc.with_status("not_in_report"), "not in the coverage report")
    out += _details(pc.with_status("no_line_data"), "in a report without line data")
    return "\n".join(out) + "\n"


def github_annotations(pc: PatchCoverage) -> list[str]:
    """GitHub Actions workflow commands for a change's patch coverage.

    A failed gate is an error. The largest uncovered ranges are marked, capped
    at what GitHub displays, with a notice counting the rest.
    """
    ranges = sorted(
        ((f.file_path, a, b) for f in pc.with_status("measured") for a, b in f.uncovered_ranges),
        key=lambda r: (r[1] - r[2], r[0], r[1]),
    )
    warnings = [
        github.annotation(
            "warning",
            f"Changed {_span(a, b)} not covered by tests",
            file=path,
            line=a,
            end_line=b,
            title="Uncovered change",
        )
        for path, a, b in ranges
    ]
    verdict = [github.error(headline(pc, markdown=False))] if pc.gate == "fail" else []
    return verdict + github.cap_annotations(warnings, noun="uncovered changed ranges")


def format_ranges(ranges: Sequence[tuple[int, int]], limit: int | None = None) -> str:
    """``((1, 3), (7, 7))`` -> ``"1-3, 7"``, with ``+N more`` past *limit*."""
    shown = ranges if limit is None else ranges[:limit]
    text = ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in shown)
    rest = len(ranges) - len(shown)
    return text + (f", +{rest} more" if rest else "")


def fmt_pct(value: float | None) -> str:
    """One decimal, floored, so a figure never reads as meeting a gate it missed."""
    return "n/a" if value is None else f"{math.floor(value * 10) / 10:.1f}%"


def _details(files: list[FilePatchCoverage], where: str) -> list[str]:
    if not files:
        return []
    noun = "file is" if len(files) == 1 else "files are"
    summary = f"{len(files)} changed {noun} {where}, so not counted"
    return ["", *details(summary, [f"`{cell(f.file_path)}`" for f in files])]


def _no_data_reason(pc: PatchCoverage) -> str:
    if not pc.changed_file_count:
        return "The change has no changed lines."
    if pc.with_status("no_line_data"):
        return "The report does not say which lines are executable."
    if pc.with_status("not_in_report"):
        return "The changed source files are not in the coverage report."
    if pc.with_status("no_coverable_changes"):
        return "The change touches no executable lines."
    return "Only tests or files the report does not measure changed."


def _span(a: int, b: int) -> str:
    return f"line {a}" if a == b else f"lines {a}-{b}"
