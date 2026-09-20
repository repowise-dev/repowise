"""Pure report construction over already-normalized domain records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent, SavingsReport
from repowise.core.savings.formulas import (
    reduction_denominator,
    reduction_quantile_offset,
    reduction_ratio,
)
from repowise.core.savings.pricing import price_tokens

#: Day-series cap, shared by both report builders. A year of daily rows is
#: already more than any surface plots; the point is that an all-time window
#: cannot return an unbounded payload just because the repository is old.
DAY_LIMIT = 366


def breakdown_rows(key: str, rows: Iterable[tuple[Any, ...]]) -> tuple[Mapping[str, Any], ...]:
    """Shape ``(group, events, saved_input_tokens)`` triples into report rows.

    Shared with :mod:`repowise.core.savings.repository` so the SQL path and the
    pure path cannot drift into two different row shapes. They are meant to be
    field-for-field identical, and the only way to keep that true is for one of
    them to own the shaping.
    """
    return tuple(
        {key: row[0], "events": int(row[1]), "saved_input_tokens": int(row[2])} for row in rows
    )


def agent_breakdown_rows(rows: Iterable[tuple[Any, ...]]) -> tuple[Mapping[str, Any], ...]:
    """Agent rows, carrying the display name beside the stored slug.

    Resolved here rather than in each consumer: the identity registry is the
    one place an agent's label is written, and there were three separate label
    maps before this, one of which spelled Codex CLI differently from the
    registry. An unregistered slug comes back verbatim, which is how a retired
    agent still in the ledger stays readable.
    """
    from repowise.core.agents.identity import display_name_for

    return tuple(
        {
            "agent": row[0],
            "agent_display_name": display_name_for(row[0]) if row[0] else None,
            "events": int(row[1]),
            "saved_input_tokens": int(row[2]),
        }
        for row in rows
    )


def _included(occurred_at: str, cutoff: datetime | None, as_of: datetime) -> bool:
    timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    return timestamp <= as_of and (cutoff is None or timestamp >= cutoff)


def _reduction(scoped: list[SavingsEvent]) -> tuple[int, int, int, float | None]:
    """Population, both sides of the aggregate ratio, and its percentile.

    Only events carrying a baseline: an event with nothing to compare against
    is not a reduction of nought, so it leaves both sides alone. The
    denominator is what each event's saving was computed against, which is not
    always its ``baseline_input_tokens`` -- see :func:`reduction_denominator`.
    """
    pairs = [
        (event, denominator)
        for event in scoped
        if (
            denominator := reduction_denominator(
                surface=event.surface,
                evidence_kind=event.evidence_kind,
                baseline_input_tokens=event.baseline_input_tokens,
                pre_budget_input_tokens=event.pre_budget_input_tokens,
            )
        )
        is not None
    ]
    ratios = sorted(event.saved_input_tokens / denominator for event, denominator in pairs)
    return (
        len(pairs),
        sum(denominator for _event, denominator in pairs),
        sum(event.saved_input_tokens for event, _denominator in pairs),
        ratios[reduction_quantile_offset(len(ratios))] if ratios else None,
    )


def build_report(
    events: Iterable[SavingsEvent],
    opportunities: Iterable[OpportunityObservation] = (),
    *,
    as_of: datetime,
    days: int | None = None,
    max_breakdowns: int = 100,
) -> SavingsReport:
    """Build a hand-checkable report while keeping opportunities separate."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if days is not None and days < 0:
        raise ValueError("days cannot be negative")
    cutoff = as_of - timedelta(days=days) if days is not None else None
    scoped = [event for event in events if _included(event.occurred_at, cutoff, as_of)]
    scoped_opportunities = [
        item for item in opportunities if _included(item.occurred_at, cutoff, as_of)
    ]
    groups: dict[str, dict[Any, list[int]]] = {
        name: defaultdict(lambda: [0, 0])
        for name in ("operation", "surface", "agent", "model", "day")
    }
    input_usd = 0.0
    output_usd = 0.0
    priced_input = 0
    priced_output = 0
    output_evidence_events = 0
    for event in scoped:
        for name, value in (
            ("operation", event.operation),
            ("surface", event.surface),
            ("agent", event.agent),
            ("model", event.model),
            ("day", event.occurred_at[:10]),
        ):
            bucket = groups[name][value]
            bucket[0] += 1
            bucket[1] += event.saved_input_tokens
        input_price = price_tokens(event.saved_input_tokens, event.input_rate_usd_per_million)
        if input_price is not None:
            priced_input += event.saved_input_tokens
            input_usd += input_price
        if event.saved_output_tokens is not None:
            output_evidence_events += 1
            output_price = price_tokens(
                event.saved_output_tokens, event.output_rate_usd_per_million
            )
            if output_price is not None:
                priced_output += event.saved_output_tokens
                output_usd += output_price
    saved_input = sum(event.saved_input_tokens for event in scoped)
    saved_output = sum(event.saved_output_tokens or 0 for event in scoped)
    baseline_events, baseline_input, baseline_saved, p90 = _reduction(scoped)
    limit = max(0, min(max_breakdowns, 100))

    def ranked(name: str) -> list[tuple[Any, ...]]:
        """Sorted ``(group, events, saved)`` triples, matching the SQL order.

        ``-saved`` then group ascending, with a null group first among ties --
        SQLite sorts NULL first on an ascending key, and an unpriced event has
        a null model, so this is the tie the two builders actually hit.
        """
        return [
            (group, values[0], values[1])
            for group, values in sorted(
                groups[name].items(),
                key=lambda item: (-item[1][1], item[0] is not None, item[0] or ""),
            )[:limit]
        ]

    # Ordered by day ascending, capped to the most recent days, matching the
    # SQL path's newest-first-then-reversed slice.
    day_rows = [
        (group, values[0], values[1])
        for group, values in sorted(groups["day"].items(), reverse=True)[:DAY_LIMIT]
    ][::-1]
    opportunity_kinds: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in scoped_opportunities:
        opportunity_kinds[item.kind][0] += 1
        opportunity_kinds[item.kind][1] += item.estimated_potential_input_tokens
    occurrences = sorted(event.occurred_at for event in scoped)

    def answered(event: SavingsEvent) -> bool:
        return event.result_state == "success" or (
            event.result_state == "partial" and event.is_usable
        )

    return SavingsReport(
        unique_events=len(scoped),
        successful_or_usable_partial_events=sum(answered(event) for event in scoped),
        saving_interactions=sum(event.saved_input_tokens > 0 for event in scoped),
        mcp_queries_answered=sum(event.surface == "mcp" and answered(event) for event in scoped),
        dead_ends=sum(event.result_state == "dead_end" for event in scoped),
        saved_input_tokens=saved_input,
        measured_saved_input_tokens=sum(
            event.saved_input_tokens for event in scoped if event.evidence_kind == "measured"
        ),
        inferred_saved_input_tokens=sum(
            event.saved_input_tokens for event in scoped if event.evidence_kind == "inferred"
        ),
        priced_saved_input_tokens=priced_input,
        unpriced_saved_input_tokens=saved_input - priced_input,
        priced_input_savings_usd=input_usd,
        saved_output_tokens=saved_output if output_evidence_events else None,
        priced_saved_output_tokens=priced_output,
        unpriced_saved_output_tokens=saved_output - priced_output,
        priced_output_savings_usd=output_usd,
        baseline_events=baseline_events,
        baseline_input_tokens=baseline_input,
        baseline_saved_input_tokens=baseline_saved,
        input_reduction_ratio=reduction_ratio(baseline_saved, baseline_input),
        input_reduction_ratio_p90=p90,
        opportunity_count=len(scoped_opportunities),
        opportunity_tokens_excluded=sum(
            item.estimated_potential_input_tokens for item in scoped_opportunities
        ),
        per_operation=breakdown_rows("operation", ranked("operation")),
        per_surface=breakdown_rows("surface", ranked("surface")),
        per_agent=agent_breakdown_rows(ranked("agent")),
        per_model=breakdown_rows("model", ranked("model")),
        per_day=breakdown_rows("day", day_rows),
        per_opportunity_kind=tuple(
            {
                "kind": kind,
                "observations": values[0],
                "estimated_potential_input_tokens": values[1],
            }
            for kind, values in sorted(
                opportunity_kinds.items(), key=lambda item: (-item[1][1], item[0])
            )[:limit]
        ),
        first_event_at=occurrences[0] if occurrences else None,
        last_event_at=occurrences[-1] if occurrences else None,
    )
