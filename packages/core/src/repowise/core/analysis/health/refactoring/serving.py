"""Serving refactoring opportunities: the pure half, with no session.

Query parsing, the response shapes and the queue's sort, filter and facet
rules live here. The rules are data: the store builds its ``ORDER BY`` and
``WHERE`` from the same tables :func:`row_sort_key`, :func:`keep` and
:func:`facet_counts` read over rows already in memory, so the two cannot
disagree about what a queue holds or in which order.

Rows may be ORM rows or mappings; every read goes through ``rows.field``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from repowise.core.analysis.health import queue_rules
from repowise.core.analysis.health.queue_rules import Facet, FilterRule, SortKeys
from repowise.core.analysis.health.rows import detail_map, field

from .recommendations import _loads_dict

# ``refactoring_view`` predates the opportunity. Both old values keep working
# and both are documented in docs/layers/REFACTORING.md:
#
# - ``canonical``  the published rank order, ties and all. What the old default
#   produced, kept for a caller that wants the score order verbatim.
# - ``file_spread``  asked for one row per file. An opportunity *is* one file's
#   work, so the spread is now satisfied by construction; the value maps onto
#   the diversified order, which is what it was reaching for.
# - ``diversified``  the new default. Rank order round-robined over cause and
#   directory, because the ranked head is a genuine run of ties.
_VIEW_ORDERS: dict[str, str] = {
    "canonical": "rank",
    "file_spread": "queue",
    "diversified": "queue",
}
CANONICAL_VIEWS = tuple(_VIEW_ORDERS)
DEFAULT_VIEW = "diversified"

# The legacy plan list has no notion of the diversified order, so the new
# default resolves to the value that list has always defaulted to.
_PLAN_VIEWS = {"canonical": "canonical", "file_spread": "file_spread", "diversified": "canonical"}

# Which open opportunities a queue lists:
#
# - ``fix_first``  the default for a repository-wide open queue: only the
#   opportunities Fix first would take (its eligibility and exclusion rules,
#   read from the same builder), with the rest counted by reason.
# - ``all``  the full inventory. The default when the caller names files (a
#   file surface asks for its own work) or lists a triaged status, which Fix
#   first never reads.
SCOPES = ("fix_first", "all")
DEFAULT_SCOPE = "fix_first"

# The triage vocabulary, shared with health findings.
_STATUSES = ("open", "acknowledged", "resolved", "false_positive")

_CONFIDENCES = ("low", "medium", "high")
_EFFORTS = ("S", "M", "L", "XL")
_TYPES = (
    "break_cycle",
    "extract_class",
    "extract_helper",
    "extract_method",
    "move_method",
    "split_file",
)

UNAVAILABLE: dict[str, Any] = {
    "status": "unavailable",
    "reason": "no_refactoring_analysis",
    "detail": "No refactoring analysis is stored for this repository. Run `repowise update`.",
}

# ---------------------------------------------------------------------------
# The rules, as data
# ---------------------------------------------------------------------------

#: Orders the queue can be read in, as ``(field, descending)`` keys. Every one
#: ends in a unique column so the total order is deterministic and a deep
#: offset cannot repeat or skip a row. A descending field must be numeric.
SORTS: dict[str, SortKeys] = {
    "queue": (("queue_position", False),),
    "rank": (("rank_position", False),),
    "health": (("recoverable_health", True), ("rank_position", False)),
    "effort": (("step_count", False), ("rank_position", False)),
    "file": (("file_path", False), ("rank_position", False)),
}
DEFAULT_ORDER = "queue"
CANONICAL_ORDERS = tuple(SORTS)

#: In the order the store has always emitted its predicates.
FILTERS: tuple[FilterRule, ...] = (
    FilterRule("status", "status", "eq", "always"),
    # A scope resolved in Python (Fix first's eligible set).
    FilterRule("opportunity_ids", "opportunity_id", "in", "set"),
    # A list is how the board's "Structural" tab asks for four types at once.
    FilterRule("lead_types", "lead_refactoring_type", "in", "truthy"),
    FilterRule("confidence", "confidence", "eq", "set"),
    FilterRule("effort", "effort_bucket", "eq", "set"),
    FilterRule("file_paths", "file_path", "in", "set"),
    # The board's search box: a residual filter, case-insensitive.
    FilterRule("path_contains", "file_path", "contains", "truthy"),
    # A directory scope (the CLI's ``--module``).
    FilterRule("path_prefix", "file_path", "prefix", "truthy"),
    FilterRule("mechanical_only", "mechanical_steps", "positive", "truthy"),
    FilterRule("addresses_primary", "addresses_primary_problem", "is", "set"),
)

#: Facet name and the field it counts. No facet is cross-filtered: the counts
#: are over the status and scope alone.
FACETS: tuple[Facet, ...] = (
    ("lead_type", "lead_refactoring_type", None),
    ("effort", "effort_bucket", None),
    ("confidence", "confidence", None),
)
FACET_FIELDS = tuple(column for _, column, _ in FACETS)


def sort_keys(order: str | None) -> SortKeys:
    """The keys for *order*; an unknown order reads as the default."""
    return SORTS.get(order or DEFAULT_ORDER, SORTS[DEFAULT_ORDER])


def keep(row: Any, params: Any) -> bool:
    """Whether *row* passes every filter *params* sets (a query or a mapping)."""
    return queue_rules.keep(FILTERS, row, params)


def row_sort_key(row: Any, order: str | None = None) -> tuple[Any, ...]:
    """The sort key for *order*; an unknown order reads as the default."""
    return queue_rules.sort_key(sort_keys(order), row)


def fold_facets(groups: Iterable[Sequence[Any]]) -> dict[str, dict[str, int]]:
    """Fold ``(*facet values, count)`` groups into per-facet counts; a NULL is skipped."""
    return queue_rules.fold_facets(groups, FACETS)


def facet_counts(
    rows: Iterable[Any], *, status: str = "open", opportunity_ids: Sequence[str] | None = None
) -> dict[str, dict[str, int]]:
    """Counts for every facet over the rows a status (and scope) selects."""
    params = {"status": status, "opportunity_ids": opportunity_ids}
    return fold_facets(
        (*(field(row, column) for column in FACET_FIELDS), 1)
        for row in rows
        if keep(row, params)
    )


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefactoringQuery:
    """A normalized queue request. The only shape either adapter passes down."""

    lead_types: tuple[str, ...] | None = None
    status: str = "open"
    confidence: str | None = None
    effort: str | None = None
    mechanical_only: bool = False
    addresses_primary: bool | None = None
    file_paths: tuple[str, ...] | None = None
    path_contains: str | None = None
    path_prefix: str | None = None
    view: str = DEFAULT_VIEW
    order: str | None = None
    limit: int = 20
    offset: int = 0
    scope: str = DEFAULT_SCOPE

    @property
    def resolved_order(self) -> str:
        """An explicit ``order`` wins; otherwise the view picks one."""
        if self.order in CANONICAL_ORDERS:
            return self.order
        return _VIEW_ORDERS.get(self.view, _VIEW_ORDERS[DEFAULT_VIEW])


def parse_query(
    *,
    lead_type: str | Sequence[str] | None = None,
    status: str | None = None,
    confidence: str | None = None,
    effort: str | None = None,
    mechanical: bool | None = None,
    addresses_primary: bool | None = None,
    file_paths: list[str] | tuple[str, ...] | None = None,
    search: str | None = None,
    path_prefix: str | None = None,
    view: str | None = None,
    order: str | None = None,
    limit: int = 20,
    offset: int = 0,
    scope: str | None = None,
) -> tuple[RefactoringQuery, dict[str, str]]:
    """Normalize a caller's arguments, naming anything it had to discard.

    An unrecognized value is reported back rather than silently treated as "no
    filter": a caller who misspells a type should not be told the repository is
    clean.
    """
    ignored: dict[str, str] = {}

    def admit(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
        if value is None:
            return None
        if value in allowed:
            return value
        ignored[name] = value
        return None

    def admit_many(
        name: str, value: str | Sequence[str] | None, allowed: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        """One value or several. A comma-separated string is how a query
        parameter carries a set; every member is admitted on its own, so a
        misspelling in a list is reported rather than narrowing the result to
        the members that happened to be spelled right."""
        if value is None:
            return None
        raw = value.split(",") if isinstance(value, str) else list(value)
        kept = [item.strip() for item in raw if item.strip()]
        good = tuple(item for item in kept if item in allowed)
        bad = [item for item in kept if item not in allowed]
        if bad:
            ignored[name] = ",".join(bad)
        return good or None

    resolved_view = view or DEFAULT_VIEW
    if resolved_view not in _VIEW_ORDERS:
        ignored["refactoring_view"] = resolved_view
        resolved_view = DEFAULT_VIEW
    resolved_status = admit("status", status, _STATUSES) or "open"
    resolved_scope = admit("scope", scope, SCOPES) or (
        "all" if file_paths is not None else DEFAULT_SCOPE
    )
    if resolved_status != "open":
        resolved_scope = "all"  # Fix first reads open opportunities only.
    return (
        RefactoringQuery(
            lead_types=admit_many("refactoring_type", lead_type, _TYPES),
            status=resolved_status,
            confidence=admit("confidence", confidence, _CONFIDENCES),
            effort=admit("effort", effort, _EFFORTS),
            mechanical_only=bool(mechanical),
            addresses_primary=addresses_primary,
            # ``is not None``, not truthiness: an empty sequence is a scope that
            # resolved to no file, and the store turns that into ``IN ()``.
            # Reading it as "unscoped" answers a question about nothing with
            # the whole repository.
            file_paths=tuple(file_paths) if file_paths is not None else None,
            path_contains=(search or "").strip() or None,
            path_prefix=path_prefix or None,
            view=resolved_view,
            order=admit("order", order, CANONICAL_ORDERS),
            limit=max(int(limit), 0),
            offset=max(int(offset), 0),
            scope=resolved_scope,
        ),
        ignored,
    )


def plan_view(view: str | None) -> Literal["canonical", "file_spread"]:
    """The legacy plan list's view for a caller's ``refactoring_view``."""
    return _PLAN_VIEWS.get(view or DEFAULT_VIEW, "canonical")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def evidence_block(
    evidence: list[dict[str, Any]], total: int, offset: int
) -> dict[str, Any]:
    """One evidence page plus the exact call that reads the rest."""
    emitted = len(evidence)
    block: dict[str, Any] = {
        "evidence": evidence,
        "evidence_total": total,
        "evidence_emitted": emitted,
        "evidence_truncated": offset + emitted < total,
    }
    if block["evidence_truncated"]:
        block["evidence_reduced_reason"] = "evidence_page"
        block["evidence_next_cursor"] = offset + emitted
    return block


def summary_payload(row: Any | None) -> dict[str, Any]:
    """The Level-1 rollup from the stored summary row."""
    if row is None:
        return dict(UNAVAILABLE)
    payload = _loads_dict(field(row, "summary_json"))
    payload["status"] = "available"
    payload["refactoring_model_version"] = field(row, "refactoring_model_version")
    payload["analyzed_commit"] = field(row, "analyzed_commit")
    return payload


def directive_from_summary(row: Any | None) -> dict[str, Any]:
    """The Level-0 lead from the stored summary row: one opportunity, and the
    exact call that opens it."""
    if row is None:
        return dict(UNAVAILABLE)
    payload = _loads_dict(field(row, "summary_json"))
    lead = payload.get("lead")
    total = int(payload.get("opportunities_total") or 0)
    if not lead:
        # The finalizer never leads with a test file, so opportunities
        # without a lead are all in tests.
        return {
            "status": "clear",
            "reason": "only_test_file_opportunities" if total else "no_open_opportunities",
            "detail": (
                "Every open refactoring opportunity is in a test file; none leads."
                if total
                else "No refactoring opportunity is open for this repository."
            ),
            "opportunities_total": total,
        }
    addresses = lead.get("addresses_primary_problem")
    directive: dict[str, Any] = {
        "status": "available",
        "opportunity_id": lead.get("opportunity_id"),
        "fix_first": lead.get("file_path"),
        "reason": lead.get("lead_biomarker"),
        "lead_refactoring_type": lead.get("lead_refactoring_type"),
        "steps": lead.get("step_count"),
        "mechanical_steps": lead.get("mechanical_steps"),
        "judgment_steps": lead.get("judgment_steps"),
        "effort_bucket": lead.get("effort_bucket"),
        "confidence": lead.get("confidence"),
        "recovers_health_points": lead.get("recoverable_health"),
        "addresses_primary_problem": addresses,
        "opportunities_total": total,
        "next_action": {
            "tool": "get_health",
            "arguments": {"opportunity_id": lead.get("opportunity_id")},
        },
    }
    # The honest half. A file's plans very often answer a different question
    # from the one that made it the worst file, and saying so beats routing
    # an agent to cleanup it will read as the fix.
    if addresses is False:
        directive["note"] = (
            f"These steps do not address {lead.get('lead_biomarker')!r}, this file's "
            "dominant finding. Treat them as related cleanup, not the fix for it."
        )
    elif addresses is None:
        directive["note"] = (
            "No dominant finding was recorded for this file, so whether these steps "
            "address it is unknown rather than no."
        )
    return directive


def next_actions(row: Any, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structured follow-ups, so a drill-down ends somewhere rather than stops."""
    actions: list[dict[str, Any]] = []
    first = steps[0] if steps else None
    if first and first.get("line_start") is not None and first.get("line_end") is not None:
        actions.append(
            {
                "why": "read the span the first step names",
                "tool": "get_symbol",
                "arguments": {
                    "symbol_id": f"{first['file_path']}:{first['line_start']}-{first['line_end']}"
                },
            }
        )
    actions.append(
        {
            "why": "what history says about touching this file",
            "tool": "get_risk",
            "arguments": {"targets": [field(row, "file_path")]},
        }
    )
    if field(row, "evidence_total"):
        actions.append(
            {
                "why": "page the supporting observations",
                "tool": "get_health",
                "arguments": {
                    "opportunity_id": field(row, "opportunity_id"),
                    "only": ["refactoring_evidence"],
                },
            }
        )
    return actions


_ROW_FIELDS = (
    "opportunity_id",
    "refactoring_model_version",
    "status",
    "file_path",
    "lead_biomarker",
    "lead_refactoring_type",
    "addresses_primary_problem",
    "effort_bucket",
    "confidence",
    "step_count",
    "mechanical_steps",
    "judgment_steps",
    "evidence_total",
    "affected_files_total",
)


def serialize(
    row: Any, *, steps_limit: int | None = None, evidence_limit: int = 0
) -> dict[str, Any]:
    """One queue row: its columns, rank explanation, and optional step/evidence heads."""
    details = detail_map(row)
    steps = list(details.get("steps") or [])
    evidence = list(details.get("evidence") or [])
    payload: dict[str, Any] = {name: field(row, name) for name in _ROW_FIELDS}
    payload["recoverable_health"] = round(float(field(row, "recoverable_health")), 3)
    payload["rank_score"] = round(float(field(row, "rank_score")), 4)
    payload["rank_position"] = field(row, "rank_position")
    payload["queue_position"] = field(row, "queue_position")
    payload["rank_factors"] = details.get("rank_factors") or {}
    payload["why_ranked"] = details.get("why_ranked") or []
    # The file's own size and reach, recorded by the finalizer. Omitted when
    # the store predates them, so a surface can tell "not measured" from a
    # genuine zero rather than plotting an unmeasured file at the origin.
    for key in ("file_nloc", "dependents"):
        if isinstance(details.get(key), int):
            payload[key] = details[key]
    if steps_limit is not None:
        kept = steps[: max(steps_limit, 0)]
        payload["steps"] = kept
        payload["steps_total"] = len(steps)
        payload["steps_emitted"] = len(kept)
        if len(kept) < len(steps):
            payload["steps_reduced_reason"] = "limit"
    if evidence_limit:
        payload.update(evidence_block(evidence[:evidence_limit], len(evidence), 0))
    return payload


def stored_validation(owner: Any, public_id: str | None) -> Any:
    """The validation profile the finalizer resolved for this step.

    Test reachability is a graph walk over the whole unanswered set; it belongs
    at index time, and this reads its result rather than repeating it.
    """
    if owner is None or not public_id:
        return None
    details = detail_map(owner)
    wanted = next(
        (
            step.get("validation_profile_id")
            for step in (details.get("steps") or [])
            if step.get("plan_id") == public_id
        ),
        None,
    )
    if not wanted:
        return None
    profile = next(
        (p for p in (details.get("validation_profiles") or []) if p.get("id") == wanted),
        None,
    )
    return validation_from_profile(profile) if profile is not None else None


def validation_from_profile(profile: Mapping[str, Any]) -> Any:
    """Rebuild a stored validation profile into the plan dataclass."""
    from .recommendations import ValidationPlan, ValidationTarget

    target_fields = set(ValidationTarget.__dataclass_fields__)
    values = {
        key: value
        for key, value in profile.items()
        if key in ValidationPlan.__dataclass_fields__
    }
    values["targets"] = [
        ValidationTarget(**{k: v for k, v in target.items() if k in target_fields})
        for target in (profile.get("targets") or [])
        if isinstance(target, dict)
    ]
    return ValidationPlan(**values)


def plan_payload(row: Any) -> dict[str, Any]:
    """The stored plan, without re-hydrating rank or validation.

    Detail reads the payload the detector wrote and the finalizer already
    validated; recomputing benefit and coverage here is what made a one-row
    lookup cost the repository.
    """
    return {
        "id": field(row, "public_id") or field(row, "id"),
        "refactoring_type": field(row, "refactoring_type"),
        "file_path": field(row, "file_path"),
        "target_symbol": field(row, "target_symbol"),
        "line_start": field(row, "line_start"),
        "line_end": field(row, "line_end"),
        "plan": _loads_dict(field(row, "plan_json")),
        "evidence": _loads_dict(field(row, "evidence_json")),
        "blast_radius": _loads_dict(field(row, "blast_radius_json")),
        "impact_delta": field(row, "impact_delta"),
        "effort_bucket": field(row, "effort_bucket"),
        "confidence": field(row, "confidence"),
        "source_biomarker": field(row, "source_biomarker"),
        "status": field(row, "status"),
    }


__all__ = [
    "CANONICAL_ORDERS",
    "CANONICAL_VIEWS",
    "DEFAULT_ORDER",
    "DEFAULT_SCOPE",
    "DEFAULT_VIEW",
    "FACETS",
    "FACET_FIELDS",
    "FILTERS",
    "SCOPES",
    "SORTS",
    "UNAVAILABLE",
    "RefactoringQuery",
    "directive_from_summary",
    "evidence_block",
    "facet_counts",
    "fold_facets",
    "keep",
    "next_actions",
    "parse_query",
    "plan_payload",
    "plan_view",
    "row_sort_key",
    "serialize",
    "sort_keys",
    "stored_validation",
    "summary_payload",
    "validation_from_profile",
]
