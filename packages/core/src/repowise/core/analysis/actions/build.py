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
    ``line_start``, ``reason``, ``health_impact``, ``status`` (absent = open).
``authors``
    ``author_name``, ``author_email``, ``committed_at``: one row per commit, or
    per identity with its latest commit.
``commit_health``
    ``sha``, ``subject``, ``committed_at`` (the commit's), ``file_path``,
    ``symbol``, ``biomarker_type``, ``severity``, ``change_kind``,
    ``line_start``, ``reason``, ``attribution_basis``.
``performance``
    ``opportunity_id``, ``biomarker_type``, ``boundary_kind``, ``file_path``,
    ``intervention_symbol``, ``affected_call_sites_total``,
    ``affected_files_total``, ``actionability_state``, ``execution_context``,
    ``status`` (absent = open), and ``details`` (a dict) or ``details_json``
    carrying ``facets`` and ``plan``.
``security``
    ``file_path``, ``kind``, ``line_number``, ``snippet``, ``severity``,
    ``commit_sha`` (empty for the working tree).
``doc_drift``
    ``file_path``, ``kind``, ``target``, ``raw``, ``line_number``, ``reason``,
    ``confidence``; ``known_paths`` is every path the repository's history held.
``dead_code``
    ``id``, ``file_path``, ``symbol_name``, ``lines``, ``kind``,
    ``safe_to_delete`` (the stored verdict), ``status`` (absent = open).
``decisions``
    One mapping: ``stale_decisions`` (rows with ``id``, ``title``) and
    ``summary`` (``proposed``, ``active`` counts).
``coverage``
    One mapping: ``files_measured``, ``ingested_at``, ``ingested_commit_sha``,
    ``partial``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from repowise.core.analysis.dead_code.risk_factors import REVIEW_ONLY_KINDS
from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.scoring import HISTORY_CATEGORY, biomarker_category
from repowise.core.author_identity import author_identity_key

from .context import WEEK
from .facts import (
    CoverageState,
    DeadFacts,
    DecisionFacts,
    DriftFacts,
    FileFacts,
    LeadFinding,
    PerfFacts,
    RecentFinding,
    RepoFacts,
    SecretFacts,
)
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


def _is_test(path: str, files: Mapping[str, FileFacts]) -> bool:
    f = files.get(path)
    return f is not None and f.is_test


def _test_path(path: str | None) -> bool:
    lowered = (path or "").lower()
    return any(
        seg in lowered
        for seg in ("/tests/", "/test/", "__tests__", "/fixtures/", ".test.", ".spec.", "_test.")
    ) or lowered.startswith(("tests/", "test/"))


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
        if path in wanted and _open(f) and field(f, "biomarker_type") not in hidden:
            by_path[path].append(f)
    out = dict(files)
    for path, found in by_path.items():
        shape, _history = split_by_origin(found)
        lead = primary_finding(shape)
        if lead is not None:
            out[path] = replace(
                out[path],
                lead=LeadFinding(
                    biomarker=field(lead, "biomarker_type"),
                    severity=field(lead, "severity"),
                    function=field(lead, "function_name"),
                    line=field(lead, "line_start"),
                    reason=field(lead, "reason") or "",
                ),
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


def _recent_candidates(
    rows: Rows, *, week: datetime | None, files: Mapping[str, FileFacts]
) -> list[tuple[Any, datetime]]:
    """Authored critical/high code-shape findings from commits since ``week``."""
    if week is None:
        return []
    hidden = excluded_types()
    out = []
    for f in rows:
        at = _when(field(f, "committed_at"))
        biomarker = field(f, "biomarker_type")
        if (
            at is not None
            and at >= week
            and field(f, "change_kind") in ("introduced", "worsened")
            and field(f, "severity") in ("critical", "high")
            and field(f, "attribution_basis") in AUTHORED_BASES
            and not _is_test(field(f, "file_path"), files)
            and biomarker_category(biomarker) != HISTORY_CATEGORY
            and biomarker not in hidden
        ):
            out.append((f, at))
    return out


def build_recent(
    rows: Rows,
    *,
    week: datetime | None,
    open_findings: Rows,
    files: Mapping[str, FileFacts],
) -> dict[str, Any]:
    """Findings a commit in the last week wrote that are still open."""
    if week is None:
        return {}
    candidates = _recent_candidates(rows, week=week, files=files)
    if not candidates:
        return {"recent_findings": ()}
    open_keys = {
        (field(h, "file_path"), field(h, "biomarker_type"), field(h, "function_name"))
        for h in open_findings
        if _open(h)
    }
    recent = []
    seen: set[tuple[str, str, str | None]] = set()
    for f, at in sorted(candidates, key=lambda c: c[1], reverse=True):
        key = (field(f, "file_path"), field(f, "biomarker_type"), field(f, "symbol"))
        # Still open, and counted once however many commits touched it.
        if key not in open_keys or key in seen:
            continue
        seen.add(key)
        recent.append(
            RecentFinding(
                sha=field(f, "sha"),
                subject=field(f, "subject") or "",
                committed_at=at,
                file_path=field(f, "file_path"),
                symbol=field(f, "symbol"),
                biomarker=field(f, "biomarker_type"),
                severity=field(f, "severity"),
                change_kind=field(f, "change_kind"),
                line=field(f, "line_start"),
                reason=field(f, "reason") or "",
            )
        )
    return {"recent_findings": tuple(recent)}


def build_perf(rows: Rows) -> dict[str, Any]:
    """Open production opportunities that are ready to plan or advise on."""
    out = []
    for r in rows:
        if not (
            _open(r)
            and field(r, "execution_context") == "production"
            and field(r, "actionability_state") in ("plan_ready", "advisory")
        ):
            continue
        details = detail_map(r)
        facets = details.get("facets") or {}
        plan = details.get("plan") or {}
        out.append(
            PerfFacts(
                opportunity_id=field(r, "opportunity_id"),
                biomarker=field(r, "biomarker_type"),
                boundary=field(r, "boundary_kind"),
                file_path=field(r, "file_path"),
                symbol=field(r, "intervention_symbol"),
                call_sites=field(r, "affected_call_sites_total") or 0,
                files=field(r, "affected_files_total") or 0,
                actionability=field(r, "actionability_state"),
                exposure=facets.get("exposure"),
                loop_magnitude=facets.get("loop_magnitude"),
                effort=plan.get("effort_bucket"),
            )
        )
    return {"perf": tuple(out)}


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
            and not _test_path(field(r, "file_path"))
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
            and field(r, "safe_to_delete")
            and field(r, "kind") not in skipped
            and not _is_test(field(r, "file_path"), files)
            and not _test_path(field(r, "file_path"))
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
    performance: Rows | None = None,
    security: Rows | None = None,
    doc_drift: Rows | None = None,
    known_paths: Iterable[str] = (),
    dead_code: Rows | None = None,
    decisions: Mapping[str, Any] | None = None,
    coverage: Mapping[str, Any] | None = None,
    unavailable: Mapping[str, str] | None = None,
    absent_reason: str = ABSENT,
) -> RepoFacts:
    """`RepoFacts` from rows (shapes in the module docstring).

    ``anchor`` is the newest commit's time and ``head_sha`` its sha. A store
    passed as ``None`` is reported unavailable with ``absent_reason``; one that
    fails to build is reported the same way, so it costs only its own rules.
    ``unavailable`` names stores the caller withholds, with the reason; they
    are still built, so their facts inform the rest.
    """
    anchor = _when(anchor)
    since = (anchor - QUARTER) if anchor else None
    week = (anchor - WEEK) if anchor else None
    values: dict[str, Any] = {"anchor": anchor}
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
    known_files: Mapping[str, FileFacts] = values.get("files") or {}
    build("authors", authors, lambda rows: build_authors(rows, since=since))
    build(
        "commit_health",
        commit_health,
        lambda rows: build_recent(
            rows, week=week, open_findings=health_findings, files=known_files
        ),
    )
    build("performance", performance, build_perf)
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
    "LEAD_LOOKUP_MIN_COMMITS",
    "QUARTER",
    "WEEK",
    "build_authors",
    "build_coverage",
    "build_dead",
    "build_decisions",
    "build_drift",
    "build_files",
    "build_perf",
    "build_recent",
    "build_repo_facts",
    "build_secrets",
    "lead_paths",
    "with_leads",
]
