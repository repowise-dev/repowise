"""One ranked attention list for the repo Overview.

Overview's "Needs attention" used to be built from three stores — decision
health, knowledge silos, dead code — because those were the three that existed
when it was written. Everything added since (code-health findings and the
test-quality biomarkers that ride in them, security findings, documentation
drift) lives on a tab and never reaches the page whose job is to say where to
look. This module is the merge.

Two rules it follows, because they are what the old builder got wrong:

**Severity is the finding's own, never the source's.** The old list assigned a
severity per source — every dead-code row was ``low``, every proposed decision
``medium`` — so the ranking encoded "which store did this come from" and read
as if it encoded danger. Stores that publish a severity are passed through
unchanged here. Stores that genuinely do not have one (dead code and drift
carry a confidence float and nothing else) get a documented bucketing rather
than a per-source constant.

**A count travels with what it covers.** Each source is capped so no single
store floods the list, so the caller cannot infer a total by taking ``len()``.
:func:`build_attention` returns the totals beside the items.

The ladder is the four-level ``Severity`` from
``repowise.core.analysis.health.models``, the only severity vocabulary in the
codebase with parity tests against its TypeScript twin. The three-level ladder
the old ``AttentionItem`` declared is a subset of it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.grading import HealthBand, band_for
from repowise.core.persistence.models import (
    DeadCodeFinding,
    DocDriftFinding,
    HealthFileMetric,
    HealthFinding,
    RefactoringSuggestion,
    SecurityFinding,
)

#: Worst first. Mirrors ``core.analysis.health.models.Severity``.
SEVERITY_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}

#: Tie-break within one severity. The ordering is a claim about kind, not about
#: importance: a secret in the tree and a file the scanner scored are statements
#: about the code being wrong, a decision record drifting is a statement about
#: the documentation of the code, and dead code is untidiness. When two items
#: are equally severe, the one that says something is *wrong* goes first.
SOURCE_RANK: dict[str, int] = {
    "security_finding": 0,
    "health_finding": 1,
    "refactoring": 2,
    "stale_decision": 3,
    "ungoverned_hotspot": 4,
    "doc_drift": 5,
    "proposed_decision": 6,
    "knowledge_silo": 7,
    "dead_code": 8,
}

#: Per-source ceilings. Deliberately larger than the five rows Overview shows:
#: the global sort has to be able to rank a critical finding above a wall of
#: medium ones, which it cannot do if each source arrives pre-truncated to the
#: display length.
PER_SOURCE_CAP = 8

#: How much an item matters *within* its own source, for breaking ties inside a
#: severity band. Comparable only against its own kind — a health finding's
#: impact and a drift finding's confidence are not the same quantity — which is
#: all the tie-break needs, since `SOURCE_RANK` has already separated sources.
#:
#: Without it the tie-break fell through to the item id, so the lead of an area
#: was whichever member of its worst band sorted first alphabetically. Health
#: fetches in `health_impact DESC` and the rank pass then discarded that order.
WEIGHT_KEY = "weight"

#: Above this, a drift finding is confident enough to be worth a person's
#: attention rather than a glance. `DocDriftFinding` has no severity column, so
#: this bucketing is ours; it is set here rather than inline so the two places
#: that care (this list and the drift tab's own summary) can converge later.
DRIFT_CONFIDENT = 0.8


def _test_paths(repo_id: str):
    """Sub-select of this repository's test files.

    Production scope, which the Code Health page already offers as a control
    and this list was missing. A test file cannot be the most urgent thing in
    a repository: it has no users, a defect in it fails a build rather than a
    customer, and `tests/` is usually the largest directory in the tree, so
    without this the top of the list fills with test files and the section
    stops being about the product.

    Reads the `is_test` column the health pass already stamps, rather than
    re-deriving it from the path here and disagreeing with the page that shows
    the same files.
    """
    return select(HealthFileMetric.file_path).where(
        HealthFileMetric.repository_id == repo_id,
        HealthFileMetric.is_test.is_(True),
    )


#: The five health bands against this list's four severities. `excellent` and
#: `good` both mean "not a problem" here; the list has no band above `low`.
_SEVERITY_OF_BAND: dict[HealthBand, str] = {
    "at_risk": "critical",
    "needs_work": "high",
    "fair": "medium",
    "good": "low",
    "excellent": "low",
}


def _severity_of_file_score(score: float) -> str:
    """A file's own band, expressed as a severity.

    The health item is a file, so its severity has to describe the file. Taking
    the worst *finding's* severity instead inverted the ranking: a file with one
    finding on the per-finding cap (score 7.5, comfortably fair) called itself
    critical and outranked a file carrying thirty-eight findings and twelve
    points of deduction (score 1.0, genuinely at risk).

    `band_for` is the single source of truth for the cutoffs and is called, not
    reimplemented. A recalibration there has to reach this list, or the same
    file is `at risk` on the health pages and `medium` here.
    """
    return _SEVERITY_OF_BAND[band_for(score)]


def _severity_of_drift(confidence: float | None) -> str:
    """Bucket a drift confidence onto the shared ladder.

    Never returns ``high``. A document that misdescribes the tree is worth
    fixing and is not the same class of problem as a credential in git or a
    file the health pass scored critical, and letting drift reach the top band
    would push those off a five-row list.
    """
    return "medium" if (confidence or 0.0) >= DRIFT_CONFIDENT else "low"


async def _health_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Open code-health findings, aggregated to the file, worst file first.

    A file rather than a finding, because `health_impact` saturates. The cap is
    2.50 and several criticals sit exactly on it, so ranking findings against
    each other decides the top of the list on a tie-break rather than on the
    metric — it surfaced one `nested_complexity` finding ahead of a file
    carrying thirty-eight findings and twelve points of deduction. Summed
    impact is the file's whole score (a file scores `10 - sum`), so this ranks
    on the number the product already stands behind.

    This is also what puts the test-quality work on the page: mock saturation,
    assertion-free tests, untested hotspots and coverage gaps are
    `biomarker_type` values inside `health_findings` rather than stores of
    their own, so they arrive here without a special case.

    `total` stays a count of findings, not of files. The row says how much the
    area holds; the lead says where to start.
    """
    scoped = (
        HealthFinding.repository_id == repo_id,
        HealthFinding.status == "open",
        HealthFinding.file_path.not_in(_test_paths(repo_id)),
    )
    rows = (
        await session.execute(
            select(
                HealthFinding.file_path,
                func.sum(HealthFinding.health_impact).label("impact"),
                func.count(HealthFinding.id).label("findings"),
            )
            .where(*scoped)
            .group_by(HealthFinding.file_path)
            .order_by(func.sum(HealthFinding.health_impact).desc())
            .limit(PER_SOURCE_CAP)
        )
    ).all()
    total = await session.scalar(select(func.count(HealthFinding.id)).where(*scoped)) or 0
    if not rows:
        return [], int(total), ""

    # The biomarker to name each file by: its own heaviest one. Bounded to the
    # handful of files above, so this is a keyed read rather than a scan.
    paths = [r.file_path for r in rows]
    lead_biomarker: dict[str, str] = {}
    for path, biomarker in (
        await session.execute(
            select(HealthFinding.file_path, HealthFinding.biomarker_type)
            .where(*scoped, HealthFinding.file_path.in_(paths))
            .order_by(HealthFinding.health_impact.desc())
        )
    ).all():
        lead_biomarker.setdefault(path, biomarker)

    items = [
        {
            "id": f"health-{row.file_path}",
            "type": "health_finding",
            "title": row.file_path,
            "description": (
                f"{row.findings} finding{'' if row.findings == 1 else 's'}"
                f" · {float(row.impact or 0.0):.1f} deducted"
            ),
            "severity": _severity_of_file_score(
                max(1.0, 10.0 - float(row.impact or 0.0))
            ),
            "target_id": row.file_path,
            # The UI resolves this through the biomarker glossary it owns, so
            # this module holds no second copy of that vocabulary.
            "subtype": lead_biomarker.get(row.file_path),
            "weight": float(row.impact or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _security_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Security findings, high severity first.

    ``SecurityFinding`` has no status column: rows are replaced wholesale by
    each scan, so every stored row is open by construction and there is nothing
    to filter on. It also only ever writes ``high`` or ``low`` — the router's
    docstring mentions a ``med`` that the scanner never emits — so the two
    middle bands of the ladder are simply unused here.
    """
    rows = (
        (
            await session.execute(
                select(SecurityFinding)
                .where(SecurityFinding.repository_id == repo_id)
                .order_by(
                    # `high` before anything else, then newest. Expressed as a
                    # sort key rather than two queries.
                    (SecurityFinding.severity != "high"),
                    SecurityFinding.detected_at.desc(),
                )
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(SecurityFinding.id)).where(
                SecurityFinding.repository_id == repo_id
            )
        )
        or 0
    )
    # A secret in the working tree and one only reachable through git history
    # are different jobs — one is a deletion, the other a rotation and a
    # rewrite — and 245 findings reads as an emergency until the split says
    # most of them are historical.
    in_tree = (
        await session.scalar(
            select(func.count(SecurityFinding.id)).where(
                SecurityFinding.repository_id == repo_id,
                SecurityFinding.commit_sha == "",
            )
        )
        or 0
    )
    items = [
        {
            "id": f"security-{row.id}",
            "type": "security_finding",
            "title": row.file_path,
            "description": (
                f"{row.kind}{f' in commit {row.commit_sha[:7]}' if row.commit_sha else ''}"
            ),
            "severity": row.severity if row.severity in SEVERITY_RANK else "medium",
            "target_id": row.file_path,
            "subtype": row.kind,
            # Nothing separates two `high` secrets but recency, and a secret
            # still sitting in the tree outranks one only git remembers.
            "weight": 1.0 if not row.commit_sha else 0.0,
        }
        for row in rows
    ]
    return items, int(total), (
        f"{in_tree} in the working tree, {int(total) - in_tree} in git history"
        if total
        else ""
    )


async def _drift_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Documentation drift, most confident first.

    ``file_path`` on this row is the *document* making the false claim, not the
    code it is wrong about, which is why the row links to the drift tab rather
    than to a file page.
    """
    rows = (
        (
            await session.execute(
                select(DocDriftFinding)
                .where(DocDriftFinding.repository_id == repo_id)
                .order_by(DocDriftFinding.confidence.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(DocDriftFinding.id)).where(
                DocDriftFinding.repository_id == repo_id
            )
        )
        or 0
    )
    items = [
        {
            "id": f"drift-{row.id}",
            "type": "doc_drift",
            "title": row.file_path,
            "description": row.reason or f"{row.kind} no longer matches the tree",
            "severity": _severity_of_drift(row.confidence),
            "target_id": row.file_path,
            "subtype": row.kind,
            "weight": float(row.confidence or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _refactoring_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Open refactoring plans, biggest recovered health first.

    The one store on this list that holds *work* rather than observations: a
    row is a plan with an impact estimate and an effort bucket, so it is the
    only source where "what should I do about it" is already answered.

    Severity is derived from the impact it recovers, because the store has no
    severity column and an opportunity is not a defect. It is capped at
    ``medium``: a thing you could improve never outranks a thing that is
    wrong.
    """
    rows = (
        (
            await session.execute(
                select(RefactoringSuggestion)
                .where(
                    RefactoringSuggestion.repository_id == repo_id,
                    RefactoringSuggestion.status == "open",
                    RefactoringSuggestion.file_path.not_in(_test_paths(repo_id)),
                )
                .order_by(RefactoringSuggestion.impact_delta.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(RefactoringSuggestion.id)).where(
                RefactoringSuggestion.repository_id == repo_id,
                RefactoringSuggestion.status == "open",
                RefactoringSuggestion.file_path.not_in(_test_paths(repo_id)),
            )
        )
        or 0
    )
    items = [
        {
            "id": f"refactor-{row.id}",
            "type": "refactoring",
            "title": row.target_symbol or row.file_path,
            "description": (
                f"+{row.impact_delta:.1f} health"
                f"{f', {row.effort_bucket} effort' if row.effort_bucket else ''}"
                f" · {row.file_path}"
            ),
            "severity": "medium" if (row.impact_delta or 0.0) >= 1.0 else "low",
            "target_id": row.file_path,
            "subtype": row.refactoring_type,
            "weight": float(row.impact_delta or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


async def _dead_code_items(session: AsyncSession, repo_id: str) -> tuple[list[dict], int, str]:
    """Safe-to-delete dead code, most confident first.

    Stays in the bottom band whatever its confidence. Confidence here measures
    how sure the graph is that nothing reaches the symbol, not how much it
    costs to leave it there, and a high-confidence unused export is still the
    least urgent thing this list can show.
    """
    rows = (
        (
            await session.execute(
                select(DeadCodeFinding)
                .where(
                    DeadCodeFinding.repository_id == repo_id,
                    DeadCodeFinding.status == "open",
                    DeadCodeFinding.safe_to_delete.is_(True),
                    DeadCodeFinding.file_path.not_in(_test_paths(repo_id)),
                )
                .order_by(DeadCodeFinding.confidence.desc())
                .limit(PER_SOURCE_CAP)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.scalar(
            select(func.count(DeadCodeFinding.id)).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.status == "open",
                DeadCodeFinding.safe_to_delete.is_(True),
                DeadCodeFinding.file_path.not_in(_test_paths(repo_id)),
            )
        )
        or 0
    )
    items = [
        {
            "id": f"dead-{row.id}",
            "type": "dead_code",
            "title": row.symbol_name or row.file_path,
            "description": f"Unreachable {row.symbol_kind or 'symbol'} ({row.lines} lines)",
            "severity": "low",
            "target_id": row.file_path,
            "subtype": row.kind,
            "weight": float(row.confidence or 0.0),
        }
        for row in rows
    ]
    return items, int(total), ""


def _decision_items(decision_health: dict) -> tuple[list[dict], int, str]:
    """Decision-record problems, in the order the summary already ranked them.

    ``get_decision_health_summary`` sorts stale records by staleness, proposed
    by confidence and ungoverned hotspots by hotspot score, and its docstring
    asks callers to truncate rather than re-sort. Truncation only.
    """
    items: list[dict] = []
    stale = decision_health.get("stale_decisions", []) or []
    proposed = decision_health.get("proposed_awaiting_review", []) or []
    ungoverned = decision_health.get("ungoverned_hotspots", []) or []

    # The summary already ranked each lane and asks callers not to re-sort, so
    # position becomes the weight: first in a lane outranks second.
    for rank, d in enumerate(stale[:PER_SOURCE_CAP]):
        items.append(
            {
                "weight": float(PER_SOURCE_CAP - rank),
                "id": f"stale-{d.id}",
                "type": "stale_decision",
                "title": d.title,
                "description": "Active decision drifting from the code it governs",
                "severity": "high",
                "target_id": d.id,
            }
        )
    for rank, d in enumerate(proposed[:PER_SOURCE_CAP]):
        items.append(
            {
                "weight": float(PER_SOURCE_CAP - rank),
                "id": f"proposed-{d.id}",
                "type": "proposed_decision",
                "title": d.title,
                "description": "Auto-proposed decision awaiting review",
                "severity": "medium",
                "target_id": d.id,
            }
        )
    for rank, fp in enumerate(ungoverned[:PER_SOURCE_CAP]):
        items.append(
            {
                "weight": float(PER_SOURCE_CAP - rank),
                "id": f"ungoverned-{fp}",
                "type": "ungoverned_hotspot",
                "title": fp,
                "description": "High-churn file with no governing decision",
                "severity": "medium",
                "target_id": fp,
            }
        )
    # One count over three lanes is not a fact anyone can act on. "1,064
    # decisions need attention" is mostly our own auto-proposals plus a
    # governance-coverage gap, and reporting those as one number overstates
    # the first and hides the second.
    parts = []
    if stale:
        parts.append(f"{len(stale)} drifting from the code")
    if proposed:
        parts.append(f"{len(proposed)} awaiting review")
    if ungoverned:
        parts.append(f"{len(ungoverned)} hotspots ungoverned")
    return items, len(stale) + len(proposed) + len(ungoverned), " · ".join(parts)


def _silo_items(knowledge_silos: list[dict]) -> tuple[list[dict], int, str]:
    """Files whose history sits with one person."""
    items = [
        {
            "id": f"silo-{s['file_path']}",
            "type": "knowledge_silo",
            "title": s["file_path"],
            "description": (
                f"{round(s['owner_pct'] * 100)}% single-owner concentration"
                + (
                    f", {s['commit_count_90d']} commits in 90d"
                    if s.get("commit_count_90d")
                    else ", not touched in 90 days"
                )
            ),
            "severity": "medium",
            "target_id": s["file_path"],
            # Sole ownership of dormant code is a fact, not a risk. Activity
            # decides which silo leads; concentration only breaks a tie.
            "weight": float(s.get("commit_count_90d") or 0) + float(s["owner_pct"]),
        }
        for s in knowledge_silos[:PER_SOURCE_CAP]
    ]
    return items, len(knowledge_silos), ""


def _rank(items: list[dict]) -> list[dict]:
    """Worst band first, and inside a band one item per source before seconds.

    A plain severity sort is the obvious implementation and it is wrong at
    real scale. The sources are orders of magnitude apart — this repository
    holds around sixteen thousand open health findings against twenty drift
    findings — so a strict sort hands every visible row to whichever store is
    biggest, and the page silently becomes a code-health list with a
    misleading title.

    Severity still decides absolutely: every critical precedes every high, and
    a source with nothing critical does not get pulled up. The round-robin
    applies only within a band, and the buckets are walked in ``SOURCE_RANK``
    order, so the leading item of each band is still the one the tie-break
    prefers.

    Ties are broken on ``id`` before bucketing, so the order is stable across
    requests rather than following whatever order the queries returned in.
    """
    ranked: list[dict] = []
    for band in sorted({i["severity"] for i in items}, key=lambda s: SEVERITY_RANK.get(s, 99)):
        buckets: dict[str, list[dict]] = {}
        for item in sorted(
            (i for i in items if i["severity"] == band),
            key=lambda i: (
                SOURCE_RANK.get(i["type"], len(SOURCE_RANK)),
                -float(i.get(WEIGHT_KEY) or 0.0),
                i["id"],
            ),
        ):
            buckets.setdefault(item["type"], []).append(item)
        while buckets:
            for source in list(buckets):
                ranked.append(buckets[source].pop(0))
                if not buckets[source]:
                    del buckets[source]
    return ranked


#: Which item types roll up into which area row. Decisions are three lanes and
#: one destination, so they collapse; everything else is its own area.
AREA_OF: dict[str, str] = {
    "security_finding": "security",
    "health_finding": "health",
    "refactoring": "refactoring",
    "doc_drift": "doc_drift",
    "stale_decision": "decisions",
    "proposed_decision": "decisions",
    "ungoverned_hotspot": "decisions",
    "knowledge_silo": "ownership",
    "dead_code": "dead_code",
}

#: Display order for the area rows. Fixed, not data-driven: a list whose rows
#: reorder between visits cannot be scanned by position, and these are stable
#: subjects rather than a ranking.
AREA_ORDER: list[str] = [
    "security",
    "health",
    "refactoring",
    "doc_drift",
    "decisions",
    "ownership",
    "dead_code",
]


def _areas(
    items: list[dict], by_source: dict[str, int], details: dict[str, str]
) -> list[dict]:
    """One row per area of work, each carrying its worst item as a lead.

    Ranking individual findings does not scale. With sixteen thousand open
    health findings, six rows of findings is a random sample of one store
    rather than triage, and the areas that hold less simply never appear. An
    area row is a count, a named example and a way in, which is a summary and
    a specific thing at the same time.

    Built from the already-ranked ``items`` rather than from a second set of
    queries, so an area's lead is exactly the item that topped it.
    """
    # `details` arrives keyed by SOURCE; the rows are keyed by AREA, and the
    # two only coincide for the sources whose name is also their area.
    totals: dict[str, int] = {}
    area_details: dict[str, str] = {}
    for source, count in by_source.items():
        area = AREA_OF.get(source, source)
        totals[area] = totals.get(area, 0) + count
        if details.get(source):
            area_details[area] = details[source]

    leads: dict[str, dict] = {}
    for item in items:  # already ranked worst-first
        leads.setdefault(AREA_OF.get(item["type"], item["type"]), item)

    out: list[dict] = []
    for area in AREA_ORDER:
        total = totals.get(area, 0)
        if not total:
            continue
        lead = leads.get(area)
        out.append(
            {
                "key": area,
                "total": total,
                "severity": lead["severity"] if lead else "low",
                # What the count is made of, where one number over several
                # lanes would misstate it. Empty for areas that have one lane.
                "detail": area_details.get(area, ""),
                "lead": (
                    {
                        "title": lead["title"],
                        "description": lead["description"],
                        "type": lead["type"],
                        "target_id": lead.get("target_id"),
                        "subtype": lead.get("subtype"),
                        "severity": lead["severity"],
                    }
                    if lead
                    else None
                ),
            }
        )
    return out


async def build_attention(
    session: AsyncSession,
    repo_id: str,
    *,
    decision_health: dict,
    knowledge_silos: list[dict],
) -> dict[str, Any]:
    """Every attention source, merged and ranked worst-first.

    Returns ``{"items": [...], "total": int, "by_source": {type: count}}``.
    ``total`` counts everything the sources hold, not the capped list, so a
    caller rendering five rows can honestly say "5 of 231".

    ``decision_health`` and ``knowledge_silos`` are passed in rather than
    fetched: the Overview route already has both in hand for other parts of its
    payload, and re-deriving them here would be two more scans of the widest
    table in the schema.

    A source that raises is dropped with its count, not allowed to fail the
    page. Overview is an aggregate over a dozen stores and one absent table (an
    index built before a detector shipped) should cost the row rather than the
    whole page.
    """
    by_source: dict[str, int] = {}
    details: dict[str, str] = {}
    items: list[dict] = []

    def _collect(key: str | None, got: list[dict], total: int, detail: str) -> None:
        source = key or (got[0]["type"] if got else None)
        if source and total:
            by_source[source] = total
            if detail:
                details[source] = detail
        items.extend(got)

    async def _add_async(fn) -> None:
        try:
            got, total, detail = await fn(session, repo_id)
        except Exception:
            return
        _collect(None, got, total, detail)

    await _add_async(_health_items)
    await _add_async(_security_items)
    await _add_async(_refactoring_items)
    await _add_async(_drift_items)
    await _add_async(_dead_code_items)

    _collect("decisions", *_decision_items(decision_health))
    _collect("knowledge_silo", *_silo_items(knowledge_silos))

    ranked = _rank(items)
    return {
        "items": ranked,
        "total": sum(by_source.values()),
        "by_source": by_source,
        "areas": _areas(ranked, by_source, details),
    }
