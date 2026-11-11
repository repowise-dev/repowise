"""The CI gate over code health: what a change itself makes worse.

Reads a :class:`~.change_health.models.ChangeHealthDelta`: both sides of the
change re-analysed from git blobs, changed files only, no index. A finding
decides the verdict only when it is code shape (:func:`~.health.scoring.is_code_shape`):
git history and coverage never do, because a squash merge rewrites the history
a branch was scored on, and a file score never does, because a score gate
rewards renaming and splitting over fixing.

The verdict is assembled from one list of :class:`GateFinding`, each carrying
its own level, so another source of findings adds to the list without changing
how it is judged or rendered.
"""

from __future__ import annotations

import hashlib
import shlex
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from repowise.core.ci import github, gitlab, sarif
from repowise.core.ci.markdown import ROW_LIMIT, cell, code, details, more_line, plural
from repowise.core.ci.summary import GateSummary, SummaryFinding

from ..test_paths import is_test_related_path
from .change_health.identity import severity_rank
from .change_health.models import INCIDENTAL_BASES, ChangeFinding, ChangeHealthDelta
from .diff_risk import DiffRiskFinding
from .health.biomarker_labels import biomarker_label
from .health.scoring import is_code_shape
from .health.suggestions import hint_for

#: ``--fail-on`` values, least to most severe: a level fails it and every level above.
FAIL_ON = ("worsened", "introduced")
_KIND_RANK = {kind: i for i, kind in enumerate(FAIL_ON)}
#: ``--fail-at`` values: the lowest severity that can fail; below it a finding warns.
FAIL_AT = ("medium", "high", "critical")

ERROR, WARNING, NOTE = "error", "warning", "note"
PASS, WARN, FAIL = "pass", "warn", "fail"

#: What a health finding has to be for the verdict to count it.
FINDINGS_BASIS = (
    "Structure only: findings from the code's shape at both revisions of the changed "
    "files. Git history, coverage and file scores never decide this gate."
)

SARIF_TOOL_NAME = "repowise-health"
SARIF_FINGERPRINT_KEY = "repowiseHealth/v1"

#: Skip reasons meaning a file that should have been read was not.
_UNREAD = frozenset({"parse_failed", "unreadable", "too_large"})
_SOURCE = "health"
_NOT_ANALYSED = "not_analysed"
#: Statuses whose findings can be judged; ``unavailable`` is decided per case.
_JUDGED = frozenset({"available", "partial"})


class NotEvaluatedError(ValueError):
    """The comparison could not judge the change; *code* names why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GateFinding:
    """One finding the gate weighs, already given its level."""

    #: Which check produced it (``health``).
    source: str
    #: The rule or marker, as SARIF and Code Quality name it.
    rule: str
    level: str
    path: str
    line: int | None
    message: str
    #: Line-free, so the same finding keeps it across commits.
    fingerprint: str
    symbol: str | None = None
    #: Source-specific fields for JSON (change kind, severity, attribution).
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "rule": self.rule,
            "level": self.level,
            "file_path": self.path,
            "line_number": self.line,
            "symbol": self.symbol,
            "message": self.message,
            "fingerprint": self.fingerprint,
            **self.extra,
        }


@dataclass(frozen=True)
class GateResult:
    """The verdict and the findings it was reached on."""

    verdict: str
    fail_on: str
    findings: tuple[GateFinding, ...] = ()
    fail_at: str = "high"
    #: What limits the verdict (files not compared, nothing to compare).
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.verdict != FAIL

    def at(self, level: str) -> list[GateFinding]:
        return [f for f in self.findings if f.level == level]

    def to_dict(self) -> dict[str, Any]:
        """The shape every gate's JSON shares: ``passed`` and the ``failing`` rows."""
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "fail_on": self.fail_on,
            "fail_at": self.fail_at,
            "failing_count": len(self.at(ERROR)),
            "warning_count": len(self.at(WARNING)),
            "note_count": len(self.at(NOTE)),
            "failing": [f.to_dict() for f in self.at(ERROR)],
            "notes": list(self.notes),
        }


def assemble(
    findings: Iterable[GateFinding],
    *,
    fail_on: str,
    fail_at: str = "high",
    notes: Iterable[str] = (),
) -> GateResult:
    """``fail`` on any error, ``warn`` on any warning, else ``pass``; errors first."""
    ordered = tuple(sorted(findings, key=_order))
    levels = {f.level for f in ordered}
    verdict = FAIL if ERROR in levels else WARN if WARNING in levels else PASS
    return GateResult(verdict, fail_on, ordered, fail_at, tuple(notes))


def _order(f: GateFinding) -> tuple:
    return ((ERROR, WARNING, NOTE).index(f.level), f.path, f.line or 0, f.rule)


# ---------------------------------------------------------------------------
# From a health comparison
# ---------------------------------------------------------------------------


def evaluate(
    delta: ChangeHealthDelta,
    *,
    fail_on: str = "introduced",
    fail_at: str = "high",
    extra: Iterable[GateFinding] = (),
) -> GateResult:
    """The verdict on *delta*, however it was built (a checkout or supplied bytes).

    *fail_on* names the change kinds that fail (:data:`FAIL_ON`), *fail_at*
    the lowest severity that does (:data:`FAIL_AT`); below either a finding
    warns. *extra* are findings from another source, judged alongside. Raises
    ``ValueError`` on an unknown option and :class:`NotEvaluatedError` when the
    comparison judged nothing.
    """
    if fail_on not in FAIL_ON or fail_at not in FAIL_AT:
        raise ValueError(f"fail_on must be one of {FAIL_ON}, fail_at one of {FAIL_AT}")
    findings, notes = health_findings(delta, fail_on=fail_on, fail_at=fail_at)
    return assemble([*findings, *extra], fail_on=fail_on, fail_at=fail_at, notes=notes)


def health_findings(
    delta: ChangeHealthDelta, *, fail_on: str, fail_at: str = "high"
) -> tuple[list[GateFinding], list[str]]:
    """``(findings, notes)`` for *delta*; raises :class:`NotEvaluatedError` when it judged nothing.

    A change with nothing health reads (docs, config, an unsupported language,
    generated or vendored files) passes with a note; a comparison that could
    not run, or one where no code file could be read, cannot be evaluated.
    """
    unread = _unread_files(delta.skipped)
    if delta.status not in _JUDGED:
        if delta.status != "unavailable" or delta.comparison_basis == "not_compared":
            raise NotEvaluatedError(delta.status, delta.explanation)
        if unread:
            raise NotEvaluatedError("not_analysed", delta.explanation)
        return [], [delta.explanation]
    findings = [_from_change(f, fail_on, fail_at) for f in delta.findings]
    findings += unread
    notes = []
    if code_skipped := delta.code_skipped:
        notes.append(
            f"Compared {delta.scope.analyzed} of {plural(delta.scope.changed, 'changed file')}; "
            f"not analysed: {_listing(code_skipped)}."
        )
    return findings, notes


def _from_change(f: ChangeFinding, fail_on: str, fail_at: str) -> GateFinding:
    extra: dict[str, Any] = {
        "change_kind": f.change_kind,
        "severity": f.severity,
        "dimension": f.dimension,
        "attribution_basis": f.attribution_basis,
    }
    if f.severity_before:
        extra["severity_before"] = f.severity_before
    if f.moved_from:
        extra["moved_from"] = f.moved_from
    return GateFinding(
        source=_SOURCE,
        rule=f.biomarker_type,
        level=_level(f, fail_on, fail_at),
        path=f.path,
        line=f.line_start,
        message=_message(f),
        fingerprint=f.finding_id or _fingerprint(f.path, f.biomarker_type, f.symbol or ""),
        symbol=f.symbol,
        extra=extra,
    )


def _level(f: ChangeFinding, fail_on: str, fail_at: str) -> str:
    """Error when the change wrote a code-shape finding at or above *fail_on* and *fail_at*.

    A finding the change only carried (moved) or sits away from its lines
    (incidental) is a note, as is anything that is not code shape save measured
    CRAP, which warns. Below
    *fail_at* a finding warns at most (by default medium: CCN 9 to 14, nesting
    depth 4, just past a detector's threshold), and so does one in a test
    file: long test functions are the change's own scaffolding more often than
    a defect.
    """
    authored = f.attribution_basis != "moved" and f.attribution_basis not in INCIDENTAL_BASES
    if not authored or f.change_kind not in _KIND_RANK:
        return NOTE
    if not is_code_shape(f.biomarker_type):
        # CRAP on measured coverage, the one coverage reading a comparison keeps
        # (estimates never reach it): worth a warning, never a failure.
        return WARNING if f.biomarker_type == "untested_complexity" else NOTE
    if (
        _KIND_RANK[f.change_kind] >= _KIND_RANK[fail_on]
        and severity_rank(f.severity) >= severity_rank(fail_at)
        and not is_test_related_path(f.path)
    ):
        return ERROR
    return WARNING


def _message(f: ChangeFinding) -> str:
    what = f.reason or biomarker_label(f.biomarker_type)
    if f.change_kind == "worsened" and f.severity_before:
        return f"{what} (worsened from {f.severity_before} to {f.severity})"
    return f"{what} ({f.change_kind}, {f.severity})"


def diff_risk_findings(findings: Iterable[DiffRiskFinding]) -> list[GateFinding]:
    """Diff-risk findings (added suppressions, weakened tests, test-only branches)
    as gate findings, for :func:`evaluate`'s *extra*. ``off`` rules never get here."""
    return [
        GateFinding(
            source="diff-risk",
            rule=f.rule,
            level=ERROR if f.severity == "error" else WARNING,
            path=f.path,
            line=f.line,
            message=f.message,
            fingerprint=f.finding_id,
            extra={"kind": f.kind, "evidence": f.evidence},
        )
        for f in findings
    ]


def _unread_files(skipped: Mapping[str, str]) -> list[GateFinding]:
    """A warning per changed code file left unread on either side (unparsable, too large)."""
    return [
        GateFinding(
            source=_SOURCE,
            rule=_NOT_ANALYSED,
            level=WARNING,
            path=path,
            line=None,
            message=f"Not analysed ({reason.replace('_', ' ')}), so a finding here would go unseen.",
            fingerprint=_fingerprint(path, _NOT_ANALYSED, reason),
        )
        for path, reason in sorted(skipped.items())
        if reason.removeprefix("base_") in _UNREAD
    ]


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(("repowise.health.gate.v1", *parts)).encode()).hexdigest()[:32]


def _listing(paths: Mapping[str, str], limit: int = 5) -> str:
    shown = [f"{p} ({r.replace('_', ' ')})" for p, r in sorted(paths.items())[:limit]]
    rest = len(paths) - limit
    return ", ".join(shown) + (f" and {rest} more" if rest > 0 else "")


def scope_line(delta: ChangeHealthDelta) -> str:
    """``"2 of 3 changed files compared on both sides"``, for every rendering."""
    s = delta.scope
    return f"{s.analyzed} of {plural(s.changed, 'changed file')} compared on both sides"


# ---------------------------------------------------------------------------
# Renderings
# ---------------------------------------------------------------------------


def location(f: GateFinding) -> str:
    """``path:line``, or the path alone for a file-level finding."""
    return f"{f.path}:{f.line}" if f.line else f.path


_FAILS_ON_TEXT = {"introduced": "introduced", "worsened": "introduced and worsened"}

_HEADLINE = {
    PASS: "**Health: nothing this change wrote fails the gate** ({label}).",
    WARN: "**Health: passed with {warnings}** ({label}).",
    FAIL: "**Health: {errors} {verb} the gate** ({label}).",
}


def reproduce_command(label: str, result: GateResult, diff_risk: Iterable[str] = ()) -> str:
    """The ``repowise health check`` command that reaches *result* again locally."""
    args = ["repowise", "health", "check", label, "--fail-on", result.fail_on]
    args += ["--fail-at", result.fail_at]
    for value in diff_risk:
        args += ["--diff-risk", value]
    return shlex.join(args)


def render_markdown(
    result: GateResult, *, label: str, scope: str, reproduce: str | None = None
) -> str:
    """Step-summary / PR-comment markdown: verdict first, then a capped table.

    A failing verdict with *reproduce* (:func:`reproduce_command`) says how to
    run the same check locally.
    """
    errors, warnings = result.at(ERROR), result.at(WARNING)
    lines = [
        _HEADLINE[result.verdict].format(
            label=cell(label),
            errors=plural(len(errors), "finding"),
            verb="fails" if len(errors) == 1 else "fail",
            warnings=plural(len(warnings), "warning"),
        ),
        "",
        f"{scope}; fails on {_FAILS_ON_TEXT[result.fail_on]} findings at "
        f"{result.fail_at} severity or above.",
    ]
    for note in result.notes:
        lines += ["", cell(note)]
    shown = [*errors, *warnings]
    if shown:
        lines += ["", "| Where | Level | Finding |", "| --- | --- | --- |"]
        lines += [
            f"| {cell(location(f))} | {f.level} | {cell(f.message)} |" for f in shown[:ROW_LIMIT]
        ]
        if len(shown) > ROW_LIMIT:
            lines += ["", more_line(len(shown) - ROW_LIMIT, "findings")]
    context = [f"{cell(location(f))}: {cell(f.message)}" for f in result.at(NOTE)]
    if context:
        lines += ["", *details("Context (moved, incidental or not code shape; never fails)", context)]
    if reproduce and not result.passed:
        lines += ["", f"Reproduce locally: {code(_INSTALL)}, then {code(reproduce)}"]
    lines += ["", f"_{FINDINGS_BASIS}_"]
    return "\n".join(lines) + "\n"


def github_annotations(result: GateResult, *, reproduce: str | None = None) -> list[str]:
    """``::error`` for failing findings and ``::warning`` for warnings, capped.

    A failing health finding carries its fix hint; a failing verdict with
    *reproduce* ends in one ``::notice`` naming the local command.
    """
    out = [
        github.annotation(
            f.level,
            _with_hint(f),
            file=f.path,
            line=f.line,
            title=f"Health: {biomarker_label(f.rule)}",
        )
        for f in [*result.at(ERROR), *result.at(WARNING)]
    ]
    out = github.cap_annotations(out, noun="health findings")
    if reproduce and not result.passed:
        out.append(github.notice(_reproduce_line(reproduce)))
    return out


#: How the gate is installed outside the Action, as docs/start/CI.md has it.
_INSTALL = "pip install repowise"


def _reproduce_line(reproduce: str) -> str:
    return f"Reproduce locally: {_INSTALL}, then {reproduce}"


def _with_hint(f: GateFinding) -> str:
    """*f*'s message, plus the first sentence of its fix when it fails the gate."""
    hint = hint_for(f.rule) if f.level == ERROR and f.source == _SOURCE else None
    return f"{f.message}. {hint}" if hint else f.message


def gate_summary(result: GateResult, *, reproduce: str | None = None) -> GateSummary:
    """The gate's row in the combined CI summary: errors and warnings, notes left out.

    A failing row with *reproduce* names the local command in its headline.
    """
    errors, warnings = result.at(ERROR), result.at(WARNING)
    headline = {
        PASS: "Nothing this change wrote fails the gate.",
        WARN: f"Passed with {plural(len(warnings), 'warning')}.",
        FAIL: f"Fails on {plural(len(errors), 'finding')} the change wrote.",
    }[result.verdict]
    if reproduce and not result.passed:
        headline = f"{headline} {_reproduce_line(reproduce)}"
    findings = tuple(
        SummaryFinding(f.level, f.path, f.line, f.message, f.fingerprint)
        for f in [*errors, *warnings]
    )
    return GateSummary(_SOURCE, result.verdict, headline, findings)


def render_sarif(result: GateResult, *, tool_version: str) -> dict:
    """One SARIF 2.1.0 run over the errors and warnings; notes stay out of code scanning."""
    shown = [*result.at(ERROR), *result.at(WARNING)]
    rules = [
        sarif.rule(rule, _rule_name(rule), label, f"{label}. {FINDINGS_BASIS}")
        for rule in sorted({f.rule for f in shown})
        for label in [biomarker_label(rule)]
    ]
    results = [
        sarif.result(
            f.rule,
            f.level,
            f.message,
            f.path,
            f.line,
            SARIF_FINGERPRINT_KEY,
            f.fingerprint,
            {k: v for k, v in f.extra.items() if isinstance(v, str)},
        )
        for f in shown
    ]
    return sarif.run(
        SARIF_TOOL_NAME, tool_version, rules, results, properties={"verdict": result.verdict}
    )


def _rule_name(rule: str) -> str:
    return "".join(part.capitalize() for part in rule.split("_"))


_GITLAB_SEVERITY = {ERROR: "major", WARNING: "minor"}


def render_gitlab(result: GateResult) -> list[dict]:
    """GitLab Code Quality issues for the errors (``major``) and warnings (``minor``)."""
    return gitlab.report(
        SARIF_TOOL_NAME,
        (
            gitlab.issue(
                f.rule, _GITLAB_SEVERITY[f.level], f.message, f.path, f.line, f.fingerprint
            )
            for f in result.findings
            if f.level in _GITLAB_SEVERITY
        ),
    )


def as_json(result: GateResult, delta: ChangeHealthDelta, *, label: str) -> dict[str, Any]:
    """The JSON document: the comparison's scope, every finding, the gate."""
    from .health import HEALTH_ANALYZER_VERSION

    return {
        "revspec": label,
        "analyzer_version": HEALTH_ANALYZER_VERSION,
        "status": delta.status,
        "explanation": delta.explanation,
        "scope": delta.scope.as_dict(),
        "skipped": dict(delta.skipped),
        "findings_basis": FINDINGS_BASIS,
        "findings": [f.to_dict() for f in result.findings],
        "gate": result.to_dict(),
        # Context only: a score never decides the verdict.
        "files": [row.as_dict() for row in delta.files],
    }

