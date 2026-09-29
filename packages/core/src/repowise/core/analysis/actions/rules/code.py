"""Rules about the code itself: what got worse, what keeps breaking, what is slow."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from datetime import datetime

from ..context import RepoContext
from ..facts import FileFacts, RepoFacts
from ..model import Action, ActionCommand, ActionDetail, RuleOutcome, WhyFact, fingerprint
from ._text import code, humanize, plural, py_list

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
        ActionCommand(
            "Every open finding in these files, with its line and reason",
            mcp=f'get_health(targets={py_list(paths[:10])}, include=["biomarkers"])',
            cli=f"repowise health --file {first}",
        ),
        ActionCommand(
            "The commit that last made the first file worse, and what it put at risk",
            mcp=f'get_change_risk(revspec="{sha}")',
            cli=f"git show {sha[:12]} -- {first}",
        ),
        ActionCommand(
            "A file's structure and callers before editing it",
            mcp=f'get_context(targets=["{first}"], include=["skeleton", "callers"])',
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


def _is_fragile(f: FileFacts, ctx: RepoContext) -> bool:
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
    if ctx.fix_commits_90d == 0:
        return RuleOutcome(rule, "not_applicable", "No bug-fix commits in the last 90 days.")
    fragile = sorted(
        (f for f in facts.files.values() if _is_fragile(f, ctx)),
        key=lambda f: -(f.fix_commits_90d * f.commits_90d),
    )
    touched = [f for f in fragile if ctx.in_week(f.last_commit_at)]
    week_rows = {f.path for f in touched[:FRAGILE_WEEK_ROWS]}
    actions = []
    for f in fragile:
        cov = f.line_coverage_pct
        if cov is None:
            title = f"Add tests around {code(f.path)} before its next change"
            done = "A coverage report includes it at 80% or more."
            variant = "unknown"
        elif cov < COVERED_PCT:
            title = f"Raise test coverage on {code(f.path)} from {cov:.0f}%"
            done = "Line coverage on this file reaches 80%."
            variant = "raise"
        elif f.lead is not None:
            where = code(f.lead.function) + " in " if f.lead.function else ""
            title = f"Simplify {where}{code(f.path)}: it is tested, and fixes keep landing"
            done = "Its lead finding closes and bug-fix commits slow down."
            variant = "simplify"
        else:
            continue
        why = [
            WhyFact("commits in 90 days", str(f.commits_90d)),
            WhyFact("bug-fix commits", str(f.fix_commits_90d)),
            _coverage_fact(f, facts),
        ]
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
                target_symbol=f.lead.function if variant == "simplify" and f.lead else None,
                identity=f.path,
                surface="file",
                effort="M" if (f.nloc or 0) < 600 else "L",
                confidence="high" if variant != "unknown" else "medium",
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
                    ActionCommand(
                        "Its bug-fix history, co-change partners and test gaps",
                        mcp=f'get_risk(targets=["{f.path}"])',
                        cli=f"repowise risk -t {f.path}",
                    ),
                    ActionCommand(
                        "Every open finding in the file",
                        mcp=f'get_health(targets=["{f.path}"], include=["biomarkers"])',
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

    def lift(folder: str) -> float:
        return (len(shas_per_folder[folder]) / total) / (files_per_folder[folder] / n_prod)

    candidates = [
        folder
        for folder, shas in shas_per_folder.items()
        if len(shas) / total >= CONCENTRATION_MIN_SHARE
        and len(fixed_files[folder]) >= CONCENTRATION_MIN_REPEAT_FILES
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
        includes = tuple(p for p in members if _is_fragile(production[p], ctx))
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
                    ActionCommand(
                        "Bug-fix history and co-change partners for the most fixed files",
                        mcp=f"get_risk(targets={py_list(members[:5])})",
                        cli="repowise risk " + " ".join(f"-t {p}" for p in members[:5]),
                    ),
                    ActionCommand(
                        "Why the folder is shaped this way",
                        mcp=f'get_why(targets=["{folder}/"])',
                        cli=f"repowise why {folder}/",
                    ),
                ),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


_BOUNDARY_NOUN = {
    "db": "database",
    "filesystem": "file system",
    "network": "network",
    "subprocess": "subprocess",
}


def hot_path_perf(facts: RepoFacts, ctx: RepoContext) -> RuleOutcome:
    """Repeated I/O an entry point can reach, whose cost grows with the data.

    Strict on purpose: on the repowise index 4 of 491 production opportunities
    clear it. The rest stay on the Performance tab as an inventory.
    """
    rule = "hot_path_perf"
    if "performance" in facts.unavailable:
        return RuleOutcome(rule, "unavailable", facts.unavailable["performance"])
    actions = []
    for p in facts.perf:
        if p.actionability not in ("plan_ready", "advisory"):
            continue
        if p.exposure != "entry_reachable":
            continue
        if p.loop_magnitude != "grows_with_data" and p.call_sites < 3:
            continue
        where = code(p.symbol.rsplit("::", 1)[-1] if p.symbol else p.file_path)
        noun = _BOUNDARY_NOUN.get(p.boundary or "")
        if noun and p.call_sites > 1:
            title = f"Batch the {noun} calls loops make through {where}"
        elif noun:
            title = f"Move the {noun} call in {where} out of its loop"
        else:
            title = f"Fix the {humanize(p.biomarker)} in {where}"
        actions.append(
            Action(
                rule=rule,
                tier="plan",
                horizons=("quarter",),
                severity="medium",
                title=title,
                impact=(
                    f"{plural(p.call_sites, 'loop')} across {plural(p.files, 'file')} "
                    f"{'reaches' if p.call_sites == 1 else 'reach'} it from code an entry "
                    "point calls, once per item."
                ),
                why=(
                    WhyFact("file", posixpath.basename(p.file_path)),
                    WhyFact("call sites", str(p.call_sites)),
                    WhyFact("reachable from an entry point", "Yes", "inferred"),
                    WhyFact(
                        "cost",
                        "Grows with the data"
                        if p.loop_magnitude == "grows_with_data"
                        else "Repeated per call site",
                        "inferred",
                    ),
                ),
                target_kind="symbol" if p.symbol else "file",
                target_path=p.file_path,
                target_symbol=p.symbol,
                identity=p.opportunity_id,
                surface="performance",
                effort=_effort(p.effort),
                confidence="high" if p.actionability == "plan_ready" else "medium",
                done_when="The opportunity closes on the next update.",
                marker=p.biomarker,
                weight=float(p.call_sites),
                evidence_ids=(p.opportunity_id,),
                evidence_total=p.call_sites,
                commands=(
                    ActionCommand(
                        "Every call site, the fix strategy and the tests to run",
                        mcp=f'get_health(opportunity_id="{p.opportunity_id}")',
                        cli=f"repowise health --file {p.file_path}",
                    ),
                    ActionCommand(
                        "The function's body and its callers",
                        mcp=f'get_symbol("{p.symbol or p.file_path}", depth=1)',
                        cli=f"repowise symbol {p.symbol or p.file_path}",
                    ),
                ),
                fingerprint=fingerprint(p.call_sites, p.actionability),
            )
        )
    return RuleOutcome(rule, "evaluated", "", tuple(actions))


def _effort(bucket: str | None) -> str:
    if bucket == "S":
        return "S"
    if bucket in ("L", "XL"):
        return "L"
    return "M"
