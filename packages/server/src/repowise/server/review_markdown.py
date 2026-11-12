"""A change review as Markdown, for a coding agent to read.

The Review page's content in its order: the verdict, the figures, what to look
at first, the tests to run with their command, who to ask, files usually
changed alongside, the health check when one ran, then the reach and the
changed files. Read from one ``ChangeBlastRadiusResponse``, so a field the
response gains becomes a section here and nowhere else.

Every list is capped and says how many it left out, so a change touching
thousands of files still reads in one fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from repowise.core.analysis.health.grading import BAND_LABEL, band_for, format_score
from repowise.core.ci.markdown import code, more_line, plural
from repowise.core.code_origin import owes_a_test, path_origin

if TYPE_CHECKING:
    from repowise.core.analysis.change_health.check import HealthCheck
    from repowise.server.schemas.blast_radius import ChangeBlastRadiusResponse

#: Rows per list. The page shows fewer; an agent gets enough to act on, and a
#: count of the rest.
LOOK_FIRST_LIMIT = 8
TESTS_LIMIT = 25
OWNERS_LIMIT = 10
PARTNERS_LIMIT = 10
DEPENDENTS_LIMIT = 40
CHANGED_LIMIT = 50
#: A test command past this is left out whole: a cut command would run the wrong tests.
COMMAND_LIMIT = 4000

# The verdict's thresholds, as on the page (review-status.ts in packages/ui).
_WIDE_REACH = 200
_SMALL_FILES = 3
_SMALL_REACH = 25


@dataclass(frozen=True)
class _Marks:
    """What the index knows about a file; ``None`` is unknown, never fine."""

    path: str
    health: float | None
    hotspot: bool | None
    untested: bool
    not_indexed: bool

    @property
    def weak(self) -> bool:
        return self.health is not None and band_for(self.health) in ("needs_work", "at_risk")

    @property
    def attention(self) -> int:
        return int(bool(self.hotspot)) + int(self.weak) + int(self.untested)

    def words(self) -> list[str]:
        out = []
        if self.health is not None:
            out.append(f"{BAND_LABEL[band_for(self.health)]} health {format_score(self.health)}")
        if self.hotspot:
            out.append("changed often")
        if self.untested:
            out.append("no test found")
        if self.not_indexed:
            out.append("new since the last index")
        return out


@dataclass(frozen=True)
class _Reached:
    marks: _Marks
    depth: int
    via: str | None


@dataclass(frozen=True)
class _Reach:
    changed: list[_Marks]
    #: Source files that import the change, nearest first; tests apart.
    files: list[_Reached]
    tests: list[str]

    @property
    def direct(self) -> list[_Reached]:
        return [f for f in self.files if f.depth == 1]


def _read(r: ChangeBlastRadiusResponse) -> _Reach:
    """The response read the way the page reads it (blast-map-model.ts)."""
    signals = {s.path: s for s in r.affected_signals or []}
    gaps = set(r.test_gaps)
    hotspot_fallback = {d.path: d.is_hotspot for d in r.direct_risks}

    def is_test(path: str) -> bool:
        s = signals.get(path)
        return bool(s and s.is_test) or path_origin(path) == "test"

    def marks(path: str) -> _Marks:
        s = signals.get(path)
        return _Marks(
            path=path,
            health=s.health_score if s else None,
            hotspot=s.is_hotspot if s else hotspot_fallback.get(path),
            # A package's __init__ is tested through what it gathers; a file new
            # since the index has no test evidence either way.
            untested=path in gaps
            and owes_a_test(path)
            and not path.endswith("__init__.py")
            and not (s and s.indexed is False),
            not_indexed=bool(s and s.indexed is False),
        )

    files: list[_Reached] = []
    tests: list[str] = []
    for t in r.transitive_affected:
        if is_test(t.path):
            tests.append(t.path)
            continue
        # "No test" is said of changed files only.
        files.append(_Reached(replace(marks(t.path), untested=False), t.depth, t.via))
    changed = sorted((marks(p) for p in r.changed_files), key=lambda m: (-m.attention, m.path))
    return _Reach(changed, files, tests)


def _partners(r: ChangeBlastRadiusResponse) -> list[tuple[str, str]]:
    """Each file that usually changes with a changed one and is left out, once."""
    seen: dict[str, str] = {}
    for w in r.cochange_warnings:
        seen.setdefault(w.missing_partner, w.changed)
    return list(seen.items())


def _verdict(r: ChangeBlastRadiusResponse, reach: _Reach, total: int) -> tuple[str, str, str]:
    """The page's one-line verdict: a word, the fact behind it, and what to do first."""
    tests = r.test_impact.recommendations_total
    untested = sum(m.untested for m in reach.changed)
    if untested:
        return (
            "Needs tests",
            f"{plural(untested, 'changed file')} with no test found.",
            "Add or point to a test for it before merging, then read the files under Look at first.",
        )
    risky = sum(1 for m in reach.changed if m.hotspot or m.weak)
    reason = None
    reach_n, partners = len(reach.files), len(_partners(r))
    if risky:
        one = risky == 1
        reason = (
            f"{plural(risky, 'changed file')} {'changes' if one else 'change'} often "
            f"or {'has' if one else 'have'} poor health."
        )
    elif r.structural_impact_band == "broad" or reach_n > _WIDE_REACH:
        reason = f"{plural(reach_n, 'source file')} {'depends' if reach_n == 1 else 'depend'} on it."
    elif partners:
        one = partners == 1
        reason = (
            f"{plural(partners, 'file')} that usually {'changes' if one else 'change'} "
            f"with these {'is' if one else 'are'} not in it."
        )
    if reason:
        return "Look first", reason, "Read the files under Look at first, then run the tests."
    small = total <= _SMALL_FILES and len(reach.files) <= _SMALL_REACH
    run = f"{plural(tests, 'test')} to run" if tests else "no test found to run"
    return (
        "Small change" if small else "Contained",
        f"{plural(total, 'file')} changed, {run}.",
        "Run the tests below and merge when they pass."
        if tests
        else "Nothing here asks for a closer look.",
    )


def _change_name(change_id: str) -> str:
    if change_id == "working-tree":
        return "Uncommitted changes"
    kind, _, ref = change_id.partition(":")
    if kind == "branch":
        return f"Branch {code(ref)}"
    if kind == "commit":
        return f"Commit {code(ref)}"
    return code(change_id)


def _capped(rows: list[str], limit: int, noun: str) -> list[str]:
    """The first *limit* rows, then how many were left out, after a blank line
    so it reads as its own paragraph rather than part of the last item."""
    out = rows[:limit]
    if len(rows) > limit:
        out += ["", more_line(len(rows) - limit, noun).capitalize()]
    return out


def _head(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    total = r.changed_files_total
    word, reason, next_step = _verdict(r, reach, total)
    analyzed = len(r.changed_files)
    changed = plural(total, "file")
    if total > analyzed:
        changed += f" (the first {analyzed} analyzed)"
    files = len(reach.files)
    scale = r.structural_impact_scale
    calibration = scale.calibration.status if scale.calibration else None
    impact = f"Structural impact: {r.structural_impact_band}, {r.structural_impact_score:.1f} of 10"
    if calibration == "uncalibrated":
        impact += " (how far the change reaches; uncalibrated, not a defect probability)"
    return [
        f"# Change review: {_change_name(r.change_id)}",
        "",
        f"**{word}.** {reason}",
        f"Next: {next_step}",
        "",
        f"- Changed: {changed}",
        f"- Source files that depend on it: {files}, {len(reach.direct)} directly",
        f"- Tests to run: {r.test_impact.recommendations_total}",
        f"- {impact}",
    ]


def _look_first(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    spots = [(m, True) for m in reach.changed] + [(f.marks, False) for f in reach.direct]
    spots = [s for s in spots if s[0].attention > 0]
    spots.sort(key=lambda s: (not s[1], -s[0].attention, s[0].path))
    if not spots:
        return []
    rows = [
        f"- {code(m.path)} ({'changed' if changed else 'imports a changed file'}): {', '.join(m.words())}"
        for m, changed in spots
    ]
    return ["## Look at first", "", *_capped(rows, LOOK_FIRST_LIMIT, "files")]


def _tests_limited(r: ChangeBlastRadiusResponse) -> bool:
    impact = r.test_impact
    return impact.coverage.status in ("unavailable", "degraded") or impact.analysis.partial or impact.analysis.stale


def _tests(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    impact = r.test_impact
    out = ["## Run these tests", ""]
    if impact.recommendations_total == 0:
        out.append(
            "No test found for these files; coverage data is limited here, so that does not mean none are needed."
            if _tests_limited(r)
            else "No test runs or imports these files."
        )
        return out
    measured = impact.recommendations_by_primary_basis.get("measured", 0)
    out.append(
        f"{plural(measured, 'test')} measured by coverage, the rest inferred from imports."
        if measured
        else "Inferred from imports; no coverage data names them."
    )
    if _tests_limited(r):
        out.append("Coverage data is limited here, so the list is a floor.")
    commands = r.test_commands or []
    if commands:
        text = "\n".join(commands)
        if len(text) <= COMMAND_LIMIT:
            fence = "```"
            out += ["", f"{fence}sh", text, fence]
        else:
            out += ["", f"The test command is {len(text)} characters, too long to include; run the tests listed below."]
    shown = impact.recommendations[:TESTS_LIMIT]
    rows = [
        f"- {code(t.test_file or t.test_id)}" + (" (measured)" if t.basis == "measured" else "")
        for t in shown
    ]
    # The response lists a capped number itself, so the count comes from its total.
    if impact.recommendations_total > len(shown):
        rows += ["", more_line(impact.recommendations_total - len(shown), "tests").capitalize()]
    return [*out, "", *rows]


def _owners(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    changed = len(r.changed_files)

    def name(o) -> str:
        return (o.name or "").strip() or o.email.split("@")[0] or o.email

    if r.changed_owners is not None:
        rows = [
            f"- {name(o)} <{o.email}>: owns {o.files} of the {plural(changed, 'changed file')}"
            for o in r.changed_owners
        ]
        owned = sum(o.files for o in r.changed_owners)
        note = (
            [
                "",
                f"{'That is' if len(rows) == 1 else 'Together,'} {owned} of the "
                f"{plural(changed, 'changed file')}; the rest have no owner on record.",
            ]
            if rows and owned < changed
            else []
        )
    else:
        rows = [f"- {name(o)} <{o.email}>: owns code that depends on this" for o in r.recommended_reviewers]
        note = []
    if not rows:
        return ["## Who to ask", "", "No one owns the changed files yet: they are new or have no history."]
    return ["## Who to ask", "", *_capped(rows, OWNERS_LIMIT, "people"), *note]


def _missing_partners(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    rows = [
        f"- {code(path)}: usually changes with {code(of)}, not in this change"
        for path, of in _partners(r)
    ]
    if not rows:
        return []
    return ["## Usually change with these", "", *_capped(rows, PARTNERS_LIMIT, "files")]


def _dependents(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    out = ["## Files that depend on this", ""]
    if not reach.files:
        out.append("No source file imports the changed files.")
    else:
        rows = []
        for f in reach.files:
            via = f" through {code(f.via)}" if f.via else ""
            words = f.marks.words()
            rows.append(
                f"- {code(f.marks.path)}: {plural(f.depth, 'import')} away{via}"
                + (f"; {', '.join(words)}" if words else "")
            )
        out += _capped(rows, DEPENDENTS_LIMIT, "files")
    if reach.tests:
        tests = len(reach.tests)
        out += [
            "",
            f"Not counted here: {plural(tests, 'test file')} that "
            f"{'imports' if tests == 1 else 'import'} the change; they are tests to run.",
        ]
    return out


def _changed(r: ChangeBlastRadiusResponse, reach: _Reach) -> list[str]:
    rows = []
    for m in reach.changed:
        words = m.words()
        rows.append(f"- {code(m.path)}" + (f": {', '.join(words)}" if words else ""))
    out = ["## Changed files", "", *_capped(rows, CHANGED_LIMIT, "files")]
    skipped = r.changed_files_total - len(r.changed_files)
    if skipped > 0:
        out += ["", f"{plural(skipped, 'more changed file')} not analyzed."]
    return out


#: The page's sections in its order. A field the response gains is one more entry.
_SECTIONS: tuple[Callable[[ChangeBlastRadiusResponse, _Reach], list[str]], ...] = (
    _look_first,
    _tests,
    _owners,
    _missing_partners,
)
_EVIDENCE: tuple[Callable[[ChangeBlastRadiusResponse, _Reach], list[str]], ...] = (
    _dependents,
    _changed,
)


def _health(check: HealthCheck | str) -> list[str]:
    """The health check's own Markdown (``repowise health check --format markdown``)."""
    if isinstance(check, str):
        return ["## Health check", "", f"The health check could not run: {check}"]
    from repowise.core.analysis.health_gate import render_markdown, reproduce_command, scope_line

    body = render_markdown(
        check.result,
        label=check.label,
        scope=scope_line(check.delta),
        reproduce=None if check.label == "working tree" else reproduce_command(check.label, check.result),
    )
    return ["## Health check", "", body.rstrip("\n")]


def render_change_markdown(
    response: ChangeBlastRadiusResponse, health: HealthCheck | str | None = None
) -> str:
    """The Review page for one change as Markdown.

    *health* is the change's health check when the reader ran one, or why it
    could not run; ``None`` leaves the section out.
    """
    reach = _read(response)
    blocks = [_head(response, reach)]
    blocks += [section(response, reach) for section in _SECTIONS]
    if health is not None:
        blocks.append(_health(health))
    blocks += [section(response, reach) for section in _EVIDENCE]
    return "\n\n".join("\n".join(b) for b in blocks if b) + "\n"
