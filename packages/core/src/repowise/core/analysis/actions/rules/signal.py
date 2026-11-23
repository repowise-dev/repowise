"""Rules that make the other rules more accurate.

Kept in their own tier so they never outrank work: they tell the reader what
Repowise cannot see yet, and what one command would change.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from repowise.core.analysis.next_call import ActionCommand
from repowise.core.evidence.setup import COVERAGE_SETUP, EVIDENCE_SETUP, SETUP_COMMAND

from ..context import RepoContext
from ..facts import RepoFacts
from ..model import Action, RuleOutcome, WhyFact, fingerprint

#: Below this many production files, a coverage report or a profile is not
#: worth asking for.
COVERAGE_MIN_FILES = 20
#: Coverage is stale once measured at an earlier commit than the index's
#: (``build.build_coverage``); a refresh is asked for only once it is this old.
#: Runtime evidence is held to the same bar.
COVERAGE_STALE_AFTER = timedelta(days=14)

#: The step every rule here hands over: setup prints the exact commands for
#: this repository's stacks (run, then ingest), and runs nothing itself. A
#: person runs the plain line; an agent reads the JSON and its ``next_call``.
_COVERAGE_COMMANDS = (
    ActionCommand(
        "The test command with coverage for each build unit and the exact ingest line",
        cli=f"{COVERAGE_SETUP} --format json",
    ),
)
_RUNTIME_COMMANDS = (
    ActionCommand(
        "The profiler command for the best target (entry point, benchmark, then tests) "
        "and the exact ingest line",
        cli=SETUP_COMMAND,
    ),
)

#: How ``evidence setup`` names each workload class, in its order.
_TARGET_WORDS = {"entry_point": "entry points", "benchmark": "benchmarks", "unit_test": "a test suite"}


def _age(anchor: datetime, then: datetime) -> timedelta:
    return anchor.replace(tzinfo=None) - then.replace(tzinfo=None)


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
        command=COVERAGE_SETUP,
        commands=_COVERAGE_COMMANDS,
        fingerprint=fingerprint("none"),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


def coverage_stale(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "coverage_stale"
    cov = facts.coverage
    if cov.status != "stale" or cov.ingested_at is None or ctx.anchor is None:
        return RuleOutcome(rule, "evaluated", "Coverage is current or absent.")
    age = _age(ctx.anchor, cov.ingested_at)
    if age < COVERAGE_STALE_AFTER:
        return RuleOutcome(
            rule,
            "evaluated",
            "Coverage was measured at an earlier commit; a refresh is asked for once "
            f"the report is {COVERAGE_STALE_AFTER.days} days older than the latest commit.",
        )
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
        command=COVERAGE_SETUP,
        commands=_COVERAGE_COMMANDS,
        fingerprint=fingerprint(age.days // 14),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


def runtime_missing(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "runtime_missing"
    if "runtime" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["runtime"])
    rt = facts.runtime
    if rt.status != "unknown":
        return RuleOutcome(rule, "evaluated", "Runtime evidence is ingested.")
    if not rt.targets:
        return RuleOutcome(rule, "not_applicable", "Evidence setup offers nothing to run.")
    if ctx.production_files < COVERAGE_MIN_FILES:
        return RuleOutcome(rule, "not_applicable", "Too few files for a profile to change the list.")
    found = [_TARGET_WORDS[t] for t in rt.targets if t in _TARGET_WORDS]
    action = Action(
        rule=rule,
        tier="improve_signal",
        horizons=("week", "quarter"),
        severity="low",
        title="Connect runtime evidence: profile a real run",
        impact=(
            "Without a profile or benchmark run, performance findings rest on code shape "
            "alone, and none can be ranked by measured cost."
        ),
        why=(
            WhyFact("runtime evidence", "None stored", "unknown"),
            WhyFact("found to run", ", ".join(found), "inferred"),
        ),
        target_kind="repo",
        target_path="",
        surface="performance",
        effort="S",
        confidence="high",
        done_when="A profile or benchmark run is ingested.",
        command=EVIDENCE_SETUP,
        commands=_RUNTIME_COMMANDS,
        fingerprint=fingerprint("none"),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))


def runtime_stale(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    rule = "runtime_stale"
    rt = facts.runtime
    if rt.status != "stale" or rt.ingested_at is None or ctx.anchor is None:
        return RuleOutcome(rule, "evaluated", "Runtime evidence is current or absent.")
    age = _age(ctx.anchor, rt.ingested_at)
    if age < COVERAGE_STALE_AFTER:
        return RuleOutcome(
            rule,
            "evaluated",
            "Runtime evidence was measured at an earlier commit; a refresh is asked for once "
            f"the newest run is {COVERAGE_STALE_AFTER.days} days older than the latest commit.",
        )
    action = Action(
        rule=rule,
        tier="improve_signal",
        horizons=("week", "quarter"),
        severity="low",
        title="Refresh the runtime evidence",
        impact=(
            f"The newest run was ingested {age.days} days before the latest commit, at an "
            "earlier commit, so measured costs may no longer match the code."
        ),
        why=(
            WhyFact("newest run age", f"{age.days} days"),
            WhyFact("measured at", (rt.commit_sha or "unknown")[:12]),
            WhyFact("runs stored", str(rt.runs)),
        ),
        target_kind="repo",
        target_path="",
        surface="performance",
        effort="S",
        confidence="high",
        done_when="A run from the current commit is ingested.",
        command=EVIDENCE_SETUP,
        commands=_RUNTIME_COMMANDS,
        fingerprint=fingerprint(age.days // 14),
    )
    return RuleOutcome(rule, "evaluated", "", (action,))
