"""Rules about the code itself: what got worse, what keeps breaking, what to fix first."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from datetime import datetime

from ..context import HISTORY_TOO_SHORT, RepoContext
from ..facts import FileFacts, RepoFacts
from ..model import Action, ActionCommand, ActionDetail, RuleOutcome, WhyFact, fingerprint
from ._text import code, plural

#: Files that tell the week's story as well as the quarter's: busy fragile
#: files someone touched this week, strongest first.
FRAGILE_WEEK_ROWS = 5

#: Line coverage at or above this reads as "the tests exist"; below it, adding
#: tests is the action.
COVERED_PCT = 80.0

#: ``fix_concentration`` needs enough fixes that a share means something, a
#: share worth a sentence, and lift: the folder's share of fixes over its share
#: of files. Lift is what separates `update_cmd/` (9% of fixes in 0.3% of the
#: files) from `packages/cli/` (29% of fixes in 7% of the files): both are
#: true, only one is an area someone can take on.
CONCENTRATION_MIN_FIXES = 20
CONCENTRATION_MIN_SHARE = 0.08
CONCENTRATION_MIN_LIFT = 4.0
CONCENTRATION_MIN_REPEAT_FILES = 3
CONCENTRATION_MAX_ACTIONS = 2


#: Fix-first items Do next reads; the queue is ranked, so these are its head.
FIX_FIRST_ACTIONS = 3

#: Up to this many files, each gets its own action; beyond it, one action
#: carries the week and names the worst files. A heavy week of feature work can
#: add findings to seventy files, and seventy rows is not a to-do list.
REGRESSION_FILE_ROWS = 3


def fresh_regressions(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    """Serious findings the last week of commits added and nobody has fixed yet.

    The loader keeps only findings the commit demonstrably wrote (added lines,
    a new file, or a changed symbol), still open, in production code, and not
    history markers: "you made this worse" has to be true of the diff.
    """
    rule = "fresh_regressions"
    if "commit_health" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["commit_health"])
    if ctx.anchor is None:
        return RuleOutcome(rule, "not_applicable", "No commit history in the index.")
    by_file: dict[str, list] = defaultdict(list)
    for f in facts.recent_findings:
        by_file[f.file_path].append(f)
    if not by_file:
        return RuleOutcome(rule, "evaluated")

    def weight(found: list) -> float:
        return sum(f.severity == "critical" for f in found) * 10.0 + len(found)

    ranked = sorted(by_file.items(), key=lambda kv: (-weight(kv[1]), kv[0]))
    if len(ranked) > REGRESSION_FILE_ROWS:
        return RuleOutcome(rule, "evaluated", "", (_regression_rollup(ranked),))
    return RuleOutcome(
        rule,
        "evaluated",
        "",
        tuple(_regression_file(path, found, ctx, weight(found)) for path, found in ranked),
    )


def _regression_file(path: str, found: list, ctx: RepoContext, weight: float) -> Action:
    symbols = sorted({f.symbol for f in found if f.symbol})
    critical = sum(f.severity == "critical" for f in found)
    commits = {f.sha: f for f in found}
    lead = max(found, key=lambda f: (f.severity == "critical", f.line or 0))
    if len(symbols) == 1:
        title = f"Simplify {code(symbols[0])} in {code(path)} while the change is fresh"
    elif symbols:
        title = f"Tidy {plural(len(symbols), 'function')} you changed in {code(path)} this week"
    else:
        title = f"Tidy what this week's changes left in {code(path)}"
    latest = max(commits.values(), key=lambda f: f.committed_at or ctx.anchor)
    return Action(
        rule="fresh_regressions",
        tier="act_now",
        horizons=("week",),
        severity="critical" if critical else "high",
        title=title,
        impact=(
            f"{plural(len(commits), 'commit')} in the last 7 days added or worsened "
            f"{plural(len(found), 'critical or high finding')} here, still open. "
            "Fixing now costs less than after the next change."
        ),
        why=(
            WhyFact("serious findings", str(len(found))),
            WhyFact("critical", str(critical)),
            WhyFact("latest commit", latest.subject[:72] or latest.sha[:7]),
        ),
        target_kind="symbol" if len(symbols) == 1 else "file",
        target_path=path,
        target_symbol=symbols[0] if len(symbols) == 1 else None,
        surface="findings",
        effort="S" if len(symbols) <= 2 else "M",
        confidence="high",
        done_when="The findings close on the next update.",
        marker=lead.biomarker,
        weight=weight,
        evidence_ids=tuple(sorted(commits)),
        evidence_total=len(found),
        fingerprint=fingerprint(len(found), critical),
        details=_regression_details(found),
        commands=_regression_commands([path], latest.sha),
    )


def _regression_details(found: list) -> tuple[ActionDetail, ...]:
    ordered = sorted(found, key=lambda f: (f.severity != "critical", f.file_path, f.line or 0))
    return tuple(
        ActionDetail(
            path=f.file_path,
            line=f.line,
            symbol=f.symbol,
            marker=f.biomarker,
            severity=f.severity,
            reason=f.reason,
            ref=f.sha,
        )
        for f in ordered
    )


def _regression_commands(paths: list[str], sha: str) -> tuple[ActionCommand, ...]:
    first = paths[0]
    return (
        ActionCommand.call(
            "Every open finding in these files, with its line and reason",
            "get_health",
            {"targets": paths[:10], "include": ["biomarkers"]},
            cli=f"repowise health --file {first}",
        ),
        ActionCommand.call(
            "The commit that last made the first file worse, and what it put at risk",
            "get_change_risk",
            {"revspec": sha},
            cli=f"git show {sha[:12]} -- {first}",
        ),
        ActionCommand.call(
            "A file's structure and callers before editing it",
            "get_context",
            {"targets": [first], "include": ["skeleton", "callers"]},
            cli=f"repowise context {first}",
        ),
    )


def _regression_rollup(ranked: list[tuple[str, list]]) -> Action:
    found = [f for _, fs in ranked for f in fs]
    critical = sum(f.severity == "critical" for f in found)
    commits = {f.sha for f in found}
    worst_path, worst = ranked[0]
    worst_critical = sum(f.severity == "critical" for f in worst)
    return Action(
        rule="fresh_regressions",
        tier="act_now",
        horizons=("week",),
        severity="critical" if critical else "high",
        title=(
            f"Clean up what this week's commits left: {plural(len(found), 'serious finding')} "
            f"in {len(ranked):,} files"
        ),
        impact=(
            f"{plural(len(commits), 'commit')} in the last 7 days added or worsened them, and they are "
            f"still open. Start with {code(worst_path)}"
            + (f", which has {worst_critical} critical." if worst_critical else ".")
        ),
        why=(
            WhyFact("serious findings", str(len(found))),
            WhyFact("critical", str(critical)),
            WhyFact("files", str(len(ranked))),
        ),
        target_kind="repo",
        target_path="",
        surface="commits",
        effort="L" if len(ranked) > 10 else "M",
        confidence="high",
        done_when="The findings close on the next update.",
        weight=float(critical * 10 + len(found)),
        evidence_ids=tuple(sorted(commits)),
        evidence_total=len(found),
        includes=tuple(path for path, _ in ranked[:10]),
        fingerprint=fingerprint(len(found) // 10, critical // 5),
        # Evidence in the order the files are ranked, so it starts where the
        # impact line says to start; the commit to inspect is the latest one
        # that touched that same file.
        details=tuple(d for _, fs in ranked for d in _regression_details(fs)),
        details_total=len(found),
        commands=_regression_commands(
            [path for path, _ in ranked],
            max(worst, key=lambda f: f.committed_at or datetime.min).sha,
        ),
    )


def _coverage_fact(f: FileFacts, facts: RepoFacts) -> WhyFact:
    if f.line_coverage_pct is not None:
        basis = "measured"
        value = f"{f.line_coverage_pct:.0f}%"
        if facts.coverage.status == "stale":
            value += " (older report)"
        return WhyFact("line coverage", value, basis)
    if facts.coverage.status == "unknown":
        return WhyFact("line coverage", "Unknown, no report", "unknown")
    if facts.coverage.partial:
        return WhyFact("line coverage", "Unknown, the report only partly mapped", "unknown")
    return WhyFact("line coverage", "Unknown, not in the report", "unknown")


def _reach_fact(f: FileFacts) -> WhyFact:
    if not f.tests_reaching:
        return WhyFact("tests that reach it", "None in the code graph", "inferred")
    via = (f.tests_reaching_via or "code graph").replace("-", " ")
    return WhyFact(
        "tests that reach it", f"{plural(f.tests_reaching, 'test file')}, {via}", "inferred"
    )


def _lead_name(f: FileFacts) -> str | None:
    """The lead function's name, unless the parser could only call it anonymous."""
    name = f.lead.function if f.lead else None
    return None if not name or name.startswith("<anonymous") else name


def _fragile_step(f: FileFacts) -> tuple[str, str, str] | None:
    """Title, done-when and variant for a fragile file, or ``None`` to stay quiet.

    Measured coverage decides first. Without it, "add tests" holds only when no
    test reaches the file in the code graph; a file tests do reach gets the
    simplify step instead, as an inferred claim.
    """
    cov = f.line_coverage_pct
    if cov is not None and cov < COVERED_PCT:
        return (
            f"Raise test coverage on {code(f.path)} from {cov:.0f}%",
            "Line coverage on this file reaches 80%.",
            "raise",
        )
    if cov is None and f.tests_reaching is None:
        # Unknown is not zero: with no coverage and no test map, neither "add
        # tests" nor "tests reach it" is a claim the facts support.
        return None
    if cov is None and f.tests_reaching == 0:
        return (
            f"Add tests around {code(f.path)} before its next change",
            "A coverage report includes it at 80% or more.",
            "unknown",
        )
    if f.lead is None:
        return None
    name = _lead_name(f)
    where = code(name) + " in " if name else ""
    tested = "it is tested" if cov is not None else "tests reach it"
    return (
        f"Simplify {where}{code(f.path)}: {tested}, and fixes keep landing",
        "Its lead finding closes and bug-fix commits slow down.",
        "simplify" if cov is not None else "reached",
    )


def is_fragile(f: FileFacts, ctx: RepoContext) -> bool:
    return (
        not f.is_test
        and f.bug_magnet
        and f.commits_90d >= ctx.busy_threshold
        and f.fix_commits_90d >= ctx.fix_threshold
        # A file with no branching is declarations: types, constants, a
        # re-export barrel. Tests are not what it needs, and "add tests around
        # types.ts" is the kind of advice that teaches a reader to skip the list.
        and (f.max_ccn is None or f.max_ccn > 1)
    )


def fragile_file(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    """Busy files where bug fixes keep landing, with the next step their tests allow."""
    rule = "fragile_file"
    if "files" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["files"])
    if ctx.history_too_short:
        return RuleOutcome(rule, "not_applicable", HISTORY_TOO_SHORT)
    if ctx.fix_commits_90d == 0:
        return RuleOutcome(rule, "not_applicable", "No bug-fix commits in the last 90 days.")
    # A file Fix first already names carries one action, not two.
    named = fix_first_paths(facts)
    fragile = sorted(
        (f for f in facts.files.values() if is_fragile(f, ctx) and f.path not in named),
        key=lambda f: -(f.fix_commits_90d * f.commits_90d),
    )
    touched = [f for f in fragile if ctx.in_week(f.last_commit_at)]
    week_rows = {f.path for f in touched[:FRAGILE_WEEK_ROWS]}
    actions = []
    for f in fragile:
        step = _fragile_step(f)
        if step is None:
            continue
        title, done, variant = step
        why = [
            WhyFact("commits in 90 days", str(f.commits_90d)),
            WhyFact("bug-fix commits", str(f.fix_commits_90d)),
            _coverage_fact(f, facts),
        ]
        if f.line_coverage_pct is None:
            why.append(_reach_fact(f))
        impact = (
            f"Changed {f.commits_90d} times in 90 days, and {f.fix_commits_90d} "
            "of those commits were bug fixes."
        )
        if f.dependents and f.dependents >= 5:
            why.append(WhyFact("files that import it", str(f.dependents)))
            impact += f" {f.dependents} files import it."
        actions.append(
            Action(
                rule=rule,
                tier="plan",
                horizons=("week", "quarter") if f.path in week_rows else ("quarter",),
                severity="high" if f.fix_commits_90d >= 10 else "medium",
                title=title,
                impact=impact,
                why=tuple(why),
                target_kind="file",
                target_path=f.path,
                target_symbol=_lead_name(f) if variant in ("simplify", "reached") else None,
                identity=f.path,
                surface="file",
                effort="M" if (f.nloc or 0) < 600 else "L",
                confidence="high" if variant in ("raise", "simplify") else "medium",
                done_when=done,
                marker=f.lead.biomarker if f.lead else None,
                weight=float(f.fix_commits_90d * f.commits_90d),
                fingerprint=fingerprint(variant, f.fix_commits_90d // 5),
                details=(
                    (
                        ActionDetail(
                            path=f.path,
                            line=f.lead.line,
                            symbol=f.lead.function,
                            marker=f.lead.biomarker,
                            severity=f.lead.severity,
                            reason=f.lead.reason,
                        ),
                    )
                    if f.lead
                    else ()
                ),
                commands=(
                    ActionCommand.call(
                        "Its bug-fix history, co-change partners and test gaps",
                        "get_risk",
                        {"targets": [f.path]},
                        cli=f"repowise risk -t {f.path}",
                    ),
                    ActionCommand.call(
                        "Every open finding in the file",
                        "get_health",
                        {"targets": [f.path], "include": ["biomarkers"]},
                        cli=f"repowise health --file {f.path}",
                    ),
                ),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


def fix_concentration(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    """One folder that takes a disproportionate share of the bug fixes.

    Told as an area of work rather than a list of files, because that is how a
    lead plans it. The deepest folder that still clears the share wins, so the
    answer is `update_cmd/`, not `packages/`.
    """
    rule = "fix_concentration"
    if "files" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["files"])
    if ctx.history_too_short:
        return RuleOutcome(rule, "not_applicable", HISTORY_TOO_SHORT)
    total = ctx.fix_commits_90d
    if total < CONCENTRATION_MIN_FIXES:
        return RuleOutcome(
            rule,
            "not_applicable",
            f"{plural(total, 'bug-fix commit')} in 90 days; a share needs "
            f"at least {CONCENTRATION_MIN_FIXES}.",
        )
    production = {p: f for p, f in facts.files.items() if not f.is_test}
    files_per_folder: dict[str, int] = defaultdict(int)
    for path in production:
        for folder in _ancestors(path):
            files_per_folder[folder] += 1
    shas_per_folder: dict[str, set[str]] = defaultdict(set)
    fixed_files: dict[str, list[str]] = defaultdict(list)
    for path, shas in facts.fix_shas_by_file.items():
        f = production.get(path)
        if f is None:
            continue
        for folder in _ancestors(path):
            shas_per_folder[folder] |= shas
            if f.fix_commits_90d >= 3:
                fixed_files[folder].append(path)
    n_prod = len(production)
    named = fix_first_paths(facts)

    def lift(folder: str) -> float:
        return (len(shas_per_folder[folder]) / total) / (files_per_folder[folder] / n_prod)

    candidates = [
        folder
        for folder, shas in shas_per_folder.items()
        if len(shas) / total >= CONCENTRATION_MIN_SHARE
        # Files Fix first names do not make a folder an area of work twice.
        and len([p for p in fixed_files[folder] if p not in named])
        >= CONCENTRATION_MIN_REPEAT_FILES
        and lift(folder) >= CONCENTRATION_MIN_LIFT
    ]
    chosen: list[str] = []
    for folder in sorted(candidates, key=lambda d: -lift(d)):
        if any(folder.startswith(c + "/") or c.startswith(folder + "/") for c in chosen):
            continue
        chosen.append(folder)
    actions = []
    for folder in chosen[:CONCENTRATION_MAX_ACTIONS]:
        n = len(shas_per_folder[folder])
        share = round(100 * n / total)
        members = sorted(
            fixed_files[folder], key=lambda p: -production[p].fix_commits_90d
        )
        includes = tuple(
            p for p in members if is_fragile(production[p], ctx) and p not in named
        )
        actions.append(
            Action(
                rule=rule,
                tier="plan",
                horizons=("quarter",),
                severity="high",
                title=(
                    f"Stabilise {code(folder + '/')}: {share}% of the bug fixes land in "
                    f"its {plural(files_per_folder[folder], 'file')}"
                ),
                impact=(
                    f"{n} of the {total} bug-fix commits in the last 90 days touched "
                    f"this folder, which holds {files_per_folder[folder] / n_prod:.1%} "
                    "of the production files."
                ),
                why=(
                    WhyFact("share of bug-fix commits", f"{share}%"),
                    WhyFact("files with repeated fixes", str(len(members))),
                    WhyFact("most fixed", posixpath.basename(members[0])),
                ),
                target_kind="folder",
                target_path=folder,
                surface="findings",
                effort="L",
                confidence="medium",
                done_when="Its share of bug-fix commits falls over the next 90 days.",
                weight=float(n),
                evidence_total=len(members),
                includes=includes,
                fingerprint=fingerprint(share // 5),
                details=tuple(
                    ActionDetail(
                        path=p,
                        reason=(
                            f"{production[p].fix_commits_90d} bug-fix commits, "
                            f"{production[p].commits_90d} commits in 90 days"
                        ),
                    )
                    for p in members
                ),
                commands=(
                    ActionCommand.call(
                        "Bug-fix history and co-change partners for the most fixed files",
                        "get_risk",
                        {"targets": members[:5]},
                        cli="repowise risk " + " ".join(f"-t {p}" for p in members[:5]),
                    ),
                    ActionCommand.call(
                        "Why the folder is shaped this way",
                        "get_why",
                        {"targets": [f"{folder}/"]},
                        cli=f"repowise why {folder}/",
                    ),
                ),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


#: Fix-first tiers that become actions; ``later`` stays on the Code Health page.
_FIX_FIRST_TIER = {"now": "act_now", "next": "plan"}


def fix_first_paths(facts: RepoFacts) -> set[str]:
    """Files the ``fix_first`` rule emits, so file-level rules do not repeat them."""
    return {i.target.file_path for i in facts.fix_first if i.tier in _FIX_FIRST_TIER}


def fix_first(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    """The head of the shared Fix-first queue: one ranking for every surface.

    Code health, refactoring and performance work all reach this list through
    it, so Do next and the Code Health lead cannot disagree about what to fix.
    """
    rule = "fix_first"
    if "fix_first" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["fix_first"])
    actions = []
    for item in facts.fix_first:
        tier = _FIX_FIRST_TIER.get(item.tier)
        if tier is None:
            continue
        target = item.target
        symbol = target.symbol
        actions.append(
            Action(
                rule=rule,
                tier=tier,
                horizons=("week", "quarter") if tier == "act_now" else ("quarter",),
                severity="high" if tier == "act_now" else "medium",
                title=item.title,
                impact=item.why,
                why=(
                    WhyFact("gain", item.gain.text, "inferred"),
                    *(WhyFact(f.label, f.value, f.basis) for f in item.facts[:3]),
                ),
                target_kind="symbol" if symbol else "file",
                target_path=target.file_path,
                target_symbol=symbol,
                identity=item.id,
                surface="performance" if item.kind == "perf_fix" else "findings",
                effort=_effort(item.effort.bucket),
                confidence="high" if item.confidence.level == "high" else "medium",
                done_when="It leaves Fix first on the next update.",
                weight=float(len(facts.fix_first) - item.rank),
                evidence_ids=tuple(
                    x for x in (item.source.opportunity_id, *item.source.finding_ids) if x
                ),
                fingerprint=fingerprint(item.tier, item.gain.text),
                details=tuple(
                    ActionDetail(path=step.file_path, line=step.line, reason=step.text)
                    for step in item.action.steps
                ),
                details_total=item.action.steps_total,
                commands=(
                    ActionCommand.call(
                        "The full item: steps, tests to run, risk",
                        "get_health",
                        {"fix_id": item.id},
                        cli="repowise health",
                    ),
                ),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


def _effort(bucket: str | None) -> str:
    if bucket == "S":
        return "S"
    if bucket in ("L", "XL"):
        return "L"
    return "M"
