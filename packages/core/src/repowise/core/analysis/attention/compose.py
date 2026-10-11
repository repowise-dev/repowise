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
:func:`compose_attention` returns the totals beside the items.

The ladder is the four-level ``Severity`` from
``repowise.core.analysis.health.models``, the only severity vocabulary in the
codebase with parity tests against its TypeScript twin. The three-level ladder
the old ``AttentionItem`` declared is a subset of it.

Pure: no I/O, no session, no clock. Fetching each source is the caller's job;
the fold takes one :class:`SourceResult` per source.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, get_args

from repowise.core.analysis.health.grading import HealthBand, band_for

# Mirrored by the TS union in ``packages/ui/src/dashboard/attention-href.ts``;
# ``test_wire_vocabulary_parity.py`` fails when the two disagree.
AttentionItemType = Literal[
    "stale_decision",
    "knowledge_silo",
    "ungoverned_hotspot",
    "dead_code",
    "proposed_decision",
    "health_finding",
    "security_finding",
    "doc_drift",
    "refactoring",
]
ATTENTION_ITEM_TYPES: frozenset[str] = frozenset(get_args(AttentionItemType))

#: One key per store. The three decision lanes share one total and detail, so
#: they arrive, and are counted in ``by_source``, under ``decisions``.
AttentionSource = Literal[
    "health_finding",
    "security_finding",
    "refactoring",
    "doc_drift",
    "dead_code",
    "decisions",
    "knowledge_silo",
]

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


@dataclass(frozen=True, slots=True)
class SourceResult:
    """One store's contribution: its top items, its full count, and a breakdown.

    The producer owns the cap: ``items`` holds at most :data:`PER_SOURCE_CAP`
    rows per lane, already in that store's own best-first order, while ``total`` counts
    every row the store holds under the same scope. Each item is a dict with
    ``id``, ``type``, ``title``, ``description``, ``severity``, ``target_id``,
    and optionally ``subtype`` and ``weight`` (see :data:`WEIGHT_KEY`).
    ``detail`` names what ``total`` is made of, where one number would mislead.
    """

    items: tuple[Mapping[str, Any], ...]
    total: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionAttentionInput:
    """The two fields of a decision record the attention list shows.

    No staleness score: :func:`decision_source` never re-sorts a lane, so a
    producer without one passes the stale lane in whatever order it trusts.
    """

    id: str
    title: str


class AttentionArea(TypedDict):
    key: str
    total: int
    severity: str
    detail: str
    lead: dict[str, Any] | None


class AttentionView(TypedDict):
    items: list[Mapping[str, Any]]
    total: int
    by_source: dict[str, int]
    areas: list[AttentionArea]


#: The five health bands against this list's four severities. `excellent` and
#: `good` both mean "not a problem" here; the list has no band above `low`.
_SEVERITY_OF_BAND: dict[HealthBand, str] = {
    "at_risk": "critical",
    "needs_work": "high",
    "fair": "medium",
    "good": "low",
    "excellent": "low",
}


def severity_of_file_score(score: float) -> str:
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


def severity_of_drift(confidence: float | None) -> str:
    """Bucket a drift confidence onto the shared ladder.

    Never returns ``high``. A document that misdescribes the tree is worth
    fixing and is not the same class of problem as a credential in git or a
    file the health pass scored critical, and letting drift reach the top band
    would push those off a five-row list.
    """
    return "medium" if (confidence or 0.0) >= DRIFT_CONFIDENT else "low"


def decision_source(
    stale: Sequence[DecisionAttentionInput],
    proposed: Sequence[DecisionAttentionInput],
    ungoverned: Sequence[str],
) -> SourceResult:
    """Decision-record problems, in the order the caller already ranked them.

    ``get_decision_health_summary`` sorts stale records by staleness, proposed
    by confidence and ungoverned hotspots by hotspot score, and its docstring
    asks callers to truncate rather than re-sort. Truncation only.
    """
    items: list[dict] = []

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
    return SourceResult(
        tuple(items), len(stale) + len(proposed) + len(ungoverned), " · ".join(parts)
    )


def silo_source(knowledge_silos: Sequence[Mapping[str, Any]]) -> SourceResult:
    """Files whose history sits with one person.

    Each silo needs ``file_path`` and ``owner_pct``; ``commit_count_90d`` is
    optional and read as dormant when absent.
    """
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
    return SourceResult(tuple(items), len(knowledge_silos), "")


def _rank(items: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
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
    ranked: list[Mapping[str, Any]] = []
    for band in sorted({i["severity"] for i in items}, key=lambda s: SEVERITY_RANK.get(s, 99)):
        buckets: dict[str, list[Mapping[str, Any]]] = {}
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
    items: list[Mapping[str, Any]], by_source: dict[str, int], details: dict[str, str]
) -> list[AttentionArea]:
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

    leads: dict[str, Mapping[str, Any]] = {}
    for item in items:  # already ranked worst-first
        leads.setdefault(AREA_OF.get(item["type"], item["type"]), item)

    out: list[AttentionArea] = []
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


def compose_attention(sources: Mapping[AttentionSource, SourceResult]) -> AttentionView:
    """Every attention source, merged and ranked worst-first.

    ``total`` counts everything the sources hold, not the capped list, so a
    caller rendering five rows can honestly say "5 of 231". A source with a
    zero total is left out of ``by_source``; a source the caller could not
    fetch is simply absent from ``sources``. ``by_source`` follows the order
    of ``sources``.
    """
    by_source: dict[str, int] = {}
    details: dict[str, str] = {}
    items: list[Mapping[str, Any]] = []
    for source, result in sources.items():
        if result.total:
            by_source[source] = result.total
            if result.detail:
                details[source] = result.detail
        items.extend(result.items)

    ranked = _rank(items)
    return {
        "items": ranked,
        "total": sum(by_source.values()),
        "by_source": by_source,
        "areas": _areas(ranked, by_source, details),
    }
