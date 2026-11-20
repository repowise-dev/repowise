"""The read side of the savings ledger: the one report, and per-agent call times.

``load_agent_calls`` answers a narrower question from the same sidecar (when
did each agent last read the index), so it shares the read-only handle below
and adds no second aggregation of savings.

Before this, three consumers each aggregated and priced the ledger themselves:
the costs endpoint, the repository overview headline and ``repowise saved``.
They disagreed in at least four ways that mattered -- which ledger rows counted
as distillation, whether MCP tokens entered the dollar figure, whether an
output credit was added, and what time window applied -- so the same repository
reported three different savings and three different dollar values depending on
where you looked.

So this module is deliberately the only reader. It opens the sidecar read-only,
runs the bounded aggregates, and hands back a :class:`SavingsReport`. It does
no formatting and no presentation: a caller maps the report to its own output
shape, but never recomputes a number from its parts.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from repowise.core.savings.contracts import SavingsReport
from repowise.core.savings.formulas import CODEX_OUTPUT_CAP_TOKENS, TRANSCRIPT_ESTIMATOR
from repowise.core.savings.recorder import sidecar_path
from repowise.core.savings.repository import SavingsRepository

logger = logging.getLogger(__name__)


def load_report(
    repo_root: str | Path | None,
    *,
    as_of: datetime | None = None,
    days: int | None = None,
    max_breakdowns: int = 100,
) -> SavingsReport | None:
    """The savings report for *repo_root*, or ``None`` when unavailable.

    ``None`` means "no sidecar to read", which is the ordinary state of a
    repository that has never run a distill, hook or MCP call -- not an error
    and not zero. Callers distinguish the two: an absent report is "we have not
    measured anything here", a report of zero is "we measured, and it is zero".

    Read-only and never raises. Reporting runs inside a dashboard endpoint and
    a CLI command, neither of which should fail because a sidecar is locked,
    truncated or written by a newer build.
    """
    connection = _open_read_only(repo_root)
    if connection is None:
        return None
    moment = as_of or datetime.now(UTC)
    _cap_codex_backfill(connection)
    try:
        return SavingsRepository(connection).report(
            # The recorder files events under the path it wrote to, so the
            # reader has to ask with the same string or it reads an empty
            # repository and reports a confident zero. Both sides normalize
            # through Path for exactly that reason: a caller-supplied path can
            # carry a trailing separator or forward slashes on Windows, and the
            # server stores whatever a client registered, verbatim.
            str(Path(repo_root)),
            as_of=moment,
            days=days,
            max_breakdowns=max_breakdowns,
            fallback_rate=_cached_agent_rate(repo_root),
        )
    # OverflowError is not a ValueError: a large enough ``days`` overflows the
    # timedelta rather than failing validation, and this function promises not
    # to raise into a dashboard endpoint.
    except (sqlite3.Error, ValueError, OverflowError):
        return None
    finally:
        connection.close()


class AgentUsage(NamedTuple):
    """One agent's calls to an index: the first and last time, and how many."""

    first: str
    last: str
    calls: int


def load_agent_usage(repo_root: str | Path | None) -> dict[str, AgentUsage] | None:
    """``{agent slug: AgentUsage}`` for the calls that read *repo_root*'s index.

    ``None`` when there is no ledger to read, same contract as
    :func:`load_report`. ``unknown`` is the slug of calls whose client did not
    announce a name the identity registry knows.
    """
    connection = _open_read_only(repo_root)
    if connection is None:
        return None
    try:
        rows = SavingsRepository(connection).index_call_window(str(Path(repo_root)))
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return {
        str(agent): AgentUsage(str(first), str(last), int(calls))
        for agent, first, last, calls in rows
    }


def load_agent_calls(repo_root: str | Path | None) -> dict[str, tuple[str, str]] | None:
    """``{agent slug: (first, last)}`` times of the calls that read *repo_root*'s index."""
    usage = load_agent_usage(repo_root)
    return None if usage is None else {slug: (u.first, u.last) for slug, u in usage.items()}


def _cap_codex_backfill(connection: sqlite3.Connection) -> None:
    """Report Codex transcript events at today's cap, whenever they were recorded.

    A ledger can hold Codex backfill rows stored above
    ``CODEX_OUTPUT_CAP_TOKENS``, single events crediting hundreds of thousands
    of tokens. The rows stay as written; this shadows ``savings_events`` on this read-only handle with a temp view
    (SQLite resolves an unqualified name in ``temp`` before ``main``) that
    re-applies ``CODEX_OUTPUT_CAP_TOKENS`` exactly as the backfill does today:
    never below what was delivered. Every aggregate in the report then reads
    the capped figure, and a row already under the cap is untouched, so this
    is idempotent and agrees with a fresh backfill.

    Best-effort: on any failure the report reads the rows as written.
    """
    try:
        cap = int(CODEX_OUTPUT_CAP_TOKENS)
        # Cheapest test first: almost every row fails the integer comparison.
        over = (
            f"baseline_input_tokens > {cap} AND integration = 'codex' "
            f"AND estimator = '{TRANSCRIPT_ESTIMATOR}' AND result_state = 'success' "
            "AND delivered_input_tokens IS NOT NULL"
        )
        baseline = f"MAX(MIN(baseline_input_tokens, {cap}), delivered_input_tokens)"
        pre_budget = f"MAX(MIN(pre_budget_input_tokens, {cap}), delivered_input_tokens)"
        capped = {
            "baseline_input_tokens": baseline,
            "pre_budget_input_tokens": pre_budget,
            "dropped_input_tokens": f"MAX({pre_budget} - delivered_input_tokens, 0)",
            "saved_input_tokens": f"MAX({baseline} - delivered_input_tokens, 0)",
        }
        names = [row[1] for row in connection.execute("PRAGMA main.table_info(savings_events)")]
        # A view naming a missing column would fail every report query, which
        # loses the report; reading the rows uncapped is the better failure.
        required = {*capped, "integration", "estimator", "result_state", "delivered_input_tokens"}
        if not required <= set(names):
            return
        columns = ", ".join(
            f"CASE WHEN {over} THEN {capped[name]} ELSE {name} END AS {name}"
            if name in capped
            else name
            for name in names
        )
        connection.execute(
            f"CREATE TEMP VIEW savings_events AS SELECT {columns} FROM main.savings_events"
        )
    except Exception:
        logger.debug("savings report reads Codex events uncapped", exc_info=True)


#: ``pricing_source`` of the default rate a scan falls back to when it sighted
#: no model (``pricing._scan``).
_UNSIGHTED_SOURCE = "session_model:unknown"


def _cached_agent_rate(repo_root: str | Path) -> Callable[[str], float | None]:
    """An agent's input rate from the pricing cache, for report-time backfill.

    Cache only, never a transcript scan: the report runs inside a dashboard
    request. A cold cache backfills nothing, and the callers that warm it
    (the costs endpoint, ``repowise saved``) fill it for the next report. The
    default rate used when no model was sighted is not a model anyone ran, so
    it backfills nothing either.
    """
    from repowise.core.savings.pricing import resolve_pricing_snapshot

    def rate(agent: str) -> float | None:
        snapshot = resolve_pricing_snapshot(repo_root, allow_scan=False, agent=agent)
        if snapshot is None or snapshot.pricing_source == _UNSIGHTED_SOURCE:
            return None
        return snapshot.input_rate_usd_per_million

    return rate


def _open_read_only(repo_root: str | Path | None) -> sqlite3.Connection | None:
    """A read-only handle on the sidecar, or ``None`` when there is none."""
    if repo_root is None:
        return None
    database = sidecar_path(repo_root)
    if not database.is_file():
        return None
    try:
        # A read-only URI handle never contends with the hook and MCP writers,
        # and never runs the schema upgrade a read path has no business doing.
        return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
