"""Rules that make the other rules more accurate.

Kept in their own tier so they never outrank work: they tell the reader what
Repowise cannot see yet, and what one command would change.
"""

from __future__ import annotations

from datetime import timedelta

from ..context import RepoContext
from ..facts import RepoFacts
from ..model import Action, RuleOutcome, WhyFact, fingerprint
from ._text import plural

#: Below this many production files, a coverage report is not worth asking for.
COVERAGE_MIN_FILES = 20
COVERAGE_STALE_AFTER = timedelta(days=14)
DECISIONS_REVIEW_MIN = 5


def coverage_missing(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "coverage_missing"
    if "coverage" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["coverage"])
    if facts.coverage.status != "unknown":
        return RuleOutcome(rule, "evaluated", "A coverage report is ingested.")
    if ctx.production_files < COVERAGE_MIN_FILES:
        return RuleOutcome(rule, "not_applicable", "Too few files for coverage to change the list.")
    action = Action(
        rule=rule,
        tier="improve_signal",
        horizons=("week", "quarter"),
        severity="low",
        title="Add a test coverage report",
        impact=(
            "Without one, Repowise cannot tell tested code from untested code, so "
            "every test-related action says “unknown”."
        ),
        why=(WhyFact("line coverage", "Unknown, no report", "unknown"),),
        target_kind="repo",
        target_path="",
        surface="coverage",
        effort="S",
        confidence="high",
        done_when="A coverage report is ingested.",
        command="repowise coverage add <report>",
        fingerprint=fingerprint("none"),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


def coverage_stale(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "coverage_stale"
    cov = facts.coverage
    if cov.status != "stale" or cov.ingested_at is None or ctx.anchor is None:
        return RuleOutcome(rule, "evaluated", "Coverage is current or absent.")
    age = ctx.anchor.replace(tzinfo=None) - cov.ingested_at.replace(tzinfo=None)
    if age < COVERAGE_STALE_AFTER:
        return RuleOutcome(rule, "evaluated", "Coverage is recent enough.")
    action = Action(
        rule=rule,
        tier="improve_signal",
        horizons=("week", "quarter"),
        severity="low",
        title="Refresh the test coverage report",
        impact=(
            f"It was measured {age.days} days before the latest commit, so "
            "coverage figures on changed files may be out of date."
        ),
        why=(
            WhyFact("report age", f"{age.days} days"),
            WhyFact("files measured", str(cov.files_measured)),
        ),
        target_kind="repo",
        target_path="",
        surface="coverage",
        effort="S",
        confidence="high",
        done_when="A report from the current commit is ingested.",
        command="repowise coverage add <report>",
        fingerprint=fingerprint(age.days // 14),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


def decisions_unreviewed(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "decisions_unreviewed"
    if "decisions" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["decisions"])
    n = facts.proposed_decisions
    if facts.accepted_decisions > 0 or n < DECISIONS_REVIEW_MIN:
        return RuleOutcome(rule, "evaluated", "Decisions are reviewed, or too few are proposed.")
    action = Action(
        rule=rule,
        tier="improve_signal",
        horizons=("quarter",),
        severity="low",
        title=f"Review the {plural(n, 'proposed decision')}",
        impact=(
            "None is accepted yet, so nothing can drift from one and agents get no "
            "enforced guidance from them."
        ),
        why=(WhyFact("proposed", str(n)), WhyFact("accepted", "0")),
        target_kind="repo",
        target_path="",
        surface="decisions",
        effort="M",
        confidence="high",
        done_when="The decisions that hold are accepted.",
        fingerprint=fingerprint(n // 25),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))
