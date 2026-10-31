"""Health, churn hotspots and bug magnets, one row per repository of the estate."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import (
    Section,
    WorkspaceReportContext,
    capped,
    capped_names,
    check,
    report_sections,
)
from repowise.core.report.sections._estate import git_history, unread
from repowise.core.workspace.seams import MIN_HISTORY_COMMITS

_HEALTH = (
    "each repo's newest health snapshot as the workspace store (.repowise-workspace/"
    "workspace.db) holds it: the average file health on the 1-10 scale 'repowise health' uses"
)
_CHURN = (
    "files each index's git history flags as hotspots: the top quarter by recent commit "
    "churn that also clears the activity floors. Churn only; it says nothing about defects"
)
_MAGNETS = (
    "files each index's git history flags as bug magnets: bug-fix commits in the trailing "
    "defect window (about six months), recent fixes weighted most, past the three-fix trigger"
)


def _alias(entry: dict[str, Any]) -> str:
    return entry.get("alias") or entry.get("name")


def _health(ctx: WorkspaceReportContext) -> dict[str, Any]:
    stored = ctx.repo_health()
    if stored is None:
        return check(
            "health",
            reason=(
                "the workspace store is missing or unreadable; 'repowise update --workspace' "
                "writes it"
            ),
            method=_HEALTH,
            basis="unresolved",
        )
    rows, not_indexed, absent, none = [], [], [], []
    for entry in ctx.repos:
        alias = _alias(entry)
        if not entry.get("indexed", True):
            not_indexed.append(alias)
            continue
        row = stored.get(alias)
        if row is None:
            absent.append(alias)
            continue
        if not row["health"] or row["health"].get("average_health") is None:
            none.append(alias)
            continue
        h = row["health"]
        rows.append(
            {
                "repo": alias,
                "average_health": h["average_health"],
                "maintainability_average": h.get("maintainability_average"),
                "hotspot_health": h.get("hotspot_health"),
                "taken_at": h.get("taken_at"),
                "store_commit": row["indexed_commit"],
                "index_commit": entry.get("head_commit"),
            }
        )
    rows.sort(key=lambda r: (r["average_health"], r["repo"]))
    lines = []
    for r in rows:
        line = f"{r['repo']}: {r['average_health']:g} average file health"
        if r["hotspot_health"] is not None:
            line += f", {r['hotspot_health']:g} over its churn hotspots"
        line += f" (snapshot {(r['taken_at'] or 'undated')[:10]}"
        stale = r["store_commit"] and r["index_commit"] and r["store_commit"] != r["index_commit"]
        line += f"; stored at {r['store_commit'][:12]}, index now at {r['index_commit'][:12]})" if stale else ")"
        lines.append(line)
    details = capped(lines)
    if none:
        details.append(f"indexed with no health snapshot: {capped_names(none)}")
    if absent:
        details.append(
            f"not in the workspace store yet ('repowise update --workspace' adds them): "
            f"{capped_names(absent)}"
        )
    if not_indexed:
        details.append(f"not indexed: {capped_names(not_indexed)}")
    if rows:
        low, high = rows[0], rows[-1]
        reason = (
            f"{len(rows)} of {len(ctx.repos)} repo(s) carry a health snapshot; average file "
            f"health runs from {low['average_health']:g} ({low['repo']}) to "
            f"{high['average_health']:g} ({high['repo']}) out of 10"
        )
    else:
        reason = f"none of {len(ctx.repos)} repo(s) carries a stored health snapshot"
    return check(
        "health",
        reason=reason,
        details=details,
        method=_HEALTH + "; repos are not averaged together, each reads on its own",
        basis="measured" if rows else "unresolved",
        data={"repositories": rows},
    )


def _split(ctx: WorkspaceReportContext, history: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """``(alias -> facts with enough history, notes on the rest)``."""
    measured: dict[str, Any] = {}
    short, missing = [], []
    for entry in ctx.repos:
        alias = _alias(entry)
        facts = history.get(alias)
        if facts is None or isinstance(facts, Exception):
            if entry.get("indexed", True) and facts is None:
                missing.append(alias)
            continue
        if facts["commits"] < MIN_HISTORY_COMMITS:
            short.append(f"{alias} ({facts['commits']})")
        else:
            measured[alias] = facts
    notes = []
    if short:
        notes.append(
            f"too little history to measure (commits indexed): {capped_names(short)}"
        )
    if missing:
        notes.append(f"no git facts in the index: {capped_names(missing)}")
    if failed := unread(history):
        notes.append(f"index could not be read: {capped_names(failed)}")
    not_indexed = [_alias(e) for e in ctx.repos if not e.get("indexed", True)]
    if not_indexed:
        notes.append(f"not indexed: {capped_names(not_indexed)}")
    return measured, notes


def _dated(aliases: list[str], facts: dict[str, Any]) -> list[str]:
    """``alias (newest commit <date>)``: a quiet repo may simply have stopped changing."""
    return [
        f"{a} (newest commit {facts[a]['newest_commit']})" if facts[a]["newest_commit"] else a
        for a in aliases
    ]


def _top(files: list[str], total: int) -> str:
    rest = total - len(files)
    return ", ".join(files) + (f" and {rest} more" if rest > 0 else "")


def _churn(ctx: WorkspaceReportContext, history: dict[str, Any]) -> dict[str, Any]:
    measured, notes = _split(ctx, history)
    hot = sorted(
        ((a, f) for a, f in measured.items() if f["hotspots"]), key=lambda kv: (-kv[1]["hotspots"], kv[0])
    )
    total = sum(f["hotspots"] for f in measured.values())
    lines = [
        f"{a}: {f['hotspots']} of {f['files']} code file(s), {f['commits']} commits indexed: "
        f"{_top(f['top_hotspots'], f['hotspots'])}"
        for a, f in hot
    ]
    quiet = sorted(a for a, f in measured.items() if not f["hotspots"])
    if quiet:
        lines.append(f"history read, no churn hotspot: {capped_names(_dated(quiet, measured))}")
    return check(
        "churn-hotspots",
        reason=(
            f"{total} churn hotspot file(s) across {len(hot)} of {len(measured)} repo(s) with "
            f"enough history; a hotspot here means frequent recent change, not a defect"
            if measured
            else "no repo holds enough indexed history to measure churn"
        ),
        details=[*capped(lines), *notes],
        method=_CHURN,
        basis="measured" if measured else "unresolved",
        data={"repositories": {a: _row(f, "hotspots") for a, f in sorted(measured.items())}},
    )


def _magnets(ctx: WorkspaceReportContext, history: dict[str, Any]) -> dict[str, Any]:
    measured, notes = _split(ctx, history)
    top = sorted(
        ((a, m) for a, f in measured.items() for m in f["top_bug_magnets"]),
        key=lambda am: (-am[1]["fixes"], am[0], am[1]["file"]),
    )
    total = sum(f["bug_magnets"] for f in measured.values())
    lines = [
        f"{a} {m['file']}: {m['fixes']} bug-fix commit(s) in the window"
        + (f", last {m['last_fix']}" if m["last_fix"] else "")
        for a, m in top
    ]
    clean = sorted(a for a, f in measured.items() if not f["files_with_fixes"])
    if clean:
        lines.append(
            f"history read, no bug-fix commit in the window: {capped_names(_dated(clean, measured))}"
        )
    with_magnets = sum(1 for f in measured.values() if f["bug_magnets"])
    return check(
        "bug-magnets",
        reason=(
            f"{total} bug magnet file(s) across {with_magnets} of {len(measured)} repo(s) with "
            f"enough history"
            if measured
            else "no repo holds enough indexed history to count bug-fix commits"
        ),
        details=[*capped(lines), *notes],
        method=_MAGNETS + "; at most five files listed per repo, each repo's total is in data",
        basis="measured" if measured else "unresolved",
        data={"repositories": {a: _row(f, "bug_magnets") for a, f in sorted(measured.items())}},
    )


def _row(facts: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "commits": facts["commits"],
        "newest_commit": facts["newest_commit"],
        "files": facts["files"],
        kind: facts[kind],
        f"top_{kind}": facts[f"top_{kind}"],
        **({"files_with_fixes": facts["files_with_fixes"]} if kind == "bug_magnets" else {}),
    }


async def collect(ctx: WorkspaceReportContext) -> list[dict[str, Any]]:
    history = await git_history(ctx)
    return [_health(ctx), _churn(ctx, history), _magnets(ctx, history)]


report_sections.register(
    "estate-health",
    Section(
        title="Health, churn and bug magnets by repository",
        summary="Each repo's stored health, its churn hotspots and its bug magnets, side by side.",
        collect=collect,
        order=40,
        report="estate",
        scope="workspace",
    ),
)
