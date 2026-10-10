"""Serving performance opportunities: the pure half, with no session.

Query parsing, the vocabulary, the plan link and every response shape live
here. The queue's sort, filter and facet rules are data: the store builds its
``ORDER BY``, ``WHERE`` and facet aggregate from the same tables that
:func:`row_sort_key`, :func:`keep` and :func:`facet_counts` read over rows
already in memory, so the two cannot disagree about what a queue holds.

Rows may be ORM rows, SQL rows or mappings; every read goes through
``rows.field``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from repowise.core.analysis.health import queue_rules
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.fix_first.text import perf_cost
from repowise.core.analysis.health.queue_rules import NULL_VALUE, Facet, FilterRule, SortKeys
from repowise.core.analysis.health.rows import detail_map, field, json_field
from repowise.core.analysis.health.worth import COST_PROOFS, LOW_PRIORITY_LABEL, perf_low_priority

from .opportunities import PERFORMANCE_MODEL_VERSION
from .opportunity_rank import DEFAULT_QUEUE_PROOFS, DEFAULT_QUEUE_STATES, NON_LEADING_MARKERS

if TYPE_CHECKING:
    from .opportunities import PerformanceOpportunity

PerformanceContext = Literal["production", "tooling", "test", "unknown", "all"]

CANONICAL_CONTEXTS = ("production", "tooling", "test", "unknown")
"""The whole vocabulary. ``all`` is every one of them, never a subset."""

DEFAULT_CONTEXT: PerformanceContext = "production"
"""What a caller that names no context is asking about.

Measured on a 17-repository corpus: 56.5% of opportunities are outside
production code, and the markers concentrated there are the ones that fail
hand-labelling. Test fixtures, benchmark harnesses, CI scripts and mocks are
real code, but "this benchmark repeats work" is a fact about a benchmark, not
performance work someone should schedule. Defaulting to production makes the
first answer the one a reader can act on; every other context stays one
selection away and is counted in ``repository_total`` either way.
"""

_DEPRECATED_CONTEXTS = {"production_tooling": frozenset({"production", "tooling"})}
"""Accepted for one compatibility window and never emitted as canonical.

An older client asks for Production+Tooling under one name. Answering it keeps
that client working; echoing the name back would make a retired spelling look
like the current product concept.
"""

CANONICAL_VIEWS = ("detail", "summary")
CONFIDENCES = ("high", "medium", "low")
ACTIONABILITIES = ("plan_ready", "advisory", "investigate", "expected")
BOUNDARIES = ("db", "network", "filesystem", "subprocess", "lock", NULL_VALUE)

DEFAULT_ACTIONABILITIES = DEFAULT_QUEUE_STATES
"""``expected`` rows offer nothing to change and ``investigate`` rows no strategy to apply,
so both are asked for, not queued. The summary's ``default_queue`` counts each one left out."""

PLAN_REASONS = {
    "available": "A stored performance plan addresses this exact opportunity.",
    "no_safe_plan": (
        "The analysis found the shared cause but could not prove one coherent "
        "intervention without guessing."
    ),
    "not_persisted": (
        "A supported strategy exists, but this index does not contain its matching "
        "stored plan. Reindex to refresh recommendations."
    ),
}

SUMMARY_UNAVAILABLE = {
    "status": "unavailable",
    "reason": "no_materialized_analysis",
    "detail": (
        "This index has no materialized performance analysis. Run repowise update "
        "to build it."
    ),
}

# ---------------------------------------------------------------------------
# The rules, as data
# ---------------------------------------------------------------------------

#: Orderings the queue offers, as ``(field, descending)`` keys, each ending in
#: rank so every one is total. Applied by the store rather than to a fetched
#: page: sorting a page selected by a different key would order twenty rows
#: correctly and the repository wrongly.
SORTS: dict[str, SortKeys] = {
    "rank": (("rank_position", False),),
    "leverage": (("affected_call_sites_total", True), ("rank_position", False)),
    "observations": (("observations_total", True), ("rank_position", False)),
}
DEFAULT_SORT = "rank"
CANONICAL_SORTS = tuple(SORTS)

#: The parameters are :class:`PerformanceQuery` attributes; ``status`` is
#: always ``open``, since a resolved id is a detail read, never a queue row.
FILTERS: tuple[FilterRule, ...] = (
    FilterRule("status", "status", "eq", "always"),
    FilterRule("contexts", "execution_context", "in", "set"),
    # A cause with no I/O boundary is stored as NULL and asked for as ``none``.
    FilterRule("boundary", "boundary_kind", "eq_or_null", "set"),
    FilterRule("confidence", "evidence_confidence", "eq", "set"),
    FilterRule("actionabilities", "actionability_state", "in", "set"),
    FilterRule("proofs", "cost_proof", "in", "set"),
    FilterRule("file_paths", "file_path", "in", "set"),
)

#: Facet name, the field it counts, and the filter parameter it is
#: cross-filtered by (``None``: no filter narrows it).
FACETS: tuple[Facet, ...] = (
    ("context", "execution_context", "contexts"),
    ("boundary", "boundary_kind", "boundary"),
    ("confidence", "evidence_confidence", "confidence"),
    ("actionability", "actionability_state", "actionabilities"),
    ("plan_state", "plan_state", None),
    ("proof", "cost_proof", "proofs"),
)
FACET_FIELDS = tuple(column for _, column, _ in FACETS)


def sort_keys(sort: str | None) -> SortKeys:
    """The keys for *sort*; an unknown sort reads as rank."""
    return SORTS.get(sort or DEFAULT_SORT, SORTS[DEFAULT_SORT])


def keep(row: Any, params: Any) -> bool:
    """Whether *row* passes every filter *params* sets (a query or a mapping)."""
    return queue_rules.keep(FILTERS, row, params)


def row_sort_key(row: Any, sort: str | None = None) -> tuple[Any, ...]:
    """The sort key for *sort*; an unknown sort reads as rank."""
    return queue_rules.sort_key(sort_keys(sort), row)


def _facet_selection(query: PerformanceQuery) -> dict[str, Any]:
    """What a caller chose, per facet. The default queue states and proof are
    not a choice, so an unfiltered control still shows every value."""
    return {
        "contexts": query.contexts,
        "boundary": query.boundary,
        "confidence": query.confidence,
        "actionabilities": (
            None if query.actionability is None else frozenset({query.actionability})
        ),
        "proofs": None if query.proof is None else frozenset({query.proof}),
    }


def fold_facets(
    groups: Iterable[Sequence[Any]], query: PerformanceQuery
) -> dict[str, list[dict[str, Any]]]:
    """Fold ``(*facet fields, count)`` groups into per-value counts, largest
    first, each facet cross-filtered by the query's other selections."""
    folded = queue_rules.fold_facets(
        groups, FACETS, rules=FILTERS, selection=_facet_selection(query), null=NULL_VALUE
    )
    return {
        name: [
            {"value": value, "total": total}
            for value, total in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        for name, counts in folded.items()
    }


def facet_counts(rows: Iterable[Any], query: PerformanceQuery) -> dict[str, list[dict[str, Any]]]:
    """:func:`fold_facets` over opportunity rows in memory, as the store scopes them:
    every open row in the query's files."""
    scope = {"status": "open", "file_paths": query.file_paths}
    return fold_facets(
        ((*(field(row, column) for column in FACET_FIELDS), 1) for row in rows if keep(row, scope)),
        query,
    )


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerformanceQuery:
    """One request for a page of the queue, in canonical terms.

    Built by :func:`parse_query` so both adapters normalize identically and an
    unrecognized value is reported rather than silently read as "no results".
    """

    context: PerformanceContext = DEFAULT_CONTEXT
    boundary: str | None = None
    confidence: str | None = None
    actionability: str | None = None
    proof: str | None = None
    view: str = "detail"
    sort: str = DEFAULT_SORT
    file_paths: tuple[str, ...] | None = None
    limit: int = 20
    offset: int = 0

    @property
    def status(self) -> str:
        return "open"

    @property
    def contexts(self) -> frozenset[str] | None:
        if self.context == "all":
            return None
        alias = _DEPRECATED_CONTEXTS.get(self.context)
        return alias if alias is not None else frozenset({self.context})

    @property
    def actionabilities(self) -> frozenset[str]:
        """The set the queue is filtered to: one explicit state, or the default queue states (plan_ready, advisory)."""
        if self.actionability is None:
            return DEFAULT_ACTIONABILITIES
        return frozenset({self.actionability})

    @property
    def proofs(self) -> frozenset[str]:
        """``unproven`` when asked for; else the measured causes, so every
        surface lists what the default queue counts. The ``proof`` facet says
        how many unproven ones sit one filter away."""
        if self.proof is None:
            return DEFAULT_QUEUE_PROOFS
        return frozenset({self.proof})


def _resolve_context(context: str | None, ignored: dict[str, str]) -> PerformanceContext:
    if not context:
        return DEFAULT_CONTEXT
    if context in CANONICAL_CONTEXTS or context == "all" or context in _DEPRECATED_CONTEXTS:
        return context  # type: ignore[return-value]
    # Named, so the caller learns the value was not understood, then treated
    # as absent like every other unrecognized filter.
    ignored["performance_context"] = (
        f"{context} (accepted: {', '.join((*CANONICAL_CONTEXTS, 'all'))})"
    )
    return DEFAULT_CONTEXT


def parse_query(
    *,
    context: str | None = None,
    boundary: str | None = None,
    confidence: str | None = None,
    actionability: str | None = None,
    proof: str | None = None,
    view: str | None = None,
    sort: str | None = None,
    file_paths: tuple[str, ...] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[PerformanceQuery, dict[str, str]]:
    """Normalize caller input, reporting anything unrecognized by name.

    An unknown filter value must not read as an empty repository, so it is
    dropped from the query and named in the returned map instead.
    """
    ignored: dict[str, str] = {}

    def pick(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
        if value is None or value == "":
            return None
        if value in allowed:
            return value
        # Name the vocabulary, so a rejected value is recoverable from the reply.
        ignored[name] = f"{value} (accepted: {', '.join(allowed)})"
        return None

    resolved_context = _resolve_context(context, ignored)
    return (
        PerformanceQuery(
            context=resolved_context,
            boundary=pick("performance_boundary", boundary, BOUNDARIES),
            confidence=pick("performance_confidence", confidence, CONFIDENCES),
            actionability=pick("performance_actionability", actionability, ACTIONABILITIES),
            proof=pick("performance_proof", proof, COST_PROOFS),
            view=pick("performance_view", view, CANONICAL_VIEWS) or "detail",
            sort=pick("performance_sort", sort, CANONICAL_SORTS) or DEFAULT_SORT,
            file_paths=file_paths,
            limit=max(0, limit),
            offset=max(0, offset),
        ),
        ignored,
    )


# ---------------------------------------------------------------------------
# Plan link
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanLink:
    """Where the plan for one opportunity lives, in both address spaces.

    ``row_id`` resolves through the repository-scoped REST route and
    ``public_id`` through the ``plan_id`` selector on the agent surface. The
    rule that decides *whether* there is a plan is :func:`plan_link`.
    """

    state: str
    row_id: str | None = None
    public_id: str | None = None

    @property
    def reason(self) -> str:
        return PLAN_REASONS[self.state]


def plan_link(row: Any, plan: Any | None, public_id: str | None = None) -> PlanLink:
    """The link for one opportunity row, given its stored plan row (if any)."""
    if plan is None:
        # The materialized state already distinguishes "no safe plan" from
        # "a strategy exists but this index has no plan row".
        state = field(row, "plan_state")
        return PlanLink(state if state != "available" else "not_persisted")
    return PlanLink("available", row_id=str(field(plan, "id")), public_id=public_id)


def intervention_file(opportunity: PerformanceOpportunity) -> str:
    """The file a reader would open first: the intervention, else the evidence."""
    symbol = opportunity.intervention_symbol
    if symbol:
        return symbol.split("::", 1)[0]
    return opportunity.evidence[0]["file_path"] if opportunity.evidence else ""


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def serialize(row: Any, link: PlanLink, *, summary: bool = False) -> dict[str, Any]:
    """Rebuild the canonical opportunity payload from row plus details.

    Columns carry what a query filters or orders on and ``details_json``
    carries the rest, so no fact is stored twice and nothing here computes
    a value the writer already decided.
    """
    details = detail_map(row)
    payload = {
        "opportunity_id": field(row, "opportunity_id"),
        "performance_model_version": field(row, "performance_model_version"),
        "biomarker_type": field(row, "biomarker_type"),
        "biomarker_types": details.get("biomarker_types", []),
        "boundary_kind": field(row, "boundary_kind"),
        "execution_context": field(row, "execution_context"),
        "terminal_sink": field(row, "terminal_sink"),
        "intervention_symbol": field(row, "intervention_symbol"),
        "intervention_kind": details.get("intervention_kind"),
        "file_path": field(row, "file_path"),
        "affected_call_sites_total": field(row, "affected_call_sites_total"),
        "affected_files_total": field(row, "affected_files_total"),
        "observations_total": field(row, "observations_total"),
        "confidence": field(row, "evidence_confidence"),
        "actionability_state": field(row, "actionability_state"),
        "rank_score": field(row, "rank_score"),
        "rank_position": field(row, "rank_position"),
        "why_ranked": details.get("why_ranked", []),
        # An older store has no per-group answer; its marker gives the old one.
        "may_lead": details.get(
            "may_lead", field(row, "biomarker_type") not in NON_LEADING_MARKERS
        ),
        "plan_id": link.row_id,
        "plan_reference": link.public_id,
        "plan_status": link.state,
        "plan_reason": link.reason,
        # Why it can wait; the stored rank already orders by loop size.
        "lower_priority": LOW_PRIORITY_LABEL.get(perf_low_priority(row) or ""),
    }
    if summary:
        return payload
    return {**payload, **_detail_fields(row, details)}


def _detail_fields(row: Any, details: dict[str, Any]) -> dict[str, Any]:
    """What the ``detail`` view adds to the summary payload."""
    fix_strategy = field(row, "fix_strategy")
    return {
        "terminal_sinks": details.get("terminal_sinks", []),
        "shared_path_suffix": details.get("shared_path_suffix", []),
        "resource_fingerprints": details.get("resource_fingerprints", []),
        "reliable_entry_reachability": details.get("reliable_entry_reachability"),
        "provenance": details.get("provenance"),
        "facets": details.get("facets", {}),
        "gain_text": gain_text(row, details.get("facets") or {}),
        "actionability_reason": details.get("actionability_reason"),
        "prerequisites": details.get("prerequisites", []),
        "rank_factors": details.get("rank_factors", {}),
        "siblings": details.get("siblings", []),
        **plan_brief(details.get("plan")),
        "fix": None
        if fix_strategy is None
        else {
            "strategy": fix_strategy,
            "safety": field(row, "fix_safety"),
            "rationale": details.get("fix_rationale") or "",
            **({"api": details["fix_api"]} if details.get("fix_api") else {}),
        },
    }


def gain_text(row: Any, facets: dict[str, Any]) -> str:
    """What fixing this cause buys, in the words the Fix-first item uses."""
    _problem, gain = perf_cost(
        "",
        field(row, "biomarker_type"),
        field(row, "boundary_kind"),
        facets.get("amplification"),
        facets.get("loop_magnitude"),
    )
    return gain


def plan_brief(plan: dict[str, Any] | None) -> dict[str, Any]:
    """The stored plan's validation, steps and economics, or nothing on an older store."""
    if not plan:
        return {}
    validation = plan.get("validation") or {}
    keys = ("basis", "via", "total", "tests", "reasons", "commands", "prerequisite")
    return {
        "validation": {key: validation.get(key) for key in keys},
        "plan_steps": plan.get("steps", []),
        "plan_economics": {
            key: plan.get(key) for key in ("effort_bucket", "benefit", "cost", "risk")
        },
    }


def summary_payload(row: Any | None) -> dict[str, Any]:
    """The canonical rollup for one stored summary row, or its absence.

    A missing row means this index has never materialized the analysis, which
    is a different answer from an empty one and must not read as a clean
    repository.
    """
    if row is None:
        return dict(SUMMARY_UNAVAILABLE)
    payload = json_field(row, "summary_json", {})
    if not isinstance(payload, dict):
        payload = {}
    materialized = field(row, "performance_model_version")
    stale = materialized != PERFORMANCE_MODEL_VERSION
    return {
        "status": "stale_model" if stale else "current",
        "performance_model_version": PERFORMANCE_MODEL_VERSION,
        "materialized_model_version": materialized,
        "analyzed_commit": field(row, "analyzed_commit"),
        "total": field(row, "opportunities_total"),
        "actionability": payload.get("actionability", {}),
        "context": payload.get("context", {}),
        "boundary": payload.get("boundary", {}),
        "proof": payload.get("proof", {}),
        "with_plan_total": payload.get("with_plan_total", 0),
        **({"default_queue": payload["default_queue"]} if "default_queue" in payload else {}),
        **(
            {"refresh_required": True, "detail": "Run repowise update to rescore."}
            if stale
            else {}
        ),
    }


def rescope_summary(
    base: dict[str, Any], groups: Iterable[Sequence[Any]], contexts: frozenset[str]
) -> dict[str, Any]:
    """Recount one stored rollup over *contexts*, from the facet aggregate.

    The same grouped counts the filter control is drawn from, summed a second
    way. Deriving the scoped headline here keeps one materialized row as the
    only stored rollup: a per-context rollup would be four more rows to write,
    version and keep honest for a number two sums recover exactly.
    """
    actionability: dict[str, int] = {}
    context: dict[str, int] = {}
    boundary: dict[str, int] = {}
    proof: dict[str, int] = {}
    total = 0
    with_plan = 0
    for execution_context, boundary_kind, _confidence, state, plan_state, cost, count in groups:
        if execution_context not in contexts:
            continue
        total += count
        actionability[state] = actionability.get(state, 0) + count
        context[execution_context] = context.get(execution_context, 0) + count
        key = boundary_kind or NULL_VALUE
        boundary[key] = boundary.get(key, 0) + count
        proof[cost] = proof.get(cost, 0) + count
        if plan_state == "available":
            with_plan += count
    return {
        **base,
        "total": total,
        "actionability": actionability,
        "context": context,
        "boundary": boundary,
        "proof": proof,
        "with_plan_total": with_plan,
    }


def unresolved_detail(state: dict[str, Any]) -> str:
    """Why an opportunity id did not resolve, from its ``model_state``."""
    if state["state"] == "stale_model":
        return (
            "That id was minted by performance model "
            f"{state['requested_model_version']}; this index is on model "
            f"{state['performance_model_version']}. Membership differs between "
            "models, so the id is not translated. Run repowise update to rescore."
        )
    if state["state"] == "unrecognized":
        return "That is not a performance opportunity id."
    return "No open or resolved opportunity in this repository carries that id."


def evidence_payload(row: Any) -> dict[str, Any]:
    """One public evidence entry, addressed by the finding's public id.

    The stored id when there is one, the same kernel recomputed when a store
    predates the column, and never the storage row id.
    """
    details = detail_map(row)
    return {
        "finding_id": field(row, "public_id") or finding_public_id(row),
        "file_path": field(row, "file_path"),
        "biomarker_type": field(row, "biomarker_type") or "",
        "function_name": field(row, "function_name"),
        "line_start": field(row, "line_start"),
        "line_end": field(row, "line_end"),
        "reason": field(row, "reason"),
        "path": list(details.get("path", ())),
        "provenance": details.get("resolution_basis", "direct"),
    }


__all__ = [
    "ACTIONABILITIES",
    "BOUNDARIES",
    "CANONICAL_CONTEXTS",
    "CANONICAL_SORTS",
    "CANONICAL_VIEWS",
    "CONFIDENCES",
    "DEFAULT_ACTIONABILITIES",
    "DEFAULT_CONTEXT",
    "DEFAULT_SORT",
    "FACETS",
    "FACET_FIELDS",
    "FILTERS",
    "PLAN_REASONS",
    "SORTS",
    "SUMMARY_UNAVAILABLE",
    "PerformanceContext",
    "PerformanceQuery",
    "PlanLink",
    "evidence_payload",
    "facet_counts",
    "fold_facets",
    "gain_text",
    "intervention_file",
    "keep",
    "parse_query",
    "plan_brief",
    "plan_link",
    "rescope_summary",
    "row_sort_key",
    "serialize",
    "sort_keys",
    "summary_payload",
    "unresolved_detail",
]
