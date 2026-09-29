"""Builders for the Stats ("By the Numbers") page, free of any database.

Every builder takes plain mappings (a dict, or a SQLAlchemy ``RowMapping``) and
returns the JSON-ready section described by ``packages/types/src/stats.ts``.
The OSS route feeds them SQL rows; the hosted backend feeds them artifact
dicts. Keeping the derivations here is what lets both surfaces publish the same
figures without a second copy of the rules.

Scope is defined by subtraction: only signals no other page already owns.
Health scores live on Code Health, commit volume on Commits, per-person
ownership on Contributors, dependencies on Architecture, co-change pairs on
Coupling.

Timestamps may arrive as aware or naive datetimes (SQLite drops tzinfo) or as
ISO strings (artifacts); :func:`parse_dt` normalises all three to aware UTC.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from repowise.core.author_identity import build_identity_resolver
from repowise.core.test_paths import is_test_related_path

Row = Mapping[str, Any]

# ---------------------------------------------------------------------------
# Size class: a playful, NLOC-driven label for "how big is this codebase".
# ---------------------------------------------------------------------------

_SIZE_CLASSES: tuple[tuple[int, str, str], ...] = (
    (1_000, "Seedling", "A fresh sprout, small enough to hold in your head."),
    (5_000, "Hamlet", "A cozy codebase you could read in an afternoon."),
    (20_000, "Village", "A tidy village. A few neighborhoods, easy to walk."),
    (60_000, "Town", "A proper town with its own districts and main streets."),
    (150_000, "City", "A real city: busy, layered, plenty going on."),
    (500_000, "Metropolis", "A sprawling metropolis with serious infrastructure."),
)
_MEGALOPOLIS = ("Megalopolis", "A vast megalopolis, its own self-contained world.")

# Share of dated commits that must carry a UTC offset before the punch card and
# chronotypes switch from UTC to author-local time. Below it the page stays
# all-UTC instead of mixing two clocks in one matrix.
_LOCAL_TIME_COVERAGE = 0.99

# A peak hour drawn from fewer commits than this is noise, not a habit.
_CHRONOTYPE_MIN_COMMITS = 10

# Automation, excluded from the people-shaped sections. Either an explicit bot
# marker or a service name matched in full, so "Netlify Johnson" stays a person.
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

_FUNCTION_KINDS = frozenset({"function", "method"})


def is_bot(name: str | None, email: str | None) -> bool:
    """True when this author is automation (CI or a coding agent's own identity).

    Identity only: an agent-*assisted* commit still has a human author.
    """
    # Imported here: the provenance module loads the whole git indexer package.
    from repowise.core.ingestion.git_indexer.agent_provenance import agent_from_identity

    if agent_from_identity(name, email):
        return True
    if name and _BOT_NAME_RE.search(name):
        return True
    return bool(email and _BOT_EMAIL_RE.search(email))


def size_class(total_nloc: int) -> dict[str, Any]:
    for ceiling, name, blurb in _SIZE_CLASSES:
        if total_nloc < ceiling:
            return {"name": name, "blurb": blurb, "nloc": total_nloc}
    name, blurb = _MEGALOPOLIS
    return {"name": name, "blurb": blurb, "nloc": total_nloc}


def parse_dt(raw: Any) -> datetime | None:
    """An aware UTC datetime from a datetime (naive read as UTC) or ISO string."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _int(raw: Any) -> int:
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _offset_minutes(raw: Any) -> int | None:
    # bool is an int subclass; a stray False must not read as a zero offset.
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_external(node: Row) -> bool:
    node_id = str(node.get("node_id") or node.get("id") or "")
    return (
        node.get("language") == "external"
        or node_id.startswith("external:")
        or node.get("external_system_id") is not None
    )


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


def build_scale(file_nodes: Iterable[Row], metrics: Iterable[Row]) -> dict[str, Any]:
    """Counts, NLOC and the language mix.

    ``file_nodes`` are graph file nodes; external dependency nodes are dropped
    so the file count describes the repo. Only code languages are counted:
    JSON, YAML and Markdown are formats a developer would not list as the
    languages a project is written in.
    """
    from repowise.core.ingestion.languages.registry import REGISTRY

    code = REGISTRY.code_languages()
    file_count = 0
    symbol_count = 0
    langs: Counter[str] = Counter()
    for node in file_nodes:
        if _is_external(node):
            continue
        file_count += 1
        symbol_count += _int(node.get("symbol_count"))
        lang = node.get("language")
        if lang in code:
            langs[lang] += 1

    total_nloc = 0
    test_nloc = 0
    modules: set[str] = set()
    for m in metrics:
        nloc = _int(m.get("nloc"))
        total_nloc += nloc
        if m.get("is_test"):
            test_nloc += nloc
        if m.get("module"):
            modules.add(m["module"])

    languages = [{"language": k, "file_count": v} for k, v in langs.most_common()]
    return {
        "file_count": file_count,
        "symbol_count": symbol_count,
        "module_count": len(modules),
        "total_nloc": total_nloc,
        "test_nloc": test_nloc,
        "language_count": len(languages),
        "languages": languages,
        "size_class": size_class(total_nloc),
    }


# ---------------------------------------------------------------------------
# Commits: origin, rhythm and people, from one pass
# ---------------------------------------------------------------------------


def _punch_card_summary(
    punch: list[list[int]], dated_total: int, *, timezone_mode: str
) -> dict[str, Any]:
    """The weekday x hour matrix plus its hottest cell and marginal peaks."""
    peak = {"weekday": 0, "hour": 0, "count": 0}
    for wd in range(7):
        for hr in range(24):
            if punch[wd][hr] > peak["count"]:
                peak = {"weekday": wd, "hour": hr, "count": punch[wd][hr]}
    weekday_totals = [sum(punch[wd]) for wd in range(7)]
    hour_totals = [sum(punch[wd][hr] for wd in range(7)) for hr in range(24)]
    return {
        "matrix": punch,
        "peak": peak if peak["count"] > 0 else None,
        "busiest_weekday": max(range(7), key=lambda wd: weekday_totals[wd]) if dated_total else None,
        "peak_hour": max(range(24), key=lambda hr: hour_totals[hr]) if dated_total else None,
        "total": dated_total,
        "timezone_mode": timezone_mode,
    }


def _commit_velocity(
    commit_times: list[datetime], last_at: datetime | None, window_start: datetime | None
) -> dict[str, Any] | None:
    """The 90 days ending at the newest commit against the 90 before.

    ``window_start`` is the oldest commit the sample holds when the sample is
    not the whole history. A 90-day window reaching past it would count only
    part of its commits, so the figure is withheld (None) or, for the prior
    window alone, the comparison is.
    """
    if last_at is None or not commit_times:
        return None
    recent_cut = last_at - timedelta(days=90)
    prior_cut = last_at - timedelta(days=180)
    if window_start is not None and window_start > recent_cut:
        return None
    recent = sum(1 for t in commit_times if t > recent_cut)
    prior = sum(1 for t in commit_times if prior_cut < t <= recent_cut)
    prior_covered = window_start is None or window_start <= prior_cut
    pct_change = round((recent - prior) / prior * 100.0, 1) if prior and prior_covered else None
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


def _longest_silence(commit_times: list[datetime]) -> dict[str, Any] | None:
    """The longest stretch between two consecutive commits."""
    if len(commit_times) < 2:
        return None
    start, end = max(pairwise(sorted(commit_times)), key=lambda p: p[1] - p[0])
    hours = int((end - start).total_seconds() // 3600)
    return {"hours": hours, "start": _iso(start), "end": _iso(end)}


def _chronotypes(
    per_author_hours: dict[str, list[int]],
    per_author_weekdays: dict[str, list[int]],
    names: dict[str, str],
) -> list[dict[str, Any]]:
    """Each frequent contributor's peak commit hour, with a habit label.

    Both marginal histograms ship because the UI names people from them, and
    which days count as the weekend is the reader's preference.
    """
    out: list[dict[str, Any]] = []
    for key, hours in per_author_hours.items():
        total = sum(hours)
        if total < _CHRONOTYPE_MIN_COMMITS:
            continue
        peak_hour = max(range(24), key=lambda h: hours[h])
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


def _keep_top(bucket: list[dict[str, Any]], item: dict[str, Any], key: str) -> None:
    bucket.append(item)
    bucket.sort(key=lambda c: -c[key])
    del bucket[3:]


class _Awards:
    """Top three commits by churn, breadth and net deletion.

    Three survive dropping the root commit, which wins every size award on an
    initial import.
    """

    def __init__(self) -> None:
        self.churn: list[dict[str, Any]] = []
        self.wide: list[dict[str, Any]] = []
        self.purge: list[dict[str, Any]] = []

    def add(self, row: Row) -> None:
        base = {"sha": row.get("sha"), "subject": row.get("subject") or ""}
        added, deleted = _int(row.get("lines_added")), _int(row.get("lines_deleted"))
        files = _int(row.get("files_changed"))
        if added + deleted > 0:
            item = {**base, "lines_changed": added + deleted, "files_changed": files}
            _keep_top(self.churn, item, "lines_changed")
        if files > 0:
            item = {**base, "files_changed": files, "lines_changed": added + deleted}
            _keep_top(self.wide, item, "files_changed")
        if deleted > added:
            item = {**base, "lines_deleted": deleted, "lines_added": added, "net": deleted - added}
            _keep_top(self.purge, item, "net")

    def winners(self, root_sha: str | None) -> dict[str, Any]:
        def pick(bucket: list[dict[str, Any]]) -> dict[str, Any] | None:
            return next((c for c in bucket if c["sha"] != root_sha), None)

        purge = pick(self.purge)
        return {
            "biggest_commit": pick(self.churn),
            "widest_commit": pick(self.wide),
            "biggest_purge": {k: v for k, v in purge.items() if k != "net"} if purge else None,
        }


def _calendar(
    stamps: list[tuple[datetime, int | None, str | None]], local_mode: bool, humans: set[str]
) -> dict[str, Any]:
    """Punch card, day and month counts and per-person histograms, in one clock."""
    punch = [[0] * 24 for _ in range(7)]
    days: Counter[Any] = Counter()
    months: Counter[str] = Counter()
    hours: dict[str, list[int]] = {}
    weekdays: dict[str, list[int]] = {}
    for moment, offset, key in stamps:
        local = moment + timedelta(minutes=offset or 0) if local_mode else moment
        punch[local.weekday()][local.hour] += 1
        days[local.date()] += 1
        months[local.strftime("%Y-%m")] += 1
        if local_mode and key in humans:
            hours.setdefault(key, [0] * 24)[local.hour] += 1
            weekdays.setdefault(key, [0] * 7)[local.weekday()] += 1
    return {"punch": punch, "days": days, "months": months, "hours": hours, "weekdays": weekdays}


def _sample_complete(
    first_at: datetime | None, true_first: datetime | None, total: int, true_total: Any
) -> bool:
    """Whether the commit sample reaches the root commit.

    Decided by date where the root is known: the sample walk skips merge
    commits and the whole-history count does not, so the counts never match on
    a repo that merges.
    """
    if true_first is not None:
        return first_at is not None and first_at <= true_first + timedelta(seconds=60)
    return true_total is None or total >= int(true_total)


def _most_common(counter: Counter[Any]) -> tuple[Any, int] | None:
    return counter.most_common(1)[0] if counter else None


def build_commit_pass(commits: Iterable[Row], repo_totals: Row | None = None) -> dict[str, Any]:
    """Origin, rhythm, people and commit awards from one walk over the commits.

    ``commits`` is the indexed commit sample, which is bounded to the newest N
    commits. Headline totals prefer the whole-history values in
    ``repo_totals`` (issue #730), and ``rhythm.window`` says how much of the
    history everything else was drawn from, so the page can name its scope.
    """
    totals = repo_totals or {}
    rows = list(commits)
    resolve = build_identity_resolver([(r.get("author_name"), r.get("author_email")) for r in rows])

    contributors: set[str] = set()
    display_name: dict[str, str] = {}
    humans: set[str] = set()
    arrival: dict[str, datetime] = {}
    stamps: list[tuple[datetime, int | None, str | None]] = []
    first: tuple[datetime, str | None] | None = None
    awards = _Awards()

    for row in rows:
        name, email = row.get("author_name"), row.get("author_email")
        key = resolve(name, email) if (name or email) else None
        if key:
            contributors.add(key)
            display_name.setdefault(key, name or key)
            if not is_bot(name, email):
                humans.add(key)
        awards.add(row)
        moment = parse_dt(row.get("committed_at"))
        if moment is None:
            continue
        if first is None or moment < first[0]:
            first = (moment, row.get("sha"))
        stamps.append((moment, _offset_minutes(row.get("committed_offset_minutes")), key))
        if key and (key not in arrival or moment < arrival[key]):
            arrival[key] = moment

    commit_times = [s[0] for s in stamps]
    first_at = first[0] if first else None
    complete = _sample_complete(
        first_at, parse_dt(totals.get("first_commit_at")), len(rows), totals.get("total_commit_count")
    )
    local_mode = bool(stamps) and (
        sum(1 for s in stamps if s[1] is not None) / len(stamps) >= _LOCAL_TIME_COVERAGE
    )
    cal = _calendar(stamps, local_mode, humans)
    rhythm = _rhythm(cal, commit_times, local_mode, complete, first_at, len(rows))
    arrivals = sorted(
        (
            {"name": display_name.get(k, k), "first_commit_at": _iso(v)}
            for k, v in arrival.items()
            if k in humans
        ),
        key=lambda a: a["first_commit_at"] or "",
    )
    return {
        "origin": _origin(totals, first_at, max(commit_times, default=None), len(rows), len(contributors)),
        "rhythm": rhythm,
        "chronotypes": (
            _chronotypes(cal["hours"], cal["weekdays"], display_name) if local_mode else []
        ),
        "arrivals": arrivals,
        # The oldest sampled commit is the root only when the sample is
        # complete; otherwise it is an ordinary commit that may rightly win.
        **awards.winners(first[1] if first and complete else None),
    }


def _origin(
    totals: Row, first_at: datetime | None, last_at: datetime | None, sampled: int, people: int
) -> dict[str, Any]:
    """Founding facts, preferring whole-history totals over the sample."""
    first = parse_dt(totals.get("first_commit_at")) or first_at
    true_total = totals.get("total_commit_count")
    true_people = totals.get("total_contributor_count")
    return {
        "first_commit_at": _iso(first),
        "first_commit_author": totals.get("first_commit_author"),
        "first_commit_subject": totals.get("first_commit_subject"),
        "last_commit_at": _iso(last_at),
        "age_days": (last_at - first).days if (first and last_at) else None,
        "total_commits": int(true_total) if true_total is not None else sampled,
        "contributor_count": int(true_people) if true_people is not None else people,
    }


def _rhythm(
    cal: dict[str, Any],
    commit_times: list[datetime],
    local_mode: bool,
    complete: bool,
    first_at: datetime | None,
    sampled: int,
) -> dict[str, Any]:
    """The time-shaped section, with the window it was drawn from."""
    last_at = max(commit_times, default=None)
    day = _most_common(cal["days"])
    month = _most_common(cal["months"])
    return {
        "window": {
            "commits": sampled,
            "first_at": _iso(first_at),
            "last_at": _iso(last_at),
            "complete": complete,
        },
        "punch_card": _punch_card_summary(
            cal["punch"], len(commit_times), timezone_mode="author_local" if local_mode else "utc"
        ),
        "velocity": _commit_velocity(commit_times, last_at, None if complete else first_at),
        "busiest_month": {"month": month[0], "total": month[1]} if month else None,
        "busiest_day": {"date": day[0].isoformat(), "commits": day[1]} if day else None,
        "longest_streak": _longest_streak(set(cal["days"])),
        "longest_silence": _longest_silence(commit_times),
        "active_days": len(cal["days"]),
    }


def build_churn(repo_totals: Row | None) -> dict[str, Any] | None:
    """Lifetime lines written vs. taken back, from whole-history repo totals.

    Never summed from the bounded commit sample; None when the totals are absent.
    """
    totals = repo_totals or {}
    added, deleted = totals.get("total_lines_added"), totals.get("total_lines_deleted")
    if added is None or deleted is None or int(added) <= 0:
        return None
    added, deleted = int(added), int(deleted)
    return {
        "lines_added": added,
        "lines_deleted": deleted,
        "net": added - deleted,
        "deleted_per_hundred": round(deleted / added * 100.0, 1),
    }


def code_half_life(git_metadata: Iterable[Row], last_at: Any) -> int | None:
    """Median days since each file was last touched, anchored to the newest commit."""
    anchor = parse_dt(last_at)
    if anchor is None:
        return None
    ages = sorted(
        (anchor - touched).days
        for m in git_metadata
        if (touched := parse_dt(m.get("last_commit_at"))) is not None and touched <= anchor
    )
    if not ages:
        return None
    mid = len(ages) // 2
    return ages[mid] if len(ages) % 2 else (ages[mid - 1] + ages[mid]) // 2


# ---------------------------------------------------------------------------
# People: repo-level concentration only
# ---------------------------------------------------------------------------


def build_people(git_metadata: Iterable[Row]) -> dict[str, Any]:
    """Owner count, single-owner files, directory silos and the truck factor."""
    from repowise.core.analysis.health.aggregation import module_label

    owners: Counter[str] = Counter()
    single_owner_files = 0
    module_owner_files: dict[str, Counter[str]] = {}
    module_file_totals: Counter[str] = Counter()

    for m in git_metadata:
        owner = m.get("primary_owner_name")
        if _int(m.get("bus_factor")) == 1:
            single_owner_files += 1
        module = module_label(m.get("file_path"))
        module_file_totals[module] += 1
        if owner:
            owners[owner] += 1
            module_owner_files.setdefault(module, Counter())[owner] += 1

    silo_count = sum(
        1
        for module, mowners in module_owner_files.items()
        if max(mowners.values()) / module_file_totals[module] > 0.8
    )

    # Fewest primary owners who together hold more than half the owned files.
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
        "tracked_files": sum(module_file_totals.values()),
        "single_owner_files": single_owner_files,
        "silo_count": silo_count,
        "truck_factor": truck_factor,
    }


# ---------------------------------------------------------------------------
# Records: the superlatives
# ---------------------------------------------------------------------------


def build_file_records(
    metrics: Iterable[Row], git_metadata: Iterable[Row], first_commit_at: Any
) -> dict[str, Any]:
    """Largest, gnarliest and most-changed file, and what survives from day one.

    "Oldest file" is replaced by a count: every file added in the root commit
    ties for it, and naming one of a hundred ties is arbitrary. Files whose
    history hit the per-file commit cap are left out of both sides, since the
    cap can cut their root commit off.
    """
    out: dict[str, Any] = {}
    metrics = list(metrics)
    largest = max(metrics, key=lambda m: _int(m.get("nloc")), default=None)
    if largest is not None and _int(largest.get("nloc")) > 0:
        out["largest_file"] = {"path": largest["file_path"], "nloc": _int(largest["nloc"])}
    gnarliest = max(metrics, key=lambda m: _int(m.get("max_ccn")), default=None)
    if gnarliest is not None and _int(gnarliest.get("max_ccn")) > 0:
        out["gnarliest_file"] = {
            "path": gnarliest["file_path"],
            "max_ccn": _int(gnarliest["max_ccn"]),
        }

    root = parse_dt(first_commit_at)
    most_changed: Row | None = None
    day_one = 0
    dated = 0
    for m in git_metadata:
        if most_changed is None or _int(m.get("commit_count_total")) > _int(
            most_changed.get("commit_count_total")
        ):
            most_changed = m
        born = parse_dt(m.get("first_commit_at"))
        if born is None or m.get("commit_count_capped"):
            continue
        dated += 1
        if root is not None and abs((born - root).total_seconds()) < 60:
            day_one += 1
    if most_changed is not None and _int(most_changed.get("commit_count_total")) > 0:
        out["most_changed_file"] = {
            "path": most_changed["file_path"],
            "commit_count": _int(most_changed["commit_count_total"]),
        }
    if day_one:
        out["day_one_files"] = {"count": day_one, "of": dated}
    return out


def most_imported_file(candidates: Iterable[Row]) -> dict[str, Any] | None:
    """The highest fan-in file that is real project source.

    ``candidates`` carry ``path``, ``in_degree`` and ``pagerank``, best first.
    The stored ``is_test`` flag can be stale, so the anchored path rules
    re-check each one (#1103).
    """
    for c in candidates:
        path = str(c.get("path") or "")
        if not path or path.startswith("external:") or is_test_related_path(path):
            continue
        if _int(c.get("in_degree")) <= 0:
            return None
        return {
            "path": path,
            "pagerank": round(float(c.get("pagerank") or 0.0), 4),
            "import_count": _int(c.get("in_degree")),
        }
    return None


def is_test_checker(metrics: Iterable[Row]) -> Callable[[str], bool]:
    """Whether a path is test code, for the records that skip it.

    Health's stored ``is_test`` answers for the files it scored. It is NULL on
    rows written before the column, and absent for unscored files; those fall
    back to the shared path rules, cached per path.
    """
    flags = {m["file_path"]: m.get("is_test") for m in metrics}
    cache: dict[str, bool] = {}

    def check(path: str) -> bool:
        flag = flags.get(path)
        if flag is not None:
            return bool(flag)
        if path not in cache:
            cache[path] = is_test_related_path(path)
        return cache[path]

    return check


def symbol_shape(total: int, async_count: int, documented: int) -> dict[str, Any] | None:
    """What share of the symbols is async, and what share carries a docstring."""
    if total <= 0:
        return None
    return {
        "total": total,
        "async_count": async_count,
        "async_pct": round(async_count / total * 100.0, 1),
        "documented_count": documented,
        "documented_pct": round(documented / total * 100.0, 1),
    }


def build_function_records(
    functions: Iterable[Row], is_test: Callable[[str], bool]
) -> dict[str, Any]:
    """Longest function, longest name and the most reused name.

    ``functions`` are function and method symbols. Test code is skipped: test
    names are sentences by convention and would win every time.
    """
    out: dict[str, Any] = {}
    longest: tuple[int, Row] | None = None
    longest_name: Row | None = None
    names: Counter[str] = Counter()

    for s in functions:
        name = s.get("name") or ""
        if not name or s.get("kind") not in _FUNCTION_KINDS or is_test(s.get("file_path") or ""):
            continue
        if not name.startswith("__"):
            names[name] += 1
        if longest_name is None or len(name) > len(longest_name["name"]):
            longest_name = s
        lines = _int(s.get("end_line")) - _int(s.get("start_line")) + 1
        if lines > 1 and (longest is None or lines > longest[0]):
            longest = (lines, s)

    if longest is not None:
        lines, s = longest
        out["longest_function"] = {"name": s["name"], "file_path": s["file_path"], "lines": lines}
    if longest_name is not None:
        out["longest_name"] = {
            "name": longest_name["name"],
            "file_path": longest_name["file_path"],
        }
    if names:
        name, count = names.most_common(1)[0]
        if count > 1:
            out["most_common_name"] = {"name": name, "count": count}
    return out


def most_patched_function(
    blame: Iterable[Row], is_test: Callable[[str], bool]
) -> dict[str, Any] | None:
    """The non-test function whose current lines the most commits own.

    ``mod_count`` is distinct commits across the function's surviving blame, so
    a wholesale rewrite scores low and a function patched in many places high.
    """
    best: Row | None = None
    for row in blame:
        if _int(row.get("mod_count")) <= _int((best or {}).get("mod_count")):
            continue
        if not row.get("function_name") or is_test(row.get("file_path") or ""):
            continue
        best = row
    if best is None:
        return None
    return {
        "name": best.get("function_name") or "",
        "file_path": best.get("file_path") or "",
        "mod_count": _int(best.get("mod_count")),
    }
