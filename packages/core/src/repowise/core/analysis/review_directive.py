"""What a change review concludes, as data rather than prose.

A verdict, why it was reached, and what to do next are analysis, not
presentation: two surfaces that render them differently must still reach the
same conclusion. So the policy lives here and the rendering does not.

Core may carry a factual explanation. It must not carry Markdown, URLs, tool
invocation syntax, or per-surface limits -- a surface caps and formats what it
is given.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .change_health.file_scores import score_change
from .change_health.models import NOT_ANALYZABLE_DATA, ChangeFinding, ChangeHealthDelta
from .health.scoring import ADVISORY_DIMENSION

#: How much of a lane's evidence was actually computed.
#:
#: ``available`` with an empty population is known-empty, which is a different
#: claim from ``unavailable``. None of these may collapse into ``None``, a
#: missing key, or an empty list.
EvidenceState = Literal[
    "available",  # computed successfully
    "partial",  # some of the requested scope was analyzed
    "unavailable",  # required data was not provided
    "degraded",  # computation failed or fell back to something weaker
    "unsupported",  # the provider cannot answer this at all
]

ReviewStatus = Literal[
    "review_required",
    "review_recommended",
    "clear_in_analyzed_scope",
    "unknown",
]

ReviewActionKind = Literal[
    "inspect_finding",
    "run_tests",
    "establish_test_coverage",
    "review_skipped_files",
]

_BLOCKING_SEVERITIES = {"high", "critical"}

#: Dimensions whose findings are advice, not a gate. An unlisted dimension
#: gates like a defect, which is the safe default.
_ADVISORY_DIMENSIONS = {"performance", ADVISORY_DIMENSION}

#: Delta statuses that mean the comparison did not happen.
_NOT_COMPARED = {"unavailable", "unsupported_range", "too_large", "timeout"}

#: Delta statuses that mean it happened against something untrustworthy.
_UNTRUSTWORTHY = {"analyzer_mismatch", "rules_mismatch", "stale_baseline"}


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class ReviewAction:
    """One thing to do next, and what makes it worth doing."""

    kind: ReviewActionKind
    #: 0 is most urgent. Ordering within a directive, not a global scale.
    priority: int
    #: Paths, symbol ids, or test ids, depending on *kind*. Uncapped.
    targets: tuple[str, ...] = ()
    #: Where the evidence for this action came from, in the emitting lane's own
    #: vocabulary (for example a finding's attribution basis, or "measured").
    evidence_basis: str | None = None
    #: A plain factual sentence. No Markdown, no URLs, no call syntax.
    explanation: str = ""

    @property
    def fingerprint(self) -> str:
        """Stable across runs, so a surface can tell a repeat from a new action."""
        return _fingerprint(self.kind, *self.targets)


@dataclass(frozen=True, slots=True)
class ReviewDirective:
    """The verdict on a change, its reasons, and its actions."""

    status: ReviewStatus
    headline: str
    reasons: tuple[str, ...] = ()
    actions: tuple[ReviewAction, ...] = ()
    #: How complete the evidence behind this verdict is.
    evidence_state: EvidenceState = "available"

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.status, *(a.fingerprint for a in self.actions))


@dataclass(frozen=True, slots=True)
class CoveringTestEvidence:
    """The test lane's contribution, already resolved by its own analyzer.

    The default is ``unsupported``: a caller that supplies no test evidence has
    not consulted the lane, which is a different claim from having looked and
    found no coverage map. Only the latter is grounds to ask for coverage.
    """

    state: EvidenceState = "unsupported"
    #: Uncapped. A surface decides how many to show.
    tests_to_run: tuple[str, ...] = ()
    basis: str | None = None  # measured | inferred | None
    #: Why every test must run, when nothing narrower can be vouched for.
    run_all: str | None = None


def _blocks(finding: ChangeFinding) -> bool:
    return finding.severity in _BLOCKING_SEVERITIES and finding.dimension not in _ADVISORY_DIMENSIONS


def review_order(findings: Iterable[ChangeFinding]) -> list[ChangeFinding]:
    """The findings that gate the verdict first, each group in its given order.

    The headline, reasons, inspect actions and any finding list a surface
    shows all read this one order, so the finding the headline names leads
    every one of them.
    """
    return sorted(findings, key=lambda f: not _blocks(f))


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _no_code_headline(delta: ChangeHealthDelta) -> str:
    if NOT_ANALYZABLE_DATA in delta.skipped.values():
        return (
            "No code changed: only query, template or other data files, which health analysis "
            "does not cover. They can still change behaviour, so test selection still applies."
        )
    return (
        "No code changed: only documentation or configuration files; "
        "health analysis does not cover them."
    )


def _verdict(delta: ChangeHealthDelta) -> tuple[ReviewStatus, str, EvidenceState]:
    if delta.status == "unavailable" and delta.skipped and not delta.code_skipped:
        return "clear_in_analyzed_scope", _no_code_headline(delta), "available"
    if delta.status in _NOT_COMPARED:
        return "unknown", f"Change health could not be compared: {delta.explanation}", "unavailable"
    if delta.status in _UNTRUSTWORTHY:
        return "unknown", f"No trustworthy baseline: {delta.explanation}", "degraded"

    blocking = [f for f in delta.findings if _blocks(f)]
    state: EvidenceState = "partial" if delta.status == "partial" else "available"
    if blocking:
        lead, total = blocking[0], len(delta.findings)
        # Out of every finding, so the headline's count agrees with the delta's.
        counted = (
            f"{len(blocking)} new {_plural('finding', len(blocking))}"
            if len(blocking) == total
            else f"{len(blocking)} of {total} new findings"
        )
        return (
            "review_required",
            f"{counted} {'needs' if len(blocking) == 1 else 'need'} review, "
            f"starting with {lead.biomarker_type} in {lead.path}.",
            state,
        )
    # Performance is excluded from BLOCKING above but still counts here: it
    # moves a score. Advisory never does.
    scoring = [f for f in delta.findings if f.dimension != ADVISORY_DIMENSION]
    if scoring:
        return (
            "review_recommended",
            f"{len(scoring)} new {_plural('finding', len(scoring))} "
            "of low or advisory severity.",
            state,
        )
    if delta.status == "partial":
        return (
            "unknown",
            "Nothing new in what was compared, but part of the change was not analysed.",
            "partial",
        )
    return (
        "clear_in_analyzed_scope",
        "No supported new findings surfaced in the analyzed scope.",
        "available",
    )


def _reasons(delta: ChangeHealthDelta) -> tuple[str, ...]:
    reasons = [
        f"{f.severity} {f.dimension}: {f.biomarker_type} in "
        f"{f.symbol or f.path} ({f.attribution_basis})"
        for f in review_order(delta.findings)
    ]
    skipped = len(delta.code_skipped)
    if skipped:
        reasons.append(
            f"{skipped} changed {_plural('file', skipped)} "
            "were not analysed, so this is not a clean bill."
        )
    return tuple(reasons)


def _actions(delta: ChangeHealthDelta, tests: CoveringTestEvidence) -> tuple[ReviewAction, ...]:
    actions: list[ReviewAction] = []
    for priority, finding in enumerate(review_order(delta.findings)):
        where = f"{finding.path}:{finding.line_start}" if finding.line_start else finding.path
        actions.append(
            ReviewAction(
                kind="inspect_finding",
                priority=priority,
                targets=(finding.change_finding_id,),
                evidence_basis=finding.attribution_basis,
                explanation=f"Inspect {where}",
            )
        )
    next_priority = len(actions)
    if tests.run_all:
        actions.append(
            ReviewAction(
                kind="run_tests",
                priority=next_priority,
                evidence_basis=tests.basis,
                explanation=f"Run every test: {tests.run_all}",
            )
        )
        next_priority += 1
    elif tests.tests_to_run:
        actions.append(
            ReviewAction(
                kind="run_tests",
                priority=next_priority,
                targets=tests.tests_to_run,
                evidence_basis=tests.basis,
                explanation="Run the tests that cover the changed files.",
            )
        )
        next_priority += 1
    elif tests.state == "unavailable":
        actions.append(
            ReviewAction(
                kind="establish_test_coverage",
                priority=next_priority,
                evidence_basis=tests.basis,
                explanation="No measured test map; run the suite covering the changed files.",
            )
        )
        next_priority += 1
    if delta.code_skipped:
        actions.append(
            ReviewAction(
                kind="review_skipped_files",
                priority=next_priority,
                targets=tuple(sorted(delta.code_skipped)),
                explanation="Review the skipped files by hand; they were not compared.",
            )
        )
    return tuple(actions)


def _net_health(delta: ChangeHealthDelta) -> str:
    """The changed code's base-nloc average score on both sides, or ``""`` without one.

    The score reads code structure only. When code moved between files, the
    source is scored on what stayed behind and the destination has no base side,
    so a pure move would read as a gain or a loss; name the move instead.
    """
    change = score_change(delta.files)
    if change is None:
        return ""
    if delta.moved_total:
        return (
            " Code moved between files, so the changed code's health"
            " (structure only) is not compared."
        )
    base, head = round(change.base, 2), round(change.head, 2)
    if base == head:
        return f" Health of the changed code holds at {head:.2f}."
    return f" Health of the changed code: {base:.2f} to {head:.2f} ({head - base:+.2f})."


def review_directive(
    delta: ChangeHealthDelta,
    tests: CoveringTestEvidence | None = None,
) -> ReviewDirective:
    """Decide the verdict, its reasons, and the actions. Uncapped and unrendered."""
    tests = tests or CoveringTestEvidence()
    status, headline, state = _verdict(delta)
    return ReviewDirective(
        status=status,
        headline=headline + _net_health(delta),
        reasons=_reasons(delta),
        actions=_actions(delta, tests),
        evidence_state=state,
    )

