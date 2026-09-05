"""/api/repos/{repo_id}/stats/highlights — the "By the Numbers" payload.

A single read-only aggregate powering the repo Stats page. Its scope is defined
by subtraction: **only signals no other page in the app already shows**. Health
scores live on Code Health, commit volume and categories on Commits, per-person
ownership on Contributors, dependencies and communities on Architecture. What is
left, and what this endpoint serves, is the repo's identity (scale, origin,
lifetime churn), its rhythm (time-of-day, streaks, how fast code goes cold), and
its records — none of which have a home anywhere else.

Everything here reads rows the indexer already wrote; nothing recomputes
analysis. The three source tables are each pulled once, as narrow column
selects rather than whole ORM rows, and every derived figure rides one of those
three passes. Sections build defensively: a missing table degrades that section
to ``None`` / empty rather than 500-ing the page.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.co_change import parse_partners
from repowise.core.ingestion.git_indexer import build_identity_resolver
from repowise.core.ingestion.git_indexer.agent_provenance import agent_from_identity
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    GitCommit,
    GitMetadata,
    GraphMetric,
    GraphNode,
    GraphNodeMembership,
    HealthFileMetric,
    WikiSymbol,
)
from repowise.core.test_paths import is_test_related_path
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.services.module_health import top_level_module

router = APIRouter(
    prefix="/api/repos",
    tags=["stats"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Size class — a playful, NLOC-driven label for "how big is this codebase".
# Thresholds are on non-comment lines of code (the health-metric NLOC sum),
# the most honest single proxy for scale across languages.
# ---------------------------------------------------------------------------

_SIZE_CLASSES: tuple[tuple[int, str, str], ...] = (
    (1_000, "Seedling", "A fresh sprout — small enough to hold in your head."),
    (5_000, "Hamlet", "A cozy codebase you could read in an afternoon."),
    (20_000, "Village", "A tidy village — a few neighborhoods, easy to walk."),
    (60_000, "Town", "A proper town with its own districts and main streets."),
    (150_000, "City", "A real city — busy, layered, plenty going on."),
    (500_000, "Metropolis", "A sprawling metropolis with serious infrastructure."),
)
_MEGALOPOLIS = ("Megalopolis", "A vast megalopolis — its own self-contained world.")

# Share of dated commits that must carry a UTC offset before the punch card and
# the chronotype awards switch from UTC to author-local time. Below it the page
# stays honestly all-UTC rather than mixing two clocks in one matrix — a chart
# where some cells are local and some are not is worse than one that is
# uniformly shifted. An index written before the offset column existed starts at
# 0% and fills on `repowise update` (the backfill in
# ``pipeline.incremental.reconcile_commit_offsets``), so this flips on its own.
# The backfill is capped per run, so a very deep window may need a few updates.
_LOCAL_TIME_COVERAGE = 0.99

# Minimum commits before someone is eligible for a chronotype award — a peak
# hour drawn from three commits is noise, not a habit.
_CHRONOTYPE_MIN_COMMITS = 10

# Automation, excluded from the people-shaped sections. A bot's "peak hour" is
# a cron schedule and its "arrival" is the day someone enabled it — both are
# facts about configuration, not about anyone's working habits, and leaving
# dependabot in a night-owl leaderboard makes the whole list read as noise.
# Matched on the author identity rather than on `agent_name`, which marks
# agent-*assisted* commits a human still authored.
# Either an explicit bot marker, or a service name matched in FULL. Matching a
# leading token would read "Netlify Johnson" as automation, and dropping a real
# person off the page is far worse than keeping one stray bot on it.
_BOT_NAME_RE = re.compile(
    r"(\[bot\]"
    r"|^bot$"
    r"|[-_ ]bot$"
    r"|^(dependabot|renovate(bot)?|greenkeeper|snyk([-_ ]bot)?|imgbot|"
    r"github[-_ ]?actions|semantic[-_ ]release|allcontributors|codecov|mergify|"
    r"pre[-_ ]commit[-_ ]ci|netlify|vercel)$)",
    re.IGNORECASE,
)
_BOT_EMAIL_RE = re.compile(
    r"(\[bot\]@|@bots\.noreply\.github\.com|^(actions@github\.com|"
    r"noreply@github\.com)$)",
    re.IGNORECASE,
)


def _is_bot(name: str | None, email: str | None) -> bool:
    """True when this author is automation rather than a person.

    Two sources. Coding agents come from the ingestion layer's provenance
    registry, so "is this Claude / Cursor / Codex?" stays answered in exactly
    one place and the stats page inherits every vendor identity the indexer
    already knows. The regexes above cover the rest of CI automation, which
    provenance has no reason to model.

    Identity only: an agent-*assisted* commit still has a human author, and
    that person belongs in these lists.
    """
    if agent_from_identity(name, email):
        return True
    if name and _BOT_NAME_RE.search(name):
        return True
    return bool(email and _BOT_EMAIL_RE.search(email))


def _size_class(total_nloc: int) -> dict[str, Any]:
    for ceiling, name, blurb in _SIZE_CLASSES:
        if total_nloc < ceiling:
            return {"name": name, "blurb": blurb, "nloc": total_nloc}
    name, blurb = _MEGALOPOLIS
    return {"name": name, "blurb": blurb, "nloc": total_nloc}


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _as_utc(dt: datetime | None) -> datetime | None:
    """Reinterpret a naive datetime as UTC.

    The ``committed_at`` columns are declared ``DateTime(timezone=True)``, but
    SQLite does not persist tzinfo, so a value read back is naive even though it
    was written aware. Anything doing arithmetic or calendar work on it has to
    restore the tz first or it silently mixes aware and naive values (and
    ``.hour`` quietly answers for the wrong clock). Same guard the change-risk
    tool applies for the same reason.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Scale — what this codebase is, dimensionally.
# ---------------------------------------------------------------------------


async def _scale(session: AsyncSession, repo_id: str, metrics: list[Any]) -> dict[str, Any]:
    """Graph + NLOC scale signals + the size-class label."""
    file_count = (
        await session.scalar(
            select(func.count(GraphNode.id)).where(
                GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
            )
        )
        or 0
    )
    symbol_count = int(
        await session.scalar(
            select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
        )
        or 0
    )
    total_nloc = sum(int(m.nloc or 0) for m in metrics)

    lang_rows = await session.execute(
        select(GraphNode.language, func.count(GraphNode.id))
        .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
        .group_by(GraphNode.language)
    )
    languages = sorted(
        ({"language": lang or "other", "file_count": n} for lang, n in lang_rows),
        key=lambda r: -r["file_count"],
    )
    module_count = len({m.module for m in metrics if m.module})

    return {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "module_count": module_count,
        "total_nloc": total_nloc,
        "language_count": len(languages),
        "languages": languages,
        "size_class": _size_class(total_nloc),
    }


# ---------------------------------------------------------------------------
# Rhythm — the time-shape of the work. Nothing else in the app has a clock.
# ---------------------------------------------------------------------------


def _punch_card_summary(
    punch: list[list[int]], dated_total: int, *, timezone_mode: str
) -> dict[str, Any]:
    """Fold the weekday x hour commit matrix into a renderable summary.

    ``matrix`` is 7 rows (0=Monday) x 24 hours in *timezone_mode*'s clock. Also
    names the single hottest cell and the busiest weekday / peak hour (by
    marginal totals). The weekend share is deliberately not computed here: which
    days are the weekend is a reader preference, and the matrix already carries
    every weekday total.
    """
    peak = {"weekday": 0, "hour": 0, "count": 0}
    for wd in range(7):
        for hr in range(24):
            if punch[wd][hr] > peak["count"]:
                peak = {"weekday": wd, "hour": hr, "count": punch[wd][hr]}

    weekday_totals = [sum(punch[wd]) for wd in range(7)]
    hour_totals = [sum(punch[wd][hr] for wd in range(7)) for hr in range(24)]
    busiest_weekday = max(range(7), key=lambda wd: weekday_totals[wd]) if dated_total else None
    peak_hour = max(range(24), key=lambda hr: hour_totals[hr]) if dated_total else None

    return {
        "matrix": punch,
        "peak": peak if peak["count"] > 0 else None,
        "busiest_weekday": busiest_weekday,
        "peak_hour": peak_hour,
        "total": dated_total,
        "timezone_mode": timezone_mode,
    }


def _commit_velocity(commit_times: list[Any], last_at: Any) -> dict[str, Any]:
    """Recent-vs-prior commit momentum, anchored to the newest commit.

    Anchoring to ``last_at`` (not wall-clock now) keeps the signal meaningful on
    an index that hasn't been synced today: it compares the 90 days ending at
    the latest commit against the 90 before that. ``pct_change`` is None when the
    prior window is empty (a young repo), so the UI can omit a divide-by-zero
    arrow rather than show a fake spike.
    """
    if last_at is None or not commit_times:
        return {"recent_90d": 0, "prior_90d": 0, "pct_change": None}
    recent_cut = last_at - timedelta(days=90)
    prior_cut = last_at - timedelta(days=180)
    recent = sum(1 for t in commit_times if t > recent_cut)
    prior = sum(1 for t in commit_times if prior_cut < t <= recent_cut)
    pct_change = round((recent - prior) / prior * 100.0, 1) if prior else None
    return {"recent_90d": recent, "prior_90d": prior, "pct_change": pct_change}


def _longest_streak(commit_days: set[Any]) -> dict[str, Any] | None:
    """Longest run of consecutive calendar days with at least one commit."""
    if not commit_days:
        return None
    days_sorted = sorted(commit_days)
    best_len = run_len = 1
    best_end = days_sorted[0]
    for prev, cur in pairwise(days_sorted):
        run_len = run_len + 1 if (cur - prev).days == 1 else 1
        if run_len > best_len:
            best_len, best_end = run_len, cur
    if best_len < 2:
        return None
    return {
        "days": best_len,
        "start": (best_end - timedelta(days=best_len - 1)).isoformat(),
        "end": best_end.isoformat(),
    }


def _chronotypes(
    per_author_hours: dict[str, list[int]],
    per_author_weekdays: dict[str, list[int]],
    names: dict[str, str],
) -> list[dict[str, Any]]:
    """Each frequent contributor's peak commit hour, with a habit label.

    Only meaningful once commits carry their author's UTC offset — the caller
    skips this entirely in UTC mode rather than award "night owl" to whoever
    happens to live furthest east.

    Both marginal histograms ship alongside the label, because the UI names
    people from them and weekday naming has to happen there: which days count
    as the weekend is a reader preference, so a "weekend warrior" decided here
    would be wrong for every team that rests Friday. The joint weekday-by-hour
    distribution is deliberately not sent. It is 168 numbers per person to
    sharpen a single label, and the two marginals answer the same question
    closely enough.
    """
    out: list[dict[str, Any]] = []
    for key, hours in per_author_hours.items():
        total = sum(hours)
        if total < _CHRONOTYPE_MIN_COMMITS:
            continue
        peak_hour = max(range(24), key=lambda h: hours[h])
        # Off-hours share doubles as the tiebreaker for the label: a peak at 9am
        # with a third of commits after midnight is still a night owl.
        night = sum(hours[h] for h in (22, 23, 0, 1, 2, 3, 4))
        early = sum(hours[h] for h in (5, 6, 7, 8))
        if peak_hour >= 22 or peak_hour <= 4:
            label = "night_owl"
        elif 5 <= peak_hour <= 8:
            label = "early_bird"
        elif night / total >= 0.25:
            label = "night_owl"
        else:
            label = "daylight"
        out.append(
            {
                "name": names.get(key, key),
                "commits": total,
                "peak_hour": peak_hour,
                "label": label,
                "night_pct": round(night / total * 100.0, 1),
                "early_pct": round(early / total * 100.0, 1),
                "hour_commits": hours,
                "weekday_commits": per_author_weekdays.get(key, [0] * 7),
            }
        )
    return sorted(out, key=lambda a: -a["commits"])[:8]


async def _commit_pass(session: AsyncSession, repo_id: str, repo: Any) -> dict[str, Any]:
    """Single scan of ``git_commits`` producing origin, rhythm and people signals.

    One query, one loop. Everything time-shaped the page shows — the punch card,
    streaks, the busiest day, the widest commit, momentum, chronotypes, arrival
    dates — falls out of this pass, because each of them needs per-commit rows
    and a second walk would double the cost for no new data.

    Headline totals (commit count, project age, contributor count, founder)
    prefer the whole-history values stamped on the ``Repository`` row at index
    time: ``git_commits`` is bounded to the newest N commits, so deriving them
    from it undercounts a long-lived repo badly (issue #730).
    """
    rows = (
        await session.execute(
            select(
                GitCommit.committed_at,
                GitCommit.committed_offset_minutes,
                GitCommit.author_name,
                GitCommit.author_email,
                GitCommit.sha,
                GitCommit.subject,
                GitCommit.lines_added,
                GitCommit.lines_deleted,
                GitCommit.files_changed,
            ).where(GitCommit.repository_id == repo_id)
        )
    ).all()

    # Fold GitHub noreply variants and same-name real+noreply emails to one
    # identity so the same person isn't counted as several contributors.
    resolve = build_identity_resolver([(name, email) for _, _, name, email, *_ in rows])

    total = 0
    dated = 0
    with_offset = 0
    contributors: set[str] = set()
    display_name: dict[str, str] = {}
    human_keys: set[str] = set()
    arrival: dict[str, datetime] = {}
    first_at: datetime | None = None
    first_sha: str | None = None
    last_at: datetime | None = None
    commit_times: list[datetime] = []
    # Normalised (utc, offset) pairs kept for the second, timezone-aware pass:
    # whether the punch card is drawn in UTC or author-local can't be decided
    # until every row has been seen, so the calendar work waits for the verdict.
    stamps: list[tuple[datetime, int | None, str | None]] = []
    # Top-3 by churn and by breadth. The repo's very first commit is excluded
    # from both awards (an initial import would always win), so a couple of
    # spare candidates are enough to survive dropping it.
    top_churn: list[dict[str, Any]] = []
    top_wide: list[dict[str, Any]] = []

    for (
        committed_at,
        offset,
        author_name,
        author_email,
        sha,
        subject,
        added,
        deleted,
        files,
    ) in rows:
        total += 1
        key = None
        if author_email or author_name:
            key = resolve(author_name, author_email)
            if key:
                contributors.add(key)
                display_name.setdefault(key, author_name or key)
                if not _is_bot(author_name, author_email):
                    human_keys.add(key)

        churn = int(added or 0) + int(deleted or 0)
        if churn > 0:
            top_churn.append(
                {
                    "sha": sha,
                    "subject": subject or "",
                    "lines_changed": churn,
                    "files_changed": int(files or 0),
                }
            )
            top_churn.sort(key=lambda c: -c["lines_changed"])
            del top_churn[3:]
        if int(files or 0) > 0:
            top_wide.append(
                {
                    "sha": sha,
                    "subject": subject or "",
                    "files_changed": int(files or 0),
                    "lines_changed": churn,
                }
            )
            top_wide.sort(key=lambda c: -c["files_changed"])
            del top_wide[3:]

        moment = _as_utc(committed_at)
        if moment is None:
            continue
        dated += 1
        if offset is not None:
            with_offset += 1
        if first_at is None or moment < first_at:
            first_at, first_sha = moment, sha
        if last_at is None or moment > last_at:
            last_at = moment
        commit_times.append(moment)
        stamps.append((moment, offset, key))
        if key and (key not in arrival or moment < arrival[key]):
            arrival[key] = moment

    # Author-local only once effectively every row can be shifted; see
    # _LOCAL_TIME_COVERAGE. Mixed clocks would make the matrix a lie.
    local_mode = bool(dated) and (with_offset / dated) >= _LOCAL_TIME_COVERAGE
    timezone_mode = "author_local" if local_mode else "utc"

    punch = [[0] * 24 for _ in range(7)]
    commit_days: set[Any] = set()
    day_counts: dict[Any, int] = {}
    per_author_hours: dict[str, list[int]] = {}
    per_author_weekdays: dict[str, list[int]] = {}
    for moment, offset, key in stamps:
        local = moment + timedelta(minutes=offset or 0) if local_mode else moment
        punch[local.weekday()][local.hour] += 1
        day = local.date()
        commit_days.add(day)
        day_counts[day] = day_counts.get(day, 0) + 1
        if local_mode and key and key in human_keys:
            per_author_hours.setdefault(key, [0] * 24)[local.hour] += 1
            per_author_weekdays.setdefault(key, [0] * 7)[local.weekday()] += 1

    busiest_day = None
    if day_counts:
        day, count = max(day_counts.items(), key=lambda kv: kv[1])
        busiest_day = {"date": day.isoformat(), "commits": count}

    months: dict[str, int] = {}
    for moment, offset, _ in stamps:
        local = moment + timedelta(minutes=offset or 0) if local_mode else moment
        month = local.strftime("%Y-%m")
        months[month] = months.get(month, 0) + 1
    busiest_month = None
    if months:
        month, count = max(months.items(), key=lambda kv: kv[1])
        busiest_month = {"month": month, "total": count}

    biggest_commit = next((c for c in top_churn if c["sha"] != first_sha), None)
    widest_commit = next((c for c in top_wide if c["sha"] != first_sha), None)

    # Prefer the whole-history values stamped on the repo at index time; fall
    # back to the bounded sample when they're absent (older index, non-git
    # repo). Age runs from the true first commit to the latest commit we have.
    true_first = _as_utc(getattr(repo, "first_commit_at", None))
    effective_first = true_first if true_first is not None else first_at
    effective_total = getattr(repo, "total_commit_count", None)
    effective_contributors = getattr(repo, "total_contributor_count", None)

    arrivals = sorted(
        (
            {"name": display_name.get(k, k), "first_commit_at": _iso(v)}
            for k, v in arrival.items()
            if k in human_keys
        ),
        key=lambda a: a["first_commit_at"] or "",
    )

    return {
        "origin": {
            "first_commit_at": _iso(effective_first),
            "first_commit_author": getattr(repo, "first_commit_author", None),
            "first_commit_subject": getattr(repo, "first_commit_subject", None),
            "last_commit_at": _iso(last_at),
            "age_days": (
                (last_at - effective_first).days if (effective_first and last_at) else None
            ),
            "total_commits": effective_total if effective_total is not None else total,
            "contributor_count": (
                effective_contributors if effective_contributors is not None else len(contributors)
            ),
        },
        "rhythm": {
            "punch_card": _punch_card_summary(punch, dated, timezone_mode=timezone_mode),
            "velocity": _commit_velocity(commit_times, last_at),
            "busiest_month": busiest_month,
            "busiest_day": busiest_day,
            "longest_streak": _longest_streak(commit_days),
            "active_days": len(commit_days),
        },
        "chronotypes": (
            _chronotypes(per_author_hours, per_author_weekdays, display_name) if local_mode else []
        ),
        "arrivals": arrivals,
        "biggest_commit": biggest_commit,
        "widest_commit": widest_commit,
    }


def _churn_ledger(repo: Any) -> dict[str, Any] | None:
    """Lifetime lines written vs. taken back, from the repo-level totals.

    Deliberately *not* summed from ``git_commits``: that table is bounded to the
    newest N commits, so on a repo with deeper history the sum would be a
    windowed figure presented as a lifetime one. ``None`` when the indexer
    skipped the walk (history too deep) or predates the capture, so the page
    omits the ledger rather than showing a number it can't stand behind.
    """
    added = getattr(repo, "total_lines_added", None)
    deleted = getattr(repo, "total_lines_deleted", None)
    if added is None or deleted is None or added <= 0:
        return None
    return {
        "lines_added": int(added),
        "lines_deleted": int(deleted),
        "net": int(added) - int(deleted),
        "deleted_per_hundred": round(int(deleted) / int(added) * 100.0, 1),
    }


def _code_half_life(all_meta: list[Any], last_at: str | None) -> int | None:
    """Median days since each file was last touched.

    "Half this codebase hasn't been changed in N days." Anchored to the newest
    commit rather than wall-clock now, so a stale index doesn't inflate it.
    """
    anchor = _as_utc(datetime.fromisoformat(last_at)) if last_at else None
    if anchor is None:
        return None
    ages = sorted(
        (anchor - touched).days
        for m in all_meta
        if (touched := _as_utc(m.last_commit_at)) is not None and touched <= anchor
    )
    if not ages:
        return None
    mid = len(ages) // 2
    return ages[mid] if len(ages) % 2 else (ages[mid - 1] + ages[mid]) // 2


# ---------------------------------------------------------------------------
# People — repo-level concentration only. Per-person detail is the Contributors
# page's job, and duplicating it here would just be a worse version of it.
# ---------------------------------------------------------------------------


def _people(all_meta: list[Any]) -> dict[str, Any]:
    """Ownership concentration: owner count, single-owner files, module silos."""
    owners: dict[str, int] = {}
    single_owner_files = 0
    module_owner_files: dict[str, dict[str, int]] = {}
    module_file_totals: dict[str, int] = {}

    for m in all_meta:
        if m.primary_owner_name:
            owners[m.primary_owner_name] = owners.get(m.primary_owner_name, 0) + 1
        if (m.bus_factor or 0) == 1:
            single_owner_files += 1
        module = top_level_module(m.file_path)
        module_file_totals[module] = module_file_totals.get(module, 0) + 1
        if m.primary_owner_name:
            bucket = module_owner_files.setdefault(module, {})
            bucket[m.primary_owner_name] = bucket.get(m.primary_owner_name, 0) + 1

    silo_count = 0
    for module, mowners in module_owner_files.items():
        top = max(mowners.values(), default=0)
        if module_file_totals.get(module) and top / module_file_totals[module] > 0.8:
            silo_count += 1

    # Truck factor: the fewest primary owners who together hold >50% of owned
    # files — "how many people could walk out before the bus problem bites".
    # A factor of 1 means a single person owns most of the codebase.
    owned_total = sum(owners.values())
    truck_factor: int | None = None
    if owned_total:
        cumulative = 0
        truck_factor = 0
        for count in sorted(owners.values(), reverse=True):
            cumulative += count
            truck_factor += 1
            if cumulative * 2 > owned_total:
                break

    return {
        "owner_count": len(owners),
        "single_owner_files": single_owner_files,
        "silo_count": silo_count,
        "truck_factor": truck_factor,
    }


# ---------------------------------------------------------------------------
# Records — the superlatives. Several of these are the only headline treatment
# their underlying signal gets anywhere in the app (max CCN is otherwise a
# sortable column; the cycle count has no UI at all).
# ---------------------------------------------------------------------------


async def _records(
    session: AsyncSession, repo_id: str, metrics: list[Any], all_meta: list[Any]
) -> dict[str, Any]:
    """The "biggest / oldest / gnarliest / most" awards, one row each."""
    out: dict[str, Any] = {}

    largest = max(metrics, key=lambda m: m.nloc or 0, default=None)
    if largest is not None and (largest.nloc or 0) > 0:
        out["largest_file"] = {"path": largest.file_path, "nloc": int(largest.nloc)}

    # Gnarliest file by cyclomatic complexity. Code Health exposes max_ccn only
    # as a sortable table column, so this is the one place it gets named.
    gnarliest = max(metrics, key=lambda m: m.max_ccn or 0, default=None)
    if gnarliest is not None and (gnarliest.max_ccn or 0) > 0:
        out["gnarliest_file"] = {"path": gnarliest.file_path, "max_ccn": int(gnarliest.max_ccn)}

    sym = (
        await session.execute(
            select(WikiSymbol.name, WikiSymbol.file_path, WikiSymbol.complexity_estimate)
            .where(WikiSymbol.repository_id == repo_id)
            .order_by(WikiSymbol.complexity_estimate.desc())
            .limit(1)
        )
    ).first()
    if sym is not None and (sym[2] or 0) > 0:
        out["most_complex_symbol"] = {
            "name": sym[0],
            "file_path": sym[1],
            "complexity": int(sym[2]),
        }

    most_changed = max(all_meta, key=lambda m: m.commit_count_total or 0, default=None)
    if most_changed is not None and (most_changed.commit_count_total or 0) > 0:
        out["most_changed_file"] = {
            "path": most_changed.file_path,
            "commit_count": int(most_changed.commit_count_total),
        }
    dated = [m for m in all_meta if m.first_commit_at is not None]
    if dated:
        oldest = min(dated, key=lambda m: _as_utc(m.first_commit_at))
        out["oldest_file"] = {
            "path": oldest.file_path,
            "first_commit_at": _iso(_as_utc(oldest.first_commit_at)),
        }

    # Most imported file — highest fan-in among non-test file nodes, the
    # legible version of "most central". External and test nodes are excluded
    # so the award names real project source, and the top candidates are then
    # re-checked against the shared test-path rules.
    #
    # The re-check is not redundant with the SQL filter. `is_test` is *stored*,
    # stamped when the file was last traversed, so an index written before the
    # current rules can carry a stale answer — and the file this award is most
    # likely to hand to is `tests/conftest.py`, which tops fan-in in any repo
    # that shares fixtures widely (713 imports in this one, more than double the
    # runner-up). The rules themselves now come from one place rather than the
    # unanchored regex this used to hold, which read `src/latest/api.py` and
    # `protest/main.py` as tests and passed the award to the runner-up (#1103).
    #
    # Falls back to the PageRank pick when graph metrics were not materialized.
    candidates = (
        await session.execute(
            select(GraphMetric.node_id, GraphMetric.in_degree, GraphMetric.pagerank)
            .join(
                GraphNode,
                (GraphNode.repository_id == GraphMetric.repository_id)
                & (GraphNode.node_id == GraphMetric.node_id),
            )
            .where(
                GraphMetric.repository_id == repo_id,
                GraphNode.node_type == "file",
                GraphNode.is_test.is_(False),
                GraphNode.external_system_id.is_(None),
                ~GraphNode.node_id.like("external:%"),
            )
            .order_by(GraphMetric.in_degree.desc())
            .limit(10)
        )
    ).all()
    imported = next((c for c in candidates if not is_test_related_path(c[0])), None)
    if imported is not None and (imported[1] or 0) > 0:
        out["most_central_file"] = {
            "path": imported[0],
            "pagerank": round(float(imported[2] or 0.0), 4),
            "import_count": int(imported[1]),
        }
    else:
        central = (
            await session.execute(
                select(GraphNode.node_id, GraphNode.pagerank)
                .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
                .order_by(GraphNode.pagerank.desc())
                .limit(1)
            )
        ).first()
        if central is not None and (central[1] or 0) > 0:
            out["most_central_file"] = {
                "path": central[0],
                "pagerank": round(float(central[1]), 4),
            }

    # The pair with the most shared commits. Counted, not weighted: the card
    # renders it as "changed together N times".
    best_pair: dict[str, Any] | None = None
    for m in all_meta:
        for p in parse_partners(m.co_change_partners_json):
            if best_pair is None or p.support > best_pair["count"]:
                best_pair = {"a": m.file_path, "b": p.file_path, "count": p.support}
    if best_pair and best_pair["count"] > 0:
        out["strongest_coupling"] = best_pair

    # Largest import cycle — strongly-connected components with more than one
    # member. One aggregate over the materialized membership rows (no graph
    # rebuild). Nothing else in the app surfaces cycles at all.
    scc_rows = (
        await session.execute(
            select(func.count())
            .where(
                GraphNodeMembership.repository_id == repo_id,
                GraphNodeMembership.scc_size > 1,
            )
            .group_by(GraphNodeMembership.scc_id)
        )
    ).all()
    if scc_rows:
        out["largest_cycle"] = {
            "files": max(int(n) for (n,) in scc_rows),
            "cycle_count": len(scc_rows),
        }

    # Symbol-shape shares: what fraction of this codebase is async, and what
    # fraction carries a docstring. One aggregate row, three counters.
    shape = (
        await session.execute(
            select(
                func.count(WikiSymbol.id),
                func.sum(func.cast(WikiSymbol.is_async, Integer)),
                func.count(WikiSymbol.docstring),
            ).where(WikiSymbol.repository_id == repo_id)
        )
    ).one_or_none()
    if shape and shape[0]:
        total_symbols = int(shape[0])
        async_count = int(shape[1] or 0)
        documented = int(shape[2] or 0)
        out["symbol_shape"] = {
            "total": total_symbols,
            "async_count": async_count,
            "async_pct": round(async_count / total_symbols * 100.0, 1),
            "documented_count": documented,
            "documented_pct": round(documented / total_symbols * 100.0, 1),
        }

    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/{repo_id}/stats/highlights")
async def stats_highlights(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Everything the Stats ("By the Numbers") page needs, in one call.

    Three narrow column selects (health metrics, git metadata, commits) plus a
    handful of aggregates. Deliberately no health summary, findings, dead-code,
    dependency, decision or cost reads: every one of those belongs to a page
    that already owns the subject, and pulling them here cost the page its two
    slowest queries for numbers shown better elsewhere.
    """
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Narrow selects, not whole ORM rows: the page needs four health columns and
    # seven git-metadata ones out of the ~20 each table carries, and the unread
    # remainder includes the large JSON blobs.
    metrics = (
        await session.execute(
            select(
                HealthFileMetric.file_path,
                HealthFileMetric.nloc,
                HealthFileMetric.module,
                HealthFileMetric.max_ccn,
            ).where(HealthFileMetric.repository_id == repo_id)
        )
    ).all()
    all_meta = (
        await session.execute(
            select(
                GitMetadata.file_path,
                GitMetadata.primary_owner_name,
                GitMetadata.bus_factor,
                GitMetadata.commit_count_total,
                GitMetadata.first_commit_at,
                GitMetadata.last_commit_at,
                GitMetadata.co_change_partners_json,
            ).where(GitMetadata.repository_id == repo_id)
        )
    ).all()

    commits = await _commit_pass(session, repo_id, repo)
    records = await _records(session, repo_id, metrics, all_meta)
    # Computed on the commit scan for cost, but they are awards, so the payload
    # files them under records.
    for key in ("biggest_commit", "widest_commit"):
        value = commits.pop(key, None)
        if value:
            records[key] = value

    rhythm = commits["rhythm"]
    rhythm["code_half_life_days"] = _code_half_life(all_meta, commits["origin"]["last_commit_at"])

    people = _people(all_meta)
    people["contributor_count"] = commits["origin"]["contributor_count"]
    people["chronotypes"] = commits.pop("chronotypes", [])
    people["arrivals"] = commits.pop("arrivals", [])

    return {
        "repo": {"id": repo.id, "name": repo.name},
        "scale": await _scale(session, repo_id, metrics),
        "origin": commits["origin"],
        "churn": _churn_ledger(repo),
        "rhythm": rhythm,
        "people": people,
        "records": records,
    }
