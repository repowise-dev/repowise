"""Pure report construction over already-normalized domain records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta

from repowise.core.savings.contracts import OpportunityObservation, SavingsEvent, SavingsReport
from repowise.core.savings.pricing import price_tokens


def _included(occurred_at: str, cutoff: datetime | None, as_of: datetime) -> bool:
    timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    return timestamp <= as_of and (cutoff is None or timestamp >= cutoff)


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
    per_operation: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    input_usd = 0.0
    output_usd = 0.0
    priced_input = 0
    priced_output = 0
    output_evidence_events = 0
    for event in scoped:
        per_operation[event.operation][0] += 1
        per_operation[event.operation][1] += event.saved_input_tokens
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
    breakdown = tuple(
        {"operation": operation, "events": values[0], "saved_input_tokens": values[1]}
        for operation, values in sorted(
            per_operation.items(), key=lambda item: (-item[1][1], item[0])
        )[: max(0, min(max_breakdowns, 100))]
    )

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
        opportunity_count=len(scoped_opportunities),
        opportunity_tokens_excluded=sum(
            item.estimated_potential_input_tokens for item in scoped_opportunities
        ),
        per_operation=breakdown,
    )
