"""Build :class:`RepoFacts` from plain rows, with no store behind them.

Every input is a sequence of rows, and a row is a mapping or any object with
the named attributes (a SQL row, an ORM object, a dict decoded from JSON). The
SQL loader in ``repowise.core.persistence.crud.analysis.actions`` narrows its
reads and hands the rows here; a caller holding the same rows in memory calls
:func:`build_repo_facts` directly. Each builder applies its store's whole rule,
so a caller may pass unfiltered rows.

Timestamps may be datetimes or ISO strings. They are compared with the anchor,
so keep them all naive or all aware.

Row shapes (the field names are the SQL columns):

``files``
    ``file_path``, ``commit_count_90d``, ``last_commit_at``, ``bug_magnet``,
    ``bus_factor``, ``primary_owner_name``, ``primary_owner_email``,
    ``primary_owner_commit_pct``, and from the file's health metric
    ``is_test``, ``score``, ``max_ccn``, ``nloc``, ``line_coverage_pct``.
``fix_events``
    ``file_path``, ``fix_sha``, ``committed_at``, ``shape_kind``, ``attribution``.
``health_findings``
    ``file_path``, ``biomarker_type``, ``severity``, ``function_name``,
    ``line_start``, ``reason``, ``health_impact``, ``status`` (absent = open);
    a lead also reads ``line_end`` and ``details_json``, which measure its function.
``authors``
    ``author_name``, ``author_email``, ``committed_at``: one row per commit, or
    per identity with its latest commit.
``commit_health``
    ``sha``, ``subject``, ``committed_at`` (the commit's), ``file_path``,
    ``symbol``, ``biomarker_type``, ``severity``, ``change_kind``,
    ``line_start``, ``reason``, ``attribution_basis``, ``dimension``.
``fix_first``
    One mapping of the Fix-first builder's own inputs (``metrics``,
    ``findings``, ``refactoring``, ``performance``, ``plans``); shapes in
    ``repowise.core.analysis.health.fix_first.build``. The recent findings
    also read ``performance`` rows' ``queue_eligible`` and the fields
    ``perf_confident_verdict`` reads.
``security``
    ``file_path``, ``kind``, ``line_number``, ``snippet``, ``severity``,
    ``commit_sha`` (empty for the working tree).
``doc_drift``
    ``file_path``, ``kind``, ``target``, ``raw``, ``line_number``, ``reason``,
    ``confidence``; ``known_paths`` is every path the repository's history held.
``dead_code``
    ``id``, ``file_path``, ``symbol_name``, ``lines``, ``kind``, ``confidence``,
    ``safe_to_delete``, ``status`` (absent = open); in a workspace, rows from
    ``serve_findings`` so the cross-repo cut applies. Readiness is re-derived
    (``dead_code.serving.is_safe``), never the stored flag alone.
``decisions``
    One mapping: ``stale_decisions`` (rows with ``id``, ``title``) and
    ``summary`` (``proposed``, ``active`` counts).
``coverage``
    One mapping: ``files_measured``, ``ingested_at``, ``ingested_commit_sha``,
    ``partial``.
``test_map``
    One mapping of path to ``total`` (test files that reach it in the code
    graph) and ``via`` (the walk, or ``mocked-only`` with a total of 0 when
    only tests replacing the file reach it), for :func:`reach_paths`; see
    ``repowise.core.analysis.test_reachability.any_tests_reaching``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from repowise.core.analysis.dead_code.risk_factors import REVIEW_ONLY_KINDS
from repowise.core.analysis.dead_code.serving import is_safe
from repowise.core.analysis.finding_registry import excluded_types, is_served
from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.models import lead_and_worth, split_by_origin
from repowise.core.analysis.health.queue.eligibility import perf_confident_verdict
from repowise.core.analysis.health.rows import field
from repowise.core.analysis.health.scoring import HISTORY_CATEGORY, biomarker_category
from repowise.core.author_identity import author_identity_key
from repowise.core.test_paths import is_test_related_path

from .context import WEEK, build_context
from .facts import (
    CoverageState,
    DeadFacts,
    DecisionFacts,
    DriftFacts,
    FileFacts,
    LeadFinding,
    RecentFinding,
    RepoFacts,
    SecretFacts,
)
from .rules.code import FIX_FIRST_ACTIONS, is_fragile
from .rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS

logger = logging.getLogger(__name__)

QUARTER = timedelta(days=90)

#: Attribution bases that tie a finding to lines the commit wrote. A
#: ``file_change`` basis only says the commit touched the file, which is not
#: enough to tell someone they made it worse.
AUTHORED_BASES = ("added_lines", "new_file", "changed_symbol")

#: Files whose lead finding is looked up: busy bug magnets only, so the lookup
#: stays a keyed read whatever the repository's size.
LEAD_LOOKUP_MIN_COMMITS = 5

#: Why a store with no input is not evaluated.
ABSENT = "Not in this index yet; run `repowise update`."

Rows = Iterable[Any]


def _when(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value if isinstance(value, datetime) else None


def _open(row: Any) -> bool:
    return (field(row, "status") or "open") == "open"


def _is_test(path: str | None, files: Mapping[str, FileFacts]) -> bool:
    """The stored test flag, or the shared classifier for a file the index lacks."""
    f = files.get(path or "")
    if f is not None:
        return f.is_test
    return bool(path) and is_test_related_path(path)


# --- files --------------------------------------------------------------------


def build_files(
    rows: Rows,
    *,
    since: datetime | None,
    fix_events: Rows = (),
    dependents: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Per-file facts, fix counts in the window from ``since``; no leads yet."""
    fix_shas: dict[str, set[str]] = defaultdict(set)
    all_fix: set[str] = set()
    if since is not None:
        for e in fix_events:
            at = _when(field(e, "committed_at"))
            if (
                at is not None
                and at >= since
                and field(e, "shape_kind") == "code_fix"
                and field(e, "attribution") != "none"
            ):
                fix_shas[field(e, "file_path")].add(field(e, "fix_sha"))
                all_fix.add(field(e, "fix_sha"))
    dependents = dependents or {}
    files: dict[str, FileFacts] = {}
    for r in rows:
        path = field(r, "file_path")
        name, email = field(r, "primary_owner_name"), field(r, "primary_owner_email")
        files[path] = FileFacts(
            path=path,
            is_test=bool(field(r, "is_test")),
            score=field(r, "score"),
            max_ccn=field(r, "max_ccn"),
            nloc=field(r, "nloc"),
            line_coverage_pct=field(r, "line_coverage_pct"),
            commits_90d=field(r, "commit_count_90d") or 0,
            last_commit_at=_when(field(r, "last_commit_at")),
            bug_magnet=bool(field(r, "bug_magnet")),
            fix_commits_90d=len(fix_shas.get(path, ())),
            bus_factor=field(r, "bus_factor"),
            owner_key=author_identity_key(name, email) if (name or email) else None,
            owner_name=name,
            owner_pct=field(r, "primary_owner_commit_pct"),
            dependents=dependents.get(path),
        )
    return {
        "files": files,
        "fix_shas_by_file": {p: frozenset(s) for p, s in fix_shas.items()},
        "fix_commits_90d": len(all_fix),
    }


def lead_paths(files: Mapping[str, FileFacts]) -> list[str]:
    """The files whose lead finding is worth a lookup."""
    return [
        p
        for p, f in files.items()
        if f.bug_magnet
        and not f.is_test
        and f.commits_90d >= LEAD_LOOKUP_MIN_COMMITS
        and f.fix_commits_90d >= 3
    ]


def with_leads(files: Mapping[str, FileFacts], findings: Rows) -> dict[str, FileFacts]:
    """``files`` with each :func:`lead_paths` file's lead code-shape finding.

    History markers are context, never the thing to do.
    """
    wanted = set(lead_paths(files))
    hidden = excluded_types()
    by_path: dict[str, list] = defaultdict(list)
    for f in findings:
        path = field(f, "file_path")
        biomarker = field(f, "biomarker_type")
        if path in wanted and _open(f) and is_served("health", biomarker, path, hidden):
            by_path[path].append(f)
    out = dict(files)
    for path, found in by_path.items():
        shape, _history = split_by_origin(found)
        lead, worth = lead_and_worth(shape)
        if lead is not None:
            out[path] = replace(
                out[path],
                lead=LeadFinding(
                    biomarker=field(lead, "biomarker_type"),
                    severity=field(lead, "severity"),
                    function=field(lead, "function_name"),
                    line=field(lead, "line_start"),
                    reason=field(lead, "reason") or "",
                    worth=worth,
                ),
            )
    return out


def reach_paths(files: Mapping[str, FileFacts]) -> list[str]:
    """The files whose test reach is worth a lookup: fragile, with no measured coverage.

    Only these can become "add tests", so the graph walk stays keyed to a few.
    The busy and fix bars depend on the files alone.
    """
    ctx = build_context(RepoFacts(anchor=None, files=files))
    return [
        p
        for p, f in files.items()
        if f.line_coverage_pct is None and is_fragile(f, ctx)
    ]


def with_test_reach(
    files: Mapping[str, FileFacts], reaching: Mapping[str, Any]
) -> dict[str, FileFacts]:
    """``files`` with each :func:`reach_paths` file's reaching test files.

    ``reaching`` maps a path to a row with ``total`` and ``via``; a looked-up
    path it does not name has none.
    """
    out = dict(files)
    for path in reach_paths(files):
        row = reaching.get(path)
        out[path] = replace(
            out[path],
            tests_reaching=int(field(row, "total") or 0) if row is not None else 0,
            tests_reaching_via=field(row, "via") if row is not None else None,
        )
    return out


# --- the other stores ---------------------------------------------------------


def build_authors(rows: Rows, *, since: datetime | None) -> dict[str, Any]:
    last: dict[str, datetime] = {}
    active: set[str] = set()
    for r in rows:
        latest = _when(field(r, "committed_at"))
        if latest is None:
            continue
        key = author_identity_key(field(r, "author_name"), field(r, "author_email"))
        if key not in last or latest > last[key]:
            last[key] = latest
        if since is not None and latest >= since:
            active.add(key)
    return {"author_last_commit": last, "active_authors_90d": len(active)}


#: Markers that read a measured coverage report, so "worsened" is coverage the
#: change lost. ``untested_hotspot`` is left out: without a report it fires on a
#: missing test file, which no commit's lines decide. ``untested_complexity``
#: reaches the serious severities only on measured coverage.
COVERAGE_MARKERS = frozenset({"coverage_gap", "coverage_gradient", "untested_complexity"})

#: Commit-health rows a loader reads: the authored kinds, at a severity that
#: counts, or a performance marker of any severity (its proof is the cause's
#: place in the performance queue, not the marker's label).
SERIOUS = ("critical", "high")


def _authored_regression(row: Any) -> bool:
    """The commit's own lines introduced or worsened the finding."""
    return (
        field(row, "change_kind") in ("introduced", "worsened")
        and field(row, "attribution_basis") in AUTHORED_BASES
    )


def recent_kind(row: Any, hidden: frozenset[str]) -> str | None:
    """What a commit-health row says the commit did: ``performance`` and
    ``coverage`` are defect-class, ``shape`` is a code-shape marker, and
    ``None`` is a history or hidden marker, or one below the bar."""
    biomarker = field(row, "biomarker_type")
    path = field(row, "file_path")
    # The row's own dimension, as the loader filters on it.
    if field(row, "dimension") == "performance":
        return "performance" if is_served("performance", biomarker, path, hidden) else None
    if field(row, "severity") not in SERIOUS or not is_served("health", biomarker, path, hidden):
        return None
    if biomarker in COVERAGE_MARKERS:
        return "coverage"
    return "shape" if biomarker_category(biomarker) != HISTORY_CATEGORY else None


def _recent_candidates(
    rows: Rows, *, week: datetime | None, files: Mapping[str, FileFacts]
) -> list[tuple[Any, datetime, str]]:
    """Authored findings from commits since ``week``, with their :func:`recent_kind`."""
    if week is None:
        return []
    hidden = excluded_types()
    out = []
    for f in rows:
        at = _when(field(f, "committed_at"))
        if at is None or at < week or not _authored_regression(f):
            continue
        if _is_test(field(f, "file_path"), files):
            continue
        kind = recent_kind(f, hidden)
        if kind is not None:
            out.append((f, at, kind))
    return out


def proven_performance(rows: Rows) -> frozenset[tuple[str, str]]:
    """``(file, marker)`` of each open performance cause stored in the default
    queue that is sure enough to be told to do (``perf_confident_verdict``):
    the bar a performance regression clears."""
    return frozenset(
        (field(r, "file_path"), field(r, "biomarker_type"))
        for r in rows
        if _open(r) and field(r, "queue_eligible") is True and perf_confident_verdict(r).eligible
    )


def build_recent(
    rows: Rows,
    *,
    week: datetime | None,
    open_findings: Rows,
    files: Mapping[str, FileFacts],
    performance: Rows = (),
) -> dict[str, Any]:
    """Findings a commit in the last week wrote that still count today.

    A code-shape or coverage finding counts while its open finding is still
    critical or high, and reads as that finding does today. A performance one
    counts while its file holds an open, proven cause of the same marker in
    the performance queue (``performance`` rows, :func:`proven_performance`).
    """
    if week is None:
        return {}
    candidates = _recent_candidates(rows, week=week, files=files)
    if not candidates:
        return {"recent_findings": ()}
    serious = _serious_open(open_findings)
    proven = proven_performance(performance)
    recent = []
    seen: set[tuple[str, str, str | None]] = set()
    for f, at, kind in sorted(candidates, key=lambda c: c[1], reverse=True):
        path, biomarker = field(f, "file_path"), field(f, "biomarker_type")
        key = (path, biomarker, field(f, "symbol"))
        # Still counts today, and counted once however many commits touched it.
        if key in seen:
            continue
        if kind == "performance":
            if (path, biomarker) not in proven:
                continue
            now = f
        else:
            now = serious.get(key)
            if now is None:
                continue
        seen.add(key)
        recent.append(
            RecentFinding(
                sha=field(f, "sha"),
                subject=field(f, "subject") or "",
                committed_at=at,
                file_path=path,
                symbol=field(f, "symbol"),
                biomarker=biomarker,
                severity=field(now, "severity"),
                change_kind=field(f, "change_kind"),
                line=field(now, "line_start"),
                reason=field(now, "reason") or "",
                kind=kind,
            )
        )
    return {"recent_findings": tuple(recent)}


def _serious_open(open_findings: Rows) -> dict[tuple[str, str, str | None], Any]:
    """The worst open critical/high finding per (file, marker, function).

    A commit's row records the finding as that commit left it; the open row
    is what is true today, so it decides whether the finding still counts and
    what the action says about it. Ties go to the higher impact, then the
    earlier line, then the reason, so the pick does not hang on row order.
    """

    def worse(h: Any) -> tuple:
        return (
            field(h, "severity") == "critical",
            field(h, "health_impact") or 0.0,
            -(field(h, "line_start") or 0),
            field(h, "reason") or "",
        )

    out: dict[tuple[str, str, str | None], Any] = {}
    for h in open_findings:
        if not _open(h) or field(h, "severity") not in ("critical", "high"):
            continue
        key = (field(h, "file_path"), field(h, "biomarker_type"), field(h, "function_name"))
        held = out.get(key)
        if held is None or worse(h) > worse(held):
            out[key] = h
    return out


def build_fix_first_store(rows: Mapping[str, Rows]) -> dict[str, Any]:
    """The head of the Fix-first queue, from the rows its own builder reads."""
    return {"fix_first": build_fix_first(**rows, limit=FIX_FIRST_ACTIONS).items}


def build_secrets(rows: Rows, files: Mapping[str, FileFacts]) -> dict[str, Any]:
    """High-severity secrets in the working tree, outside tests."""
    kinds = {*SECRET_KINDS, PUBLIC_ENV_KIND}
    return {
        "secrets": tuple(
            SecretFacts(
                field(r, "file_path"),
                field(r, "kind"),
                field(r, "line_number"),
                field(r, "snippet") or "",
            )
            for r in rows
            if not field(r, "commit_sha")
            and field(r, "severity") == "high"
            and field(r, "kind") in kinds
            and not _is_test(field(r, "file_path"), files)
        )
    }


def build_drift(rows: Rows, known_paths: Iterable[str]) -> dict[str, Any]:
    known = set(known_paths)
    return {
        "drift": tuple(
            DriftFacts(
                document=field(r, "file_path"),
                kind=field(r, "kind"),
                target=field(r, "target") or "",
                raw=field(r, "raw") or "",
                line=field(r, "line_number"),
                reason=field(r, "reason") or "",
                confidence=float(field(r, "confidence") or 0.0),
                target_known=(field(r, "target") in known),
            )
            for r in rows
        )
    }


def build_dead(rows: Rows, files: Mapping[str, FileFacts]) -> dict[str, Any]:
    """Open, deletion-ready findings outside tests."""
    skipped = excluded_types() | REVIEW_ONLY_KINDS
    return {
        "dead": tuple(
            DeadFacts(
                field(r, "id"),
                field(r, "file_path"),
                field(r, "symbol_name"),
                field(r, "lines") or 0,
            )
            for r in rows
            if _open(r)
            and is_safe(r)
            and is_served("dead_code", field(r, "kind"), field(r, "file_path"), skipped)
            and not _is_test(field(r, "file_path"), files)
        )
    }


def build_decisions(summary: Mapping[str, Any]) -> dict[str, Any]:
    counts = summary.get("summary") or {}
    return {
        "stale_decisions": tuple(
            DecisionFacts(field(d, "id"), field(d, "title"))
            for d in summary.get("stale_decisions") or []
        ),
        "proposed_decisions": int(counts.get("proposed") or 0),
        "accepted_decisions": int(counts.get("active") or 0),
    }


def build_coverage(row: Mapping[str, Any], head_sha: str | None) -> dict[str, Any]:
    count = row.get("files_measured") or 0
    if not count:
        return {"coverage": CoverageState("unknown")}
    sha = row.get("ingested_commit_sha")
    # Stale only when both commits are known and differ; a report without a
    # commit cannot be called out of date.
    status = "stale" if sha and head_sha and sha != head_sha else "measured"
    return {
        "coverage": CoverageState(
            status, _when(row.get("ingested_at")), int(count), bool(row.get("partial"))
        )
    }


# --- all of it ----------------------------------------------------------------


def build_repo_facts(
    *,
    anchor: datetime | str | None,
    head_sha: str | None = None,
    files: Rows | None = None,
    fix_events: Rows = (),
    dependents: Mapping[str, int] | None = None,
    health_findings: Rows = (),
    authors: Rows | None = None,
    commit_health: Rows | None = None,
    fix_first: Mapping[str, Rows] | None = None,
    security: Rows | None = None,
    doc_drift: Rows | None = None,
    known_paths: Iterable[str] = (),
    dead_code: Rows | None = None,
    decisions: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    test_map: Mapping[str, Any] | None = None,
    history_commits: int | None = None,
    unavailable: Mapping[str, str] | None = None,
    absent_reason: str = ABSENT,
) -> RepoFacts:
    """`RepoFacts` from rows (shapes in the module docstring).

    ``anchor`` is the newest commit's time and ``head_sha`` its sha;
    ``history_commits`` is how many commits the index holds. A store
    passed as ``None`` is reported unavailable with ``absent_reason``; one that
    fails to build is reported the same way, so it costs only its own rules.
    ``unavailable`` names stores the caller withholds, with the reason; they
    are still built, so their facts inform the rest.
    """
    anchor = _when(anchor)
    since = (anchor - QUARTER) if anchor else None
    week = (anchor - WEEK) if anchor else None
    values: dict[str, Any] = {"anchor": anchor, "history_commits": history_commits}
    missing: dict[str, str] = {}
    health_findings = list(health_findings)

    def build(store: str, rows: Any, fn: Callable[[Any], dict[str, Any]]) -> None:
        if rows is None:
            missing[store] = absent_reason
            return
        try:
            values.update(fn(rows))
        except Exception as exc:  # one store must not cost the list
            logger.warning("actions: %s unavailable: %s", store, exc)
            missing[store] = absent_reason

    def file_store(rows: Rows) -> dict[str, Any]:
        out = build_files(rows, since=since, fix_events=fix_events, dependents=dependents)
        out["files"] = with_leads(out["files"], health_findings)
        return out

    build("files", files, file_store)
    if "files" in values:
        build("test_map", test_map, lambda m: {"files": with_test_reach(values["files"], m)})
    known_files: Mapping[str, FileFacts] = values.get("files") or {}
    build("authors", authors, lambda rows: build_authors(rows, since=since))
    build(
        "commit_health",
        commit_health,
        lambda rows: build_recent(
            rows,
            week=week,
            open_findings=health_findings,
            files=known_files,
            performance=(fix_first or {}).get("performance") or (),
        ),
    )
    build("fix_first", fix_first, build_fix_first_store)
    build("security", security, lambda rows: build_secrets(rows, known_files))
    build("doc_drift", doc_drift, lambda rows: build_drift(rows, known_paths))
    build("dead_code", dead_code, lambda rows: build_dead(rows, known_files))
    build("decisions", decisions, build_decisions)
    build("coverage", coverage, lambda row: build_coverage(row, head_sha))
    values["unavailable"] = {**missing, **(unavailable or {})}
    return RepoFacts(**values)


__all__ = [
    "ABSENT",
    "AUTHORED_BASES",
    "COVERAGE_MARKERS",
    "LEAD_LOOKUP_MIN_COMMITS",
    "QUARTER",
    "SERIOUS",
    "WEEK",
    "build_authors",
    "build_coverage",
    "build_dead",
    "build_decisions",
    "build_drift",
    "build_files",
    "build_fix_first_store",
    "build_recent",
    "build_repo_facts",
    "build_secrets",
    "lead_paths",
    "proven_performance",
    "reach_paths",
    "recent_kind",
    "with_leads",
    "with_test_reach",
]
