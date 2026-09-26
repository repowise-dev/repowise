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
from dataclasses import dataclass
from typing import Literal

from .change_health.models import ChangeHealthDelta
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


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"


def _verdict(delta: ChangeHealthDelta) -> tuple[ReviewStatus, str, EvidenceState]:
    if delta.status in _NOT_COMPARED:
        return "unknown", f"Change health could not be compared: {delta.explanation}", "unavailable"
    if delta.status in _UNTRUSTWORTHY:
        return "unknown", f"No trustworthy baseline: {delta.explanation}", "degraded"

    blocking = [
        f
        for f in delta.findings
        if f.severity in _BLOCKING_SEVERITIES and f.dimension not in _ADVISORY_DIMENSIONS
    ]
    state: EvidenceState = "partial" if delta.status == "partial" else "available"
    if blocking:
        lead = blocking[0]
        return (
            "review_required",
            f"{len(blocking)} new {_plural('finding', len(blocking))} "
            f"{'needs' if len(blocking) == 1 else 'need'} review, "
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
        for f in delta.findings
    ]
    if delta.skipped:
        reasons.append(
            f"{len(delta.skipped)} changed {_plural('file', len(delta.skipped))} "
            "were not analysed, so this is not a clean bill."
        )
    return tuple(reasons)


def _actions(delta: ChangeHealthDelta, tests: CoveringTestEvidence) -> tuple[ReviewAction, ...]:
    actions: list[ReviewAction] = []
    for priority, finding in enumerate(delta.findings):
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
    if tests.tests_to_run:
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
    if delta.skipped:
        actions.append(
            ReviewAction(
                kind="review_skipped_files",
                priority=next_priority,
                targets=tuple(sorted(delta.skipped)),
                explanation="Review the skipped files by hand; they were not compared.",
            )
        )
    return tuple(actions)


def review_directive(
    delta: ChangeHealthDelta,
    tests: CoveringTestEvidence | None = None,
) -> ReviewDirective:
    """Decide the verdict, its reasons, and the actions. Uncapped and unrendered."""
    tests = tests or CoveringTestEvidence()
    status, headline, state = _verdict(delta)
    return ReviewDirective(
        status=status,
        headline=headline,
        reasons=_reasons(delta),
        actions=_actions(delta, tests),
        evidence_state=state,
    )

