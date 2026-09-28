"""Human renderings of :class:`PatchCoverage`: markdown and CI annotations.

Markdown is what a CI step summary or a pull-request comment shows, so it is
written to be read once and acted on: the verdict in words on the first line
with its denominator, the diff and report it was measured from on the second,
and detail only for what needs attention. A passing change with no gaps is
two lines. A file the report never named reads "not in report", never 0%.
"""

from __future__ import annotations

from .compute import FilePatchCoverage, PatchCoverage

#: Files listed per section before the rest collapse into "and N more".
ROW_LIMIT = 10
#: Annotations emitted per run; CI systems cap and truncate beyond this.
ANNOTATION_LIMIT = 50


def headline(pc: PatchCoverage) -> str:
    """One-sentence verdict, markdown bold on the figure."""
    pct = pc.pct
    if pct is None:
        return "**Patch coverage: no measurable changed lines.** " + _no_data_reason(pc)
    text = (
        f"**Patch coverage {_fmt_pct(pct)}** "
        f"({pc.covered_lines} of {pc.coverable_lines} changed executable lines covered)"
    )
    if pc.gate == "fail":
        text += f" · below the {_fmt_pct(pc.threshold)} gate"
    elif pc.gate == "pass":
        text += f" · meets the {_fmt_pct(pc.threshold)} gate"
    return text


def render_markdown(pc: PatchCoverage) -> str:
    """Markdown for a CI step summary or PR comment."""
    out = [headline(pc), "", _scope_line(pc)]

    gaps = sorted(
        (f for f in pc.with_status("measured") if f.uncovered_lines),
        key=lambda f: (-f.uncovered_lines, f.path),
    )
    if gaps:
        out += ["", "| File | Uncovered changed lines | Covered |", "|---|---|---|"]
        for f in gaps[:ROW_LIMIT]:
            out.append(
                f"| `{f.path}` | {_ranges(f)} | {f.covered_lines} of {f.coverable_lines} |"
            )
        if len(gaps) > ROW_LIMIT:
            out.append(f"\nand {len(gaps) - ROW_LIMIT} more files with uncovered changed lines.")

    missing = pc.with_status("not_in_report")
    if missing:
        noun = "file is" if len(missing) == 1 else "files are"
        out += [
            "",
            "<details>",
            f"<summary>{len(missing)} changed {noun} not in the coverage report, "
            "so not counted</summary>",
            "",
            *(f"- `{f.path}`" for f in missing[:ROW_LIMIT]),
        ]
        if len(missing) > ROW_LIMIT:
            out.append(f"- and {len(missing) - ROW_LIMIT} more")
        out += ["", "</details>"]
    return "\n".join(out) + "\n"


def github_annotations(pc: PatchCoverage) -> list[str]:
    """GitHub Actions workflow commands marking uncovered changed lines."""
    lines: list[str] = []
    for f in pc.with_status("measured"):
        for start, end in f.uncovered_ranges:
            span = f"line {start}" if start == end else f"lines {start}-{end}"
            lines.append(
                f"::warning file={_escape_property(f.path)},line={start},endLine={end},"
                f"title=Uncovered change::Changed {span} not covered by tests"
            )
            if len(lines) >= ANNOTATION_LIMIT:
                return lines
    return lines


def _scope_line(pc: PatchCoverage) -> str:
    parts = []
    if pc.scope.label:
        parts.append(f"`{pc.scope.label}`")
    if pc.scope.report_formats:
        parts.append(", ".join(pc.scope.report_formats))
    if pc.scope.unmatched_report_paths:
        parts.append(
            f"{pc.scope.unmatched_report_paths} of {pc.scope.report_files} report paths "
            "did not match a file in this repository"
        )
    return " · ".join(parts) if parts else "Measured against the coverage report."


def _no_data_reason(pc: PatchCoverage) -> str:
    if pc.with_status("no_line_data"):
        return "The report does not say which lines are executable."
    if pc.with_status("not_in_report"):
        return "The changed source files are not in the coverage report."
    if pc.with_status("no_coverable_changes"):
        return "The change touches no executable lines."
    return "No changed file is measured by the coverage report."


def _ranges(f: FilePatchCoverage) -> str:
    shown = [f"{a}" if a == b else f"{a}-{b}" for a, b in f.uncovered_ranges[:8]]
    rest = len(f.uncovered_ranges) - len(shown)
    return ", ".join(shown) + (f", +{rest} more" if rest else "")


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _escape_property(value: str) -> str:
    # Workflow-command property values escape %, CR, LF, ':' and ','.
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )
