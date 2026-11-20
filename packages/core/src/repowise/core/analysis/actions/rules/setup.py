"""Setup steps in the ``improve_signal`` tier: each shows when its fact is observed.

They read :class:`~..setup.SetupFacts`, which is gathered live, so a step
leaves the list the moment it is done, with no index update in between.
Every one carries the exact command that finishes it; the pass and the
per-row prompts run that command.
"""

from __future__ import annotations

from collections.abc import Callable

from repowise.core.analysis.next_call import ActionCommand
from repowise.core.evidence.setup import EVIDENCE_SETUP, SETUP_COMMAND, coverage_ingest_line

from ..model import Action, ActionDetail, RuleOutcome, WhyFact, fingerprint
from ..setup import AGENT_CALLS_UNLOCK, NAMED_FILES, SYNCED_COMMITS_UNLOCK, SetupFacts
from ._text import code, plural

SetupRule = Callable[[SetupFacts], RuleOutcome]

SYNC_COMMAND = "repowise hook install"
DISCOVER_COMMAND = "repowise decision discover"
SUGGESTED_COMMAND = "repowise config list --suggested"
REWRITE_COMMAND = "repowise hook rewrite install"

#: What each evidence format holds, in a row title.
_EVIDENCE_WORDS = {
    "pytest-benchmark": "benchmark results",
    "hyperfine": "benchmark results",
    "junit": "test timings",
}
_BENCHMARK_DIR = ".benchmarks/"


def _step(
    rule: str,
    *,
    title: str,
    impact: str,
    why: tuple[WhyFact, ...],
    surface: str,
    done_when: str,
    command: str,
    marks: tuple[object, ...],
    details: tuple[ActionDetail, ...] = (),
    commands: tuple[ActionCommand, ...] = (),
) -> RuleOutcome:
    action = Action(
        rule=rule,  # type: ignore[arg-type]
        tier="improve_signal",
        horizons=("week", "quarter"),
        severity="low",
        title=title,
        impact=impact,
        why=why,
        target_kind="repo",
        target_path="",
        surface=surface,  # type: ignore[arg-type]
        effort="S",
        confidence="high",
        done_when=done_when,
        command=command,
        commands=commands,
        details=details,
        details_total=len(details),
        fingerprint=fingerprint(*marks),
    )
    return RuleOutcome(action.rule, "evaluated", "", (action,))


def _unknown(rule: str, facts: SetupFacts, key: str) -> RuleOutcome:
    return RuleOutcome(rule, "unavailable", facts.unavailable.get(key, "Could not be read here."))  # type: ignore[arg-type]


def _age(days: int) -> str:
    return "today" if days == 0 else plural(days, "day")


def sync_hook_missing(facts: SetupFacts) -> RuleOutcome:
    rule = "sync_hook_missing"
    if facts.sync_hook is None:
        return _unknown(rule, facts, "sync_hook")
    if facts.sync_hook:
        return RuleOutcome(rule, "evaluated", "The post-commit hook keeps the index in sync.")
    behind = facts.unsynced_commits
    why = (WhyFact("commits since last sync", f"{behind:,}"),) if behind is not None else ()
    return _step(
        rule,
        title="Keep the index in sync on commit",
        impact=(
            "The index updates only when you run `repowise update`. The hook "
            "updates it after each commit."
        ),
        why=why,
        surface="setup",
        done_when="The post-commit hook is installed.",
        command=SYNC_COMMAND,
        marks=("missing",),
    )


def coverage_found(facts: SetupFacts) -> RuleOutcome:
    rule = "coverage_found"
    if facts.coverage_ingested is None:
        return _unknown(rule, facts, "coverage")
    if facts.coverage_ingested:
        return RuleOutcome(rule, "evaluated", "A coverage report is ingested.")
    reports = facts.coverage_reports
    if not reports:
        return RuleOutcome(rule, "evaluated", "No coverage report on disk.")
    newest = min(r.age_days for r in reports)
    if len(reports) == 1:
        title = f"Add the coverage report at {code(reports[0].path)}"
        why = (WhyFact("report age", _age(newest)),)
    else:
        title = f"Add the {len(reports)} coverage reports found on disk"
        why = (WhyFact("reports found", str(len(reports))), WhyFact("newest", _age(newest)))
    # Past the named few, the bare command discovers and ingests every report.
    named = reports[:NAMED_FILES]
    command = (
        coverage_ingest_line(r.path for r in reports)
        if len(reports) <= NAMED_FILES
        else "repowise coverage add"
    )
    return _step(
        rule,
        title=title,
        impact=(
            "It is on disk but not read yet. With it, tested and untested code "
            "read as measured, not unknown."
        ),
        why=why,
        surface="coverage",
        done_when="A coverage report is ingested.",
        command=command,
        marks=tuple(r.path for r in reports),
        details=tuple(ActionDetail(r.path, reason=f"modified {_age(r.age_days)} ago") for r in named),
    )


def evidence_found(facts: SetupFacts) -> RuleOutcome:
    rule = "evidence_found"
    if facts.runtime_stored is None:
        return _unknown(rule, facts, "runtime")
    if facts.runtime_stored:
        return RuleOutcome(rule, "evaluated", "Runtime evidence is ingested.")
    files = facts.evidence_files
    if not files:
        return RuleOutcome(rule, "evaluated", "No profile or benchmark file on disk.")
    words = sorted({_EVIDENCE_WORDS.get(f.kind, "profile") for f in files})
    if all(f.path.startswith(_BENCHMARK_DIR) for f in files):
        title = f"Connect the benchmark results in {code(_BENCHMARK_DIR)}"
    elif len(files) == 1:
        title = f"Connect the {words[0]} at {code(files[0].path)}"
    else:
        title = f"Connect the {len(files)} profiles and benchmark files found on disk"
    return _step(
        rule,
        title=title,
        impact=(
            "Performance can then rank slow paths by measured time, not only by code shape."
        ),
        why=(WhyFact("found", ", ".join(sorted({f.kind for f in files}))),),
        surface="performance",
        done_when="A profile or benchmark run is ingested.",
        command=EVIDENCE_SETUP,
        marks=tuple(f.path for f in files),
        details=tuple(ActionDetail(f.path, reason=f.kind) for f in files),
        commands=(
            ActionCommand(
                "The exact collect and ingest steps for this repository, as a `next_call`",
                cli=SETUP_COMMAND,
            ),
        ),
    )


def decisions_unchecked(facts: SetupFacts) -> RuleOutcome:
    rule = "decisions_unchecked"
    count = facts.background_records
    if count is None:
        return _unknown(rule, facts, "decisions")
    if count == 0:
        return RuleOutcome(rule, "evaluated", "No background record waits to be checked.")
    return _step(
        rule,
        title=f"Check {plural(count, 'background record')} on the busiest files",
        impact=(
            "Repowise mined them from history. None is in force yet, so no agent "
            "hears about them when it edits that code."
        ),
        why=(WhyFact("background records", f"{count:,}"),),
        surface="decisions",
        done_when="Each record is put in force, dismissed or left as background.",
        command=DISCOVER_COMMAND,
        marks=(count,),
    )


def docs_model_ready(facts: SetupFacts) -> RuleOutcome:
    rule = "docs_model_ready"
    if facts.model_source is None:
        return RuleOutcome(
            rule, "evaluated", "A model writes the docs, or no key or local model is set up."
        )
    provider, source, command = facts.model_source
    return _step(
        rule,
        title="Write the docs with a model",
        impact=(
            "Docs and answers are built from the index today. A model adds how and "
            "why to each page. It sends code to the model and may cost money, so ask first."
        ),
        why=(WhyFact("model found", source),),
        surface="setup",
        done_when="A model has written the docs.",
        command=command,
        marks=(provider,),
    )


def settings_suggested(facts: SetupFacts) -> RuleOutcome:
    rule = "settings_suggested"
    if facts.synced_commits is None:
        return _unknown(rule, facts, "history")
    if facts.synced_commits < SYNCED_COMMITS_UNLOCK:
        return RuleOutcome(
            rule,
            "not_applicable",
            f"Suggested after {SYNCED_COMMITS_UNLOCK} synced commits.",
        )
    keys = facts.suggested_settings
    if keys is None:
        return _unknown(rule, facts, "settings")
    if not keys:
        return RuleOutcome(rule, "evaluated", "No setting is suggested for this repository.")
    return _step(
        rule,
        title=f"Review {plural(len(keys), 'setting')} that fit{'s' if len(keys) == 1 else ''} this repository",
        impact="Picked from the settings registry for this repository's languages, size and history.",
        why=(WhyFact("settings", ", ".join(keys[:3]) + (", …" if len(keys) > 3 else "")),),
        surface="setup",
        done_when="Each suggested setting is set or left as it is.",
        command=SUGGESTED_COMMAND,
        marks=tuple(sorted(keys)),
    )


def _claude_code_unlocked(rule: str, facts: SetupFacts) -> RuleOutcome | None:
    if facts.claude_code_calls < AGENT_CALLS_UNLOCK:
        return RuleOutcome(
            rule,  # type: ignore[arg-type]
            "not_applicable",
            f"Suggested after {AGENT_CALLS_UNLOCK} Claude Code calls to this index.",
        )
    return None


def always_load_suggested(facts: SetupFacts) -> RuleOutcome:
    from repowise.core.settings import registry
    from repowise.core.settings.views import set_command

    rule = "always_load_suggested"
    locked = _claude_code_unlocked(rule, facts)
    if locked is not None:
        return locked
    if facts.always_load is None:
        return _unknown(rule, facts, "settings")
    if facts.always_load:
        return RuleOutcome(rule, "evaluated", "The read tools already load up front.")
    setting = registry.get("mcp.always_load")
    return _step(
        rule,
        title="Load the four read tools up front",
        impact=(
            "Claude Code then loads get_answer, get_context, search_codebase and "
            "get_symbol at session start, so its first lookup skips a tool-loading "
            f"turn. Costs {setting.cost_note}. Restart Claude Code after."
        ),
        why=(WhyFact("Claude Code calls", f"{facts.claude_code_calls:,}"),),
        surface="setup",
        done_when="`mcp.always_load` is on.",
        command=set_command(setting.key, True),
        marks=("off",),
    )


def distill_suggested(facts: SetupFacts) -> RuleOutcome:
    rule = "distill_suggested"
    locked = _claude_code_unlocked(rule, facts)
    if locked is not None:
        return locked
    if facts.rewrite_hook is None:
        return _unknown(rule, facts, "rewrite_hook")
    if facts.rewrite_hook:
        return RuleOutcome(rule, "evaluated", "Command output already goes through distill.")
    return _step(
        rule,
        title="Shorten command output with distill",
        impact=(
            "Claude Code then reads test, build and git output through distill, so "
            "long logs cost fewer tokens."
        ),
        why=(WhyFact("Claude Code calls", f"{facts.claude_code_calls:,}"),),
        surface="setup",
        done_when="The rewrite hook is installed.",
        command=REWRITE_COMMAND,
        marks=("off",),
    )


SETUP_RULES: tuple[SetupRule, ...] = (
    sync_hook_missing,
    coverage_found,
    evidence_found,
    decisions_unchecked,
    docs_model_ready,
    settings_suggested,
    always_load_suggested,
    distill_suggested,
)

#: A stored rule a live step replaces when the step fires: a found report is
#: the better "add a report", found profiles the better "connect evidence".
SUPERSEDES: dict[str, tuple[str, ...]] = {
    "coverage_found": ("coverage_missing",),
    "evidence_found": ("runtime_missing",),
}
