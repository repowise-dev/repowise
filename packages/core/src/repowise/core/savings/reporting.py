"""Pure report construction over already-normalized domain records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
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
    """Shape ``(group, events, saved_input_tokens, priced_input_usd)`` rows into report rows.

    Shared with :mod:`repowise.core.savings.repository` so the SQL path and the
    pure path cannot drift into two different row shapes. They are meant to be
    field-for-field identical, and the only way to keep that true is for one of
    them to own the shaping.
    """
    return tuple(
        {
            key: row[0],
            "events": int(row[1]),
            "saved_input_tokens": int(row[2]),
            "priced_input_savings_usd": float(row[3]),
        }
        for row in rows
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


#: The MCP tools an agent runs as a check before changing code, in the order a
#: page lists them. Their events carry an ``outcome`` (see
#: :func:`~repowise.core.savings.contracts.normalize_outcome`).
CHECK_TOOLS = ("get_change_risk", "get_why", "get_risk", "get_health", "get_dead_code")

#: Fields a check row reports per tool. The rest stay null on that row.
_CHECK_FIELDS: dict[str, tuple[str, ...]] = {
    "get_change_risk": ("high", "medium", "low"),
    "get_risk": ("high", "medium", "low"),
    "get_why": ("decisions_accepted", "decisions_candidate", "decisions_none"),
    "get_health": ("findings", "files"),
    "get_dead_code": ("items", "high_confidence"),
}
_CHECK_ROW_FIELDS = tuple(dict.fromkeys(f for fields in _CHECK_FIELDS.values() for f in fields))
_SUMMED_COUNTS = ("high", "medium", "low", "findings", "files", "items", "high_confidence")


@dataclass
class CheckTally:
    """One check tool's calls and summed outcomes, before shaping."""

    calls: int = 0
    outcome_calls: int = 0
    sums: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add_outcome(self, outcome: Mapping[str, Any], tool: str, calls: int = 1) -> None:
        """Fold in *calls* calls that returned *outcome*. Calls are counted apart."""
        self.outcome_calls += calls
        if tool == "get_change_risk":
            # A change has one level; it counts once under it.
            level = outcome.get("level")
            if level:
                self.sums[level] += calls
        else:
            for key in _SUMMED_COUNTS:
                self.sums[key] += int(outcome.get(key) or 0) * calls
        decision = outcome.get("decision")
        if decision:
            self.sums[f"decisions_{decision}"] += calls


def check_rows(tallies: Mapping[str, CheckTally]) -> tuple[Mapping[str, Any], ...]:
    """One row per check tool, zero calls included, so every surface lists all five.

    Outcome fields are null on a row with no recorded outcome (calls made
    before outcomes were kept, or a host that records none), and null where a
    field does not apply to that tool.
    """
    rows = []
    for tool in CHECK_TOOLS:
        tally = tallies.get(tool) or CheckTally()
        recorded = tally.outcome_calls > 0
        row: dict[str, Any] = {
            "tool": tool,
            "calls": tally.calls,
            "outcome_calls": tally.outcome_calls,
        }
        for name in _CHECK_ROW_FIELDS:
            applies = recorded and name in _CHECK_FIELDS[tool]
            row[name] = int(tally.sums.get(name, 0)) if applies else None
        rows.append(row)
    return tuple(rows)


#: Surfaces whose events are tool calls Repowise answered: an MCP or VS Code
#: tool call, or a hook that replaced a tool result. Distill rows are command
#: output, recorded by the CLI or mined from transcripts.
TOOL_CALL_SURFACES = ("mcp", "vscode_lm", "hook")


@dataclass(frozen=True, slots=True)
class FilesNotOpened:
    files: int
    tokens: int
    tool_calls: int
    per_day: tuple[Mapping[str, Any], ...]
    per_agent: tuple[Mapping[str, Any], ...]
    per_surface: tuple[Mapping[str, Any], ...]


def files_not_opened(
    credited: Iterable[tuple[Any, ...]], calls: Iterable[tuple[Any, ...]], limit: int
) -> FilesNotOpened:
    """Totals and breakdowns from ``(day, agent, surface, n, tokens)`` credited
    rows and ``(day, agent, surface, n)`` tool-call rows.

    Shared by both report builders, like :func:`breakdown_rows`, so the SQL and
    pure paths cannot shape these rows two ways.
    """
    cells: dict[str, dict[Any, list[int]]] = {
        name: defaultdict(lambda: [0, 0, 0]) for name in ("day", "agent", "surface")
    }
    for day, agent, surface, count, tokens in credited:
        for name, key in (("day", day), ("agent", agent), ("surface", surface)):
            cell = cells[name][key]
            cell[0] += int(count)
            cell[1] += int(tokens or 0)
    for day, agent, surface, count in calls:
        for name, key in (("day", day), ("agent", agent), ("surface", surface)):
            cells[name][key][2] += int(count)

    def rows(name: str, ordered: list[tuple[Any, list[int]]]) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            {
                name: key,
                "files_not_opened": cell[0],
                "files_not_opened_tokens": cell[1],
                "tool_calls_answered": cell[2],
            }
            for key, cell in ordered
        )

    def ranked(name: str) -> list[tuple[Any, list[int]]]:
        return sorted(
            cells[name].items(), key=lambda item: (-item[1][0], -item[1][2], item[0] or "")
        )[:limit]

    days = sorted(cells["day"].items(), reverse=True)[:DAY_LIMIT][::-1]
    totals = [sum(cell[i] for cell in cells["day"].values()) for i in range(3)]
    return FilesNotOpened(
        files=totals[0],
        tokens=totals[1],
        tool_calls=totals[2],
        per_day=rows("day", days),
        per_agent=rows("agent", ranked("agent")),
        per_surface=rows("surface", ranked("surface")),
    )


def _credited_files(
    scoped: list[SavingsEvent], file_reads: Iterable[tuple[str, str, str]]
) -> list[tuple[Any, ...]]:
    """The pure path's credited rows: first event per (session, path) owns it.

    *file_reads* are ``(session_key, path, read_at)`` rows; a Read after the
    owning event cancels the credit. Mirrors the SQL in the repository.
    """
    latest_read = {(key, path): at for key, path, at in file_reads}
    owners: dict[tuple[str, str], tuple[SavingsEvent, int | None]] = {}
    for event in sorted(scoped, key=lambda item: (item.occurred_at, item.event_id)):
        if not event.is_usable:
            continue
        for path, tokens in event.avoided_files:
            owners.setdefault((event.file_session_key or event.event_id, path), (event, tokens))
    credited: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    for (key, path), (event, tokens) in owners.items():
        read_at = latest_read.get((key, path))
        if read_at is not None and read_at > event.occurred_at:
            continue
        cell = credited[(event.occurred_at[:10], event.agent, event.surface)]
        cell[0] += 1
        cell[1] += tokens or 0
    return [(*group, cell[0], cell[1]) for group, cell in credited.items()]


def _included(occurred_at: str, cutoff: datetime | None, as_of: datetime) -> bool:
    timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    return timestamp <= as_of and (cutoff is None or timestamp >= cutoff)


def _reduction(scoped: list[SavingsEvent]) -> tuple[int, int, int, int, float | None]:
    """Coverage, both sides of the aggregate ratio, and its percentile.

    Two populations. ``pairs`` is every event with something to compare
    against -- an event with no baseline is not a reduction of nought, so it is
    in neither. ``reducing`` is the subset where the input actually got
    smaller, and the ratio is over that: "when it fires, by how much". Both
    counts are returned because the ratio only stays honest while the surface
    states its coverage.

    The denominator is what each event's saving was computed against, which is
    not always its ``baseline_input_tokens`` -- see
    :func:`reduction_denominator`.
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
    reducing = [(event, den) for event, den in pairs if event.saved_input_tokens > 0]
    ratios = sorted(event.saved_input_tokens / den for event, den in reducing)
    return (
        len(pairs),
        len(reducing),
        sum(den for _event, den in reducing),
        sum(event.saved_input_tokens for event, _den in reducing),
        ratios[reduction_quantile_offset(len(ratios))] if ratios else None,
    )


#: Report-time rate for an unpriced event's agent, or None. See
#: :func:`backfill_usd`.
FallbackRate = Callable[[str], float | None]


def backfill_usd(
    groups: Iterable[tuple[str, float | None, int]], fallback_rate: FallbackRate | None
) -> tuple[int, float]:
    """Price ``(agent, session rate, unpriced tokens)`` groups at report time.

    Shared by both report builders, so the rule has one owner: the session's
    own captured rate first, else *fallback_rate* for the agent, else nothing.
    Returns the tokens priced and their value.
    """
    tokens = 0
    usd = 0.0
    for agent, session_rate, saved in groups:
        rate = session_rate if session_rate is not None else (
            fallback_rate(agent) if fallback_rate is not None else None
        )
        if rate is None or saved <= 0:
            continue
        tokens += saved
        usd += saved * rate / 1_000_000
    return tokens, usd


def _session_rates(events: Iterable[SavingsEvent]) -> dict[str, float]:
    """Each session's newest captured input rate; ties go to the higher rate."""
    newest: dict[str, tuple[str, float]] = {}
    for event in events:
        rate = event.input_rate_usd_per_million
        if event.session_id is None or rate is None:
            continue
        key = (event.occurred_at, rate)
        if event.session_id not in newest or key > newest[event.session_id]:
            newest[event.session_id] = key
    return {session: rate for session, (_at, rate) in newest.items()}


def _check_rows_from(scoped: Iterable[SavingsEvent]) -> tuple[Mapping[str, Any], ...]:
    """The pure path's check rows, tallied exactly as the SQL path tallies them."""
    tallies: dict[str, CheckTally] = {}
    for event in scoped:
        if event.surface != "mcp" or not event.is_usable or event.operation not in CHECK_TOOLS:
            continue
        tally = tallies.setdefault(event.operation, CheckTally())
        tally.calls += 1
        if event.outcome is not None:
            tally.add_outcome(event.outcome, event.operation)
    return check_rows(tallies)


def build_report(
    events: Iterable[SavingsEvent],
    opportunities: Iterable[OpportunityObservation] = (),
    *,
    as_of: datetime,
    days: int | None = None,
    max_breakdowns: int = 100,
    file_reads: Iterable[tuple[str, str, str]] = (),
    fallback_rate: FallbackRate | None = None,
) -> SavingsReport:
    """Build a hand-checkable report while keeping opportunities separate.

    *file_reads* are ``(session_key, path, read_at)`` rows for the
    files-not-opened clawback, as the ledger stores them.
    """
    from repowise.core.savings.output_estimate import estimate_output, session_groups

    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if days is not None and days < 0:
        raise ValueError("days cannot be negative")
    cutoff = as_of - timedelta(days=days) if days is not None else None
    events = list(events)
    scoped = [event for event in events if _included(event.occurred_at, cutoff, as_of)]
    scoped_opportunities = [
        item for item in opportunities if _included(item.occurred_at, cutoff, as_of)
    ]
    groups: dict[str, dict[Any, list[float]]] = {
        name: defaultdict(lambda: [0, 0, 0.0])
        for name in ("operation", "surface", "agent", "model", "day")
    }
    input_usd = 0.0
    output_usd = 0.0
    priced_input = 0
    priced_output = 0
    output_evidence_events = 0
    for event in scoped:
        input_price = price_tokens(event.saved_input_tokens, event.input_rate_usd_per_million)
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
            bucket[2] += input_price or 0.0
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
    baseline_events, reducing_events, baseline_input, baseline_saved, p90 = _reduction(
        scoped
    )
    limit = max(0, min(max_breakdowns, 100))

    def ranked(name: str) -> list[tuple[Any, ...]]:
        """Sorted ``(group, events, saved, usd)`` rows, matching the SQL order.

        ``-saved`` then group ascending, with a null group first among ties --
        SQLite sorts NULL first on an ascending key, and an unpriced event has
        a null model, so this is the tie the two builders actually hit.
        """
        return [
            (group, values[0], values[1], values[2])
            for group, values in sorted(
                groups[name].items(),
                key=lambda item: (-item[1][1], item[0] is not None, item[0] or ""),
            )[:limit]
        ]

    # Ordered by day ascending, capped to the most recent days, matching the
    # SQL path's newest-first-then-reversed slice.
    day_rows = [
        (group, values[0], values[1], values[2])
        for group, values in sorted(groups["day"].items(), reverse=True)[:DAY_LIMIT]
    ][::-1]
    opportunity_kinds: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in scoped_opportunities:
        opportunity_kinds[item.kind][0] += 1
        opportunity_kinds[item.kind][1] += item.estimated_potential_input_tokens
    occurrences = sorted(event.occurred_at for event in scoped)
    session_rates = _session_rates(events)
    backfilled_tokens, backfilled_usd = backfill_usd(
        (
            (event.agent, session_rates.get(event.session_id or ""), event.saved_input_tokens)
            for event in scoped
            if event.input_rate_usd_per_million is None
        ),
        fallback_rate,
    )
    in_subagent = [event for event in scoped if event.subagent_id is not None]

    def answered(event: SavingsEvent) -> bool:
        return event.result_state == "success" or (
            event.result_state == "partial" and event.is_usable
        )

    files = files_not_opened(
        _credited_files(scoped, file_reads),
        [
            (event.occurred_at[:10], event.agent, event.surface, 1)
            for event in scoped
            if event.surface in TOOL_CALL_SURFACES and answered(event)
        ],
        limit,
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
        reducing_events=reducing_events,
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
        files_not_opened=files.files,
        files_not_opened_tokens=files.tokens,
        tool_calls_answered=files.tool_calls,
        files_per_day=files.per_day,
        files_per_agent=files.per_agent,
        files_per_surface=files.per_surface,
        subagent_events=len(in_subagent),
        subagent_saved_input_tokens=sum(event.saved_input_tokens for event in in_subagent),
        backfilled_saved_input_tokens=backfilled_tokens,
        backfilled_input_savings_usd=backfilled_usd,
        output_estimate=estimate_output(session_groups(scoped)),
        checks=_check_rows_from(scoped),
    )
