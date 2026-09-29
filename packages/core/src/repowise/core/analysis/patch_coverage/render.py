"""Human renderings of :class:`PatchCoverage`: markdown, plain text, CI annotations.

Markdown is what a CI step summary or a pull-request comment shows, so it is
written to be read once and acted on: the verdict in words on the first line
with its denominator, the diff, report and file counts on the second, and
detail only for what needs attention. A passing change with no gaps is two
lines. A file the report never named reads "not in report", never 0%. With
an index, each gap names the test to extend (``hints``).

Workflow-command and markdown mechanics (escaping, caps, collapsed lists)
come from :mod:`repowise.core.ci`, shared with every other CI gate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ...ci import github
from ...ci.markdown import ROW_LIMIT, cell, details, more_line, plural
from .compute import FilePatchCoverage, PatchCoverage, PathGateResult
from .delta import IndirectCause, IndirectChange, ProjectDelta
from .hints import RANGE_LIMIT, TestHint, first_hint, hint_phrase
from .risk import FileRisk

#: How a file that could not be measured reads, wherever it is listed.
STATUS_TEXT = {
    "not_in_report": "not in report",
    "no_line_data": "report has no line data",
    "no_coverable_changes": "no executable lines changed",
}


def headline(pc: PatchCoverage, *, markdown: bool = True) -> str:
    """One-sentence verdict. Bold on the figure only in markdown.

    A failing path-scoped gate that is not informational leads, with its
    counts, since it is why the change fails; the whole-change verdict follows.
    """
    text = _flat_headline(pc, markdown=markdown)
    failing = pc.failing_path_gates
    if failing:
        text = f"Fails: {'; '.join(path_gate_verdict(g) for g in failing)}. {text}"
    return text


def _flat_headline(pc: PatchCoverage, *, markdown: bool) -> str:
    """The whole-change verdict alone, path-scoped gates aside."""
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
    return text + _gate_suffix(pc.flat_gate, pc.threshold, pc.min_coverable_lines, "gate")


def _gate_suffix(gate: str, threshold: float | None, min_lines: int | None, noun: str) -> str:
    """`` · meets the 80.0% gate`` and its kin, or ``""`` for an unset or unjudged gate."""
    if gate == "fail":
        return f" · below the {fmt_pct(threshold)} {noun}"
    if gate == "pass":
        return f" · meets the {fmt_pct(threshold)} {noun}"
    if gate == "too_small":
        lines = "line" if min_lines == 1 else "lines"
        return (
            f" · below the {fmt_pct(threshold)} {noun}, not applied: fewer than {min_lines} "
            f"changed executable {lines} (min_coverable_lines)"
        )
    return ""


def path_gate_verdict(g: PathGateResult) -> str:
    """``path-scoped gate api 60.0% (6 of 10 changed executable lines), below its 85.0% gate``.

    One wording for the headline, the ``::error::`` of a failing gate and the
    ``::notice::`` of an informational one below its threshold.
    """
    return (
        f"path-scoped gate {g.name} {fmt_pct(g.patch_coverage_pct)} ({g.covered_line_count} "
        f"of {g.coverable_line_count} changed executable lines), below its "
        f"{fmt_pct(g.threshold)} gate"
    )


# A path-scoped gate's verdict in words.
_PATH_GATE_TEXT = {
    "pass": "passes",
    "fail": "fails",
    "no_data": "no measured changed lines",
    "not_set": "no threshold",
    "too_small": "too few changed lines to judge",
}


def path_gate_row(g: PathGateResult) -> tuple[str, str, str, str]:
    """``(name, verdict, covered, threshold)`` for one path-scoped gate, in plain text."""
    covered = "n/a"
    if g.coverable_line_count:
        covered = (
            f"{g.covered_line_count} of {g.coverable_line_count} "
            f"({fmt_pct(g.patch_coverage_pct)})"
        )
    threshold = "none" if g.threshold is None else fmt_pct(g.threshold)
    return g.name, _path_gate_words(g), covered, threshold


def _path_gate_words(g: PathGateResult) -> str:
    if g.informational:
        words = "below threshold" if g.gate == "fail" else _no_data_words(g)
        return f"{words} (informational)"
    return _no_data_words(g)


def _no_data_words(g: PathGateResult) -> str:
    """The verdict in words, saying why a ``no_data`` gate has none."""
    if g.gate != "no_data":
        return _PATH_GATE_TEXT[g.gate]
    if g.coverable_line_count:
        # Counted but not judged: stale coverage or invalid config.
        return "not judged"
    n = g.unmeasured_file_count
    unmeasured = f" ({plural(n, 'changed file')} not measured)" if n else ""
    return _PATH_GATE_TEXT["no_data"] + unmeasured


def risky_line(pc: PatchCoverage, *, markdown: bool = True) -> str:
    """The verdict over risky files, or ``""`` when there is nothing to say."""
    if all(f.risk is None for f in pc.files):
        return ""
    bold = "**" if markdown else ""
    pct = pc.risky_pct
    if pct is None:
        if pc.risky_threshold is None:
            return ""
        return (
            "No changed executable line is in a file history marks as risky. "
            f"The {fmt_pct(pc.risky_threshold)} risky-file gate was not applied."
        )
    text = (
        f"{bold}Risky files {fmt_pct(pct)}{bold} ({pc.risky_covered_line_count} of "
        f"{pc.risky_coverable_line_count} changed executable lines covered in "
        f"{plural(len(pc.risky_files), 'risky file')})"
    )
    return text + _gate_suffix(
        pc.risky_gate, pc.risky_threshold, pc.min_coverable_lines, "risky-file gate"
    )


def risk_order_key(f: FilePatchCoverage) -> tuple:
    """Risky first, then fix pressure, then dependents, then uncovered lines."""
    risk = f.risk or FileRisk()
    return (
        not risk.risky,
        -(risk.fix_pressure or 0.0),
        -(risk.dependents or 0),
        -f.uncovered_line_count,
        f.file_path,
    )


def risk_words(risk: FileRisk | None) -> str:
    """``"hotspot, bug-fix weight 3.2, 14 dependents"``, ``"none known"`` or ``"unknown"``."""
    if risk is None or risk.basis == "unavailable":
        return "unknown"
    parts = list(risk.reasons)
    if risk.fix_pressure:
        parts.append(f"bug-fix weight {risk.fix_pressure:.1f}")
    if risk.dependents:
        parts.append(plural(risk.dependents, "dependent"))
    return ", ".join(parts) or "none known"


def risk_basis_line(pc: PatchCoverage) -> str:
    """Where the risk came from, when that is not the index for every row; else ``""``."""
    bases = [f.risk.basis for f in pc.files if f.risk is not None]
    unknown = bases.count("unavailable")
    parts = [
        _git_only_text(bases.count("git"), len(bases)),
        "git fix history could not be read" if "index" in bases else "",
        f"risk could not be read for {plural(unknown, 'file')}" if unknown else "",
    ]
    return " · ".join(part for part in parts if part)


def _git_only_text(git_only: int, total: int) -> str:
    """The basis clause for rows the index has no data for, or ``""``."""
    if not git_only:
        return ""
    if git_only == total:
        return (
            "Risk is from git bug-fix history alone; an index adds hotspot, bug-magnet "
            "and dependent counts"
        )
    return (
        f"Risk for {git_only} of {plural(total, 'file')} is from git bug-fix "
        "history alone: the index has no row for them yet"
    )


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
    if scope.ignored_file_count:
        files += f", {scope.ignored_file_count} ignored by coverage.ignore"
    parts.append(files)
    if scope.freshness == "stale":
        at = f" at {scope.measured_commit[:7]}" if scope.measured_commit else ""
        parts.append(f"coverage was measured{at}, not at this change's head")
    if scope.mapping_partial:
        parts.append("most report paths did not match this repository, so this covers a fragment")
    elif scope.unmatched_report_path_count:
        parts.append(
            f"{scope.unmatched_report_path_count} of {scope.report_path_count} report paths "
            "did not match a file in this repository"
        )
    return " · ".join(parts)


def attention_rows(pc: PatchCoverage) -> list[FilePatchCoverage]:
    """Files a reader should look at: uncovered changes first, then unmeasurable ones.

    Within each group the riskiest file leads (:func:`risk_order_key`).
    """
    gaps = sorted(
        (f for f in pc.with_status("measured") if f.uncovered_line_count), key=risk_order_key
    )
    unmeasured = sorted(
        pc.with_status("not_in_report") + pc.with_status("no_line_data"),
        key=lambda f: (f.status != "not_in_report", *risk_order_key(f)),
    )
    return gaps + unmeasured


def render_markdown(pc: PatchCoverage) -> str:
    """Markdown for a CI step summary or PR comment."""
    out = [headline(pc)]
    if project := project_line(pc):
        out += ["", project]
    if risky := risky_line(pc):
        out += ["", risky]
    out += ["", scope_line(pc)]
    if basis := risk_basis_line(pc):
        out += ["", basis + "."]
    out += _path_gate_table(pc)
    out += _gap_table(pc)
    out += _details(pc.with_status("not_in_report"), "not in the coverage report")
    out += _details(pc.with_status("no_line_data"), "in a report without line data")
    out += _outside_section(pc)
    return "\n".join(out) + "\n"


def _path_gate_table(pc: PatchCoverage) -> list[str]:
    if not pc.path_gates:
        return []
    out = ["", "| Path-scoped gate | Verdict | Covered changed lines | Threshold |", "|---|---|---|---|"]
    # Gates failing the change first, so the cap never hides one.
    for g in sorted(pc.path_gates, key=lambda g: not g.fails_change)[:ROW_LIMIT]:
        name, verdict, covered, threshold = path_gate_row(g)
        out.append(f"| `{cell(name)}` | {verdict} | {covered} | {threshold} |")
    if len(pc.path_gates) > ROW_LIMIT:
        out += ["", more_line(len(pc.path_gates) - ROW_LIMIT, "path-scoped gates")]
    return out


def _gap_table(pc: PatchCoverage) -> list[str]:
    """Files with uncovered changed lines, riskiest first; a Risk column once risk is read."""
    gaps = [f for f in attention_rows(pc) if f.status == "measured"]
    if not gaps:
        return []
    out = ["", *_gap_rows(pc, gaps[:ROW_LIMIT])]
    if len(gaps) > ROW_LIMIT:
        out += ["", more_line(len(gaps) - ROW_LIMIT, "files with uncovered changed lines")]
    return out


def _gap_rows(pc: PatchCoverage, rows: list[FilePatchCoverage]) -> list[str]:
    """The uncovered-lines table; Risk and Extend columns only when something fills them."""
    with_risk = any(f.risk is not None for f in pc.files)
    with_hint = any(f.hints for f in rows)
    head = ["File", *(["Risk"] if with_risk else []), "Uncovered changed lines", "Covered"]
    head += ["Extend"] if with_hint else []
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for f in rows:
        cells = [f"`{cell(f.file_path)}`"]
        cells += [cell(risk_words(f.risk))] if with_risk else []
        cells += [
            format_ranges(f.uncovered_ranges, RANGE_LIMIT),
            f"{f.covered_line_count} of {f.coverable_line_count}",
        ]
        if with_hint:
            hint = first_hint(f)
            # The phrase's backticks are code spans; everything between is escaped.
            cells.append("`".join(cell(p) for p in hint_phrase(hint).split("`")) if hint else "")
        lines.append("| " + " | ".join(cells) + " |")
    return lines


#: Warning slots kept for coverage lost outside the change; unused ones go to ranges.
OUTSIDE_RESERVE = 3


def github_annotations(pc: PatchCoverage) -> list[str]:
    """GitHub Actions workflow commands for a change's patch coverage.

    A failed gate is an error, and so is each failing path-scoped gate that is
    not informational and a failing risky-file gate; one exempted by the
    small-change tolerance, and an informational gate below its threshold, is
    a notice, so neither is silent. Uncovered ranges are marked riskiest file
    first, then largest range, sharing what GitHub displays with a warning per
    file that lost coverage outside the change (:data:`OUTSIDE_RESERVE`),
    with one notice counting the rest.
    """
    ranges = sorted(
        ((f, a, b) for f in pc.with_status("measured") for a, b in f.uncovered_ranges),
        key=lambda r: (*risk_order_key(r[0])[:3], r[1] - r[2], r[0].file_path, r[1]),
    )
    warnings = [_range_annotation(f, a, b) for f, a, b in ranges]
    verdict = [
        *_gate_annotations(pc.flat_gate, _flat_headline(pc, markdown=False)),
        *_path_gate_annotations(pc),
        *_gate_annotations(pc.risky_gate, risky_line(pc, markdown=False)),
    ]
    if pc.project is not None:
        verdict += _gate_annotations(pc.project.gate, project_line(pc, markdown=False))
    outside = [_outside_annotation(c) for c in _lost_outside(pc)]
    return verdict + github.cap_shared(
        warnings, outside, reserve=OUTSIDE_RESERVE, noun="warnings"
    )


def _gate_annotations(gate: str, text: str) -> list[str]:
    """An error for a failed gate, a notice for one the tolerance exempted."""
    if gate == "fail":
        return [github.error(text)]
    if gate == "too_small":
        return [github.notice(text)]
    return []


def _path_gate_annotations(pc: PatchCoverage) -> list[str]:
    out = []
    for g in pc.path_gates:
        if g.fails_change:
            out.append(github.error(f"Fails: {path_gate_verdict(g)}."))
        elif g.informational and g.gate == "fail":
            out.append(github.notice(f"Informational: {path_gate_verdict(g)}."))
    return out


def _range_annotation(f: FilePatchCoverage, a: int, b: int) -> str:
    risky = f.risk is not None and f.risk.risky
    message = f"Changed {_span(a, b)} not covered by tests"
    if risky:
        message += f" ({risk_words(f.risk)})"
    if hint := _hint_for(f, a, b):
        phrase = hint_phrase(hint)
        message += f". {phrase[:1].upper()}{phrase[1:]}"
    return github.annotation(
        "warning",
        message,
        file=f.file_path,
        line=a,
        end_line=b,
        title="Uncovered change in a risky file" if risky else "Uncovered change",
    )


def _hint_for(f: FilePatchCoverage, a: int, b: int) -> TestHint | None:
    """The hint for range ``(a, b)``, when the index gave one."""
    return next((h for h in f.hints or () if h.range == (a, b)), None)


def project_line(pc: PatchCoverage, *, markdown: bool = True) -> str:
    """Project coverage at the head against the base, or ``""`` when not computed.

    ``Project coverage 81.2% · down 0.30 points from 81.5% at a1b2c3d · within
    the 0.5-point max-drop gate``: one shape on every surface (the UI's
    ``projectText`` mirrors it). Incomparable measurements say why instead.
    """
    p = pc.project
    if p is None:
        return ""
    bold = "**" if markdown else ""
    if reason := _not_compared(p):
        return f"{bold}Project coverage not compared:{bold} {reason}."
    assert p.base is not None and p.head is not None  # _not_compared vouches
    head, base = p.head.pct, p.base.pct
    parts = [
        f"{bold}Project coverage {fmt_pct(head)}{bold}",
        _delta_phrase(head, base, p.base_commit),
        _gate_phrase(p.gate, p.max_drop),
    ]
    return " · ".join(part for part in parts if part)


def _not_compared(p: ProjectDelta) -> str:
    """Why the two sides were not compared, or ``""`` when they were."""
    if p.incomparable:
        return "; ".join(p.incomparable)
    if p.head is None or p.head.pct is None:
        return "the head measured no coverable line"
    if p.base is None or p.base.pct is None:
        return "the base measured no coverable line"
    return ""


def _delta_phrase(head: float, base: float, base_commit: str | None) -> str:
    """``down 0.30 points from 81.5% at a1b2c3d``; ``unchanged`` at zero."""
    points = round(head - base, 2)
    change = "unchanged" if points == 0 else (
        f"{'up' if points > 0 else 'down'} {abs(points):.2f} points"
    )
    at = f" at {base_commit[:7]}" if base_commit else ""
    return f"{change} from {fmt_pct(base)}{at}"


def _gate_phrase(gate: str, max_drop: float | None) -> str:
    if gate == "fail":
        return f"falls more than the {max_drop:g}-point max-drop gate allows"
    if gate == "pass":
        return f"within the {max_drop:g}-point max-drop gate"
    return ""


def outside_change_rows(pc: PatchCoverage) -> tuple[IndirectChange, ...]:
    """The files whose coverage changed outside the change, when a base report named them."""
    rows = pc.project.outside_change if pc.project is not None else None
    return rows or ()


def _lost_outside(pc: PatchCoverage) -> list[IndirectChange]:
    """Rows that lost coverage: newly uncovered lines (most first), or no longer measured."""
    return [
        c
        for c in outside_change_rows(pc)
        if c.newly_uncovered_ranges or c.status == "no_longer_measured"
    ]


def cause_words(causes: tuple[IndirectCause, ...] | None) -> str:
    """``"deleted test tests/test_a.py (by name)"``; ``"unknown"`` when not assessed."""
    if causes is None:
        return "unknown"
    if not causes:
        return "nothing in the change names it"
    shown = [
        f"{_CAUSE_KIND[c.kind]} {c.path} ({_CAUSE_BASIS[c.basis]})" for c in causes[:_CAUSE_LIMIT]
    ]
    rest = len(causes) - len(shown)
    return ", ".join(shown) + (f", +{rest} more" if rest else "")


_CAUSE_KIND = {
    "test_deleted": "deleted test",
    "test_modified": "changed test",
    "dependent_changed": "changed dependent",
}
_CAUSE_BASIS = {"per_test": "measured", "graph": "inferred", "name": "by name"}
_CAUSE_LIMIT = 2


def indirect_row(c: IndirectChange) -> tuple[str, str, str, str, str]:
    """``(file, before, after, newly uncovered lines, cause)`` in plain text."""
    if c.status == "no_longer_measured":
        after, lost = "not measured", "the head report does not name it"
    else:
        after = fmt_pct(c.head_pct)
        lost = format_ranges(c.newly_uncovered_ranges, RANGE_LIMIT) or "none"
        if c.newly_covered_line_count:
            lost += f" ({c.newly_covered_line_count} newly covered)"
    cause = cause_words(c.causes) if c.newly_uncovered_ranges else ""
    return c.file_path, fmt_pct(c.base_pct), after, lost, cause


def _outside_section(pc: PatchCoverage) -> list[str]:
    p = pc.project
    if p is None or (not p.outside_change and not p.outside_change_note):
        return []
    out = ["", "### Coverage outside the change"]
    if p.outside_change_note:
        out += ["", f"Files left out: {p.outside_change_note}."]
    rows = outside_change_rows(pc)
    if not rows:
        return out
    out += [
        "",
        "| File | Before | After | Newly uncovered lines | Cause |",
        "|---|---|---|---|---|",
    ]
    for c in rows[:ROW_LIMIT]:
        path, *rest = indirect_row(c)
        out.append(f"| `{cell(path)}` | " + " | ".join(cell(v) for v in rest) + " |")
    if len(rows) > ROW_LIMIT:
        out += ["", more_line(len(rows) - ROW_LIMIT, "files")]
    return out


def _outside_annotation(c: IndirectChange) -> str:
    """A file-level warning at the first line that lost coverage, or on the file."""
    if c.status == "no_longer_measured":
        return github.annotation(
            "warning",
            f"Measured at the base ({fmt_pct(c.base_pct)}), not in the head's coverage report",
            file=c.file_path,
            title="Coverage lost outside the change",
        )
    n = c.newly_uncovered_line_count
    message = (
        f"{plural(n, 'line')} outside the change lost coverage "
        f"({format_ranges(c.newly_uncovered_ranges, RANGE_LIMIT)})"
    )
    if c.causes:
        message += f"; cause: {cause_words(c.causes)}"
    return github.annotation(
        "warning",
        message,
        file=c.file_path,
        line=c.newly_uncovered_ranges[0][0],
        title="Coverage lost outside the change",
    )


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
        if pc.scope.ignored_file_count:
            return "Every changed file is ignored by coverage.ignore."
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
