"""Short transactional writes and bounded aggregate reads for savings data."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from repowise.core.savings.contracts import (
    OpportunityObservation,
    SavingsEvent,
    SavingsReport,
    utc_text,
)
from repowise.core.savings.formulas import (
    REDUCTION_DENOMINATOR_SQL,
    reduction_quantile_offset,
    reduction_ratio,
)
from repowise.core.savings.reporting import (
    CHECK_TOOLS,
    DAY_LIMIT,
    TOOL_CALL_SURFACES,
    CheckTally,
    FallbackRate,
    agent_breakdown_rows,
    backfill_usd,
    breakdown_rows,
    check_rows,
    files_not_opened,
)
from repowise.core.savings.schema import is_newer_store

if TYPE_CHECKING:
    from repowise.core.savings.output_estimate import OutputEstimate

logger = logging.getLogger(__name__)

#: Kept under SQLite's 999-variable ceiling with room to spare.
_REF_QUERY_BATCH = 400

#: Cap on the ``(day, agent, surface)`` cells the files-not-opened queries
#: return: a year of days across every agent and surface fits several times.
_FILE_CELL_LIMIT = 20_000

#: Distinct ``(tool, outcome)`` groups one page of the check-row read folds.
#: Real ledgers hold a few hundred, so this is one page; a larger window pages
#: on, so the sums stay exact while each statement stays bounded.
_CHECK_OUTCOME_PAGE = 5_000

_ANSWERED_SQL = "(result_state = 'success' OR (result_state = 'partial' AND is_usable = 1))"
#: Unpriced ``(agent, session)`` groups the report-time price backfill reads. A
#: ledger with more than this leaves the smallest of them unpriced.
_BACKFILL_GROUP_LIMIT = 10_000

_EVENT_COLUMNS = (
    "event_id",
    "schema_version",
    "idempotency_key",
    "occurred_at",
    "repository_id",
    "surface",
    "integration",
    "agent",
    "subagent_id",
    "session_id",
    "request_id",
    "tool_call_id",
    "operation",
    "evidence_kind",
    "confidence",
    "estimator",
    "token_unit",
    "result_state",
    "is_usable",
    "baseline_input_tokens",
    "pre_budget_input_tokens",
    "delivered_input_tokens",
    "dropped_input_tokens",
    "saved_input_tokens",
    "baseline_output_tokens",
    "delivered_output_tokens",
    "saved_output_tokens",
    "model",
    "currency",
    "pricing_source",
    "pricing_version",
    "input_rate_usd_per_million",
    "output_rate_usd_per_million",
    "metadata_json",
    "outcome_json",
)


class SavingsRepository:
    """Repository over an initialized omissions-sidecar connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._newer: bool | None = None

    def _read_only(self) -> bool:
        """Whether a newer Repowise owns this store, so every write here is dropped.

        Checked once per repository, which callers create per write: one
        ``PRAGMA`` read, on the writes only.
        """
        if self._newer is None:
            self._newer = is_newer_store(self._conn)
            if self._newer:
                logger.debug("savings sidecar is newer than this build; ledger writes dropped")
        return self._newer

    def _grouped(
        self,
        expression: str,
        where: str,
        params: list[Any],
        limit: int,
        *,
        order: str = "3 DESC, 1 ASC",
    ) -> list[tuple[Any, ...]]:
        """One bounded ``GROUP BY`` over the scoped events.

        *expression* and *order* are SQL fragments and are only ever called
        with literals written in this module -- never with a caller's value.
        The window filter rides ``idx_savings_events_repo_time``; the grouping
        sorts the matched slice, which is why every one of these is capped.
        """
        return self._conn.execute(
            f"SELECT {expression}, COUNT(*), COALESCE(SUM(saved_input_tokens), 0), "
            "COALESCE(SUM(CASE WHEN input_rate_usd_per_million IS NOT NULL "
            "THEN saved_input_tokens * input_rate_usd_per_million / 1000000.0 ELSE 0 END), 0) "
            f"FROM savings_events WHERE {where} "
            f"GROUP BY 1 ORDER BY {order} LIMIT ?",
            [*params, limit],
        ).fetchall()

    def index_call_window(self, repository_id: str) -> list[tuple[Any, ...]]:
        """``(agent, first, last, calls)`` per agent for calls that read the index, latest first.

        MCP tool calls and VS Code language-model tool calls; distill and hook
        rows are output an agent ran, not questions it asked. Bounded like
        every other grouping here.
        """
        return self._conn.execute(
            "SELECT agent, MIN(occurred_at), MAX(occurred_at), COUNT(*) FROM savings_events "
            "WHERE repository_id = ? AND surface IN ('mcp', 'vscode_lm') "
            "GROUP BY agent ORDER BY 3 DESC LIMIT 100",
            [repository_id],
        ).fetchall()

    def recorded_omission_refs(self, refs: Sequence[str]) -> set[str]:
        """Which of *refs* an event already claims, via ``idx_..._omissions_ref``.

        The idempotency key cannot answer this for a producer reading a
        transcript: a live event's key is seeded on a random id, so it carries
        no content identity. The omission ref does, and it is the same 12 hex
        characters in the ledger and in the marker the agent was shown.
        """
        found: set[str] = set()
        for start in range(0, len(refs), _REF_QUERY_BATCH):
            batch = refs[start : start + _REF_QUERY_BATCH]
            placeholders = ",".join("?" for _ in batch)
            found.update(
                row[0]
                for row in self._conn.execute(
                    "SELECT DISTINCT omission_ref FROM savings_event_omissions "
                    f"WHERE omission_ref IN ({placeholders})",
                    list(batch),
                )
            )
        return found

    def record_event(self, event: SavingsEvent) -> bool:
        """Atomically insert an event and its links; return false for a retry."""
        if self._read_only():
            return False
        values = [getattr(event, column) for column in _EVENT_COLUMNS[:-2]]
        values[18] = int(event.is_usable)
        values.append(event.metadata_json())
        values.append(event.outcome_json())
        placeholders = ",".join("?" for _ in _EVENT_COLUMNS)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._conn.execute(
                f"INSERT OR IGNORE INTO savings_events ({','.join(_EVENT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                values,
            )
            if cursor.rowcount == 0:
                existing = self._conn.execute(
                    f"SELECT {','.join(_EVENT_COLUMNS[1:])} FROM savings_events "
                    "WHERE repository_id = ? AND idempotency_key = ?",
                    (event.repository_id, event.idempotency_key),
                ).fetchone()
                if existing is None or tuple(existing) != tuple(values[1:]):
                    raise ValueError("idempotency key conflicts with a different event payload")
                self._conn.commit()
                return False
            self._conn.executemany(
                "INSERT INTO savings_event_omissions(event_id, omission_ref) VALUES (?, ?)",
                ((event.event_id, ref) for ref in event.omission_refs),
            )
            if event.avoided_files:
                self._insert_files(event)
            correlations = {
                "session": event.session_id,
                "request": event.request_id,
                "tool_call": event.tool_call_id,
            }
            self._conn.executemany(
                "INSERT INTO savings_event_correlations(event_id, kind, value_hash) "
                "VALUES (?, ?, ?)",
                (
                    (event.event_id, kind, value)
                    for kind, value in correlations.items()
                    if value is not None
                ),
            )
            self._conn.commit()
            return True
        except BaseException:
            self._conn.rollback()
            raise

    def _insert_files(self, event: SavingsEvent) -> None:
        """The event's avoided files, inside its transaction. Never fails the event.

        A sidecar opened on a raw connection may predate the files table; the
        failed statement rolls back alone and the event still lands.
        """
        with contextlib.suppress(sqlite3.Error):
            self._conn.executemany(
                "INSERT OR IGNORE INTO savings_event_files"
                "(event_id, path, read_tokens, session_key) VALUES (?, ?, ?, ?)",
                (
                    (event.event_id, path, tokens, event.file_session_key)
                    for path, tokens in event.avoided_files
                ),
            )

    def record_file_read(self, session_key: str, path: str, read_at: str) -> None:
        """Note that the agent Read *path* in this session, keeping the latest time."""
        if self._read_only():
            return
        self._conn.execute(
            "INSERT INTO savings_file_reads(session_key, path, read_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_key, path) DO UPDATE SET read_at = excluded.read_at "
            "WHERE excluded.read_at > savings_file_reads.read_at",
            (session_key, path, read_at),
        )

    def _files_not_opened(self, where: str, params: list[Any], limit: int) -> Any:
        """Files not opened and tool calls answered, grouped by (day, agent, surface).

        Each (session, path) is owned by its first usable event in the window,
        or by the event alone when it carries no session. A later Read of the
        path in that session cancels it. Rides ``idx_savings_events_repo_time``
        and the files table's primary key; each statement is capped.
        """
        cells = [*params, _FILE_CELL_LIMIT]
        surfaces = ",".join(f"'{name}'" for name in TOOL_CALL_SURFACES)
        calls = self._conn.execute(
            "SELECT substr(occurred_at, 1, 10), agent, surface, COUNT(*) FROM savings_events "
            f"WHERE {where} AND surface IN ({surfaces}) AND {_ANSWERED_SQL} "
            "GROUP BY 1, 2, 3 LIMIT ?",
            cells,
        ).fetchall()
        # A read-only report never migrates, so a pre-v3 ledger has no files.
        (tables,) = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('savings_event_files', 'savings_file_reads')"
        ).fetchone()
        credited: list[tuple[Any, ...]] = []
        if tables == 2:
            credited = self._conn.execute(
                "SELECT day, agent, surface, COUNT(*), COALESCE(SUM(read_tokens), 0) FROM ("
                " SELECT f.path AS path, f.read_tokens AS read_tokens,"
                "  COALESCE(f.session_key, f.event_id) AS k, e.occurred_at AS at,"
                "  substr(e.occurred_at, 1, 10) AS day, e.agent AS agent, e.surface AS surface,"
                "  ROW_NUMBER() OVER (PARTITION BY COALESCE(f.session_key, f.event_id), f.path"
                "   ORDER BY e.occurred_at, e.event_id) AS rn"
                " FROM savings_events e JOIN savings_event_files f ON f.event_id = e.event_id"
                f" WHERE {where} AND e.is_usable = 1"
                ") c WHERE rn = 1 AND NOT EXISTS (SELECT 1 FROM savings_file_reads r"
                " WHERE r.session_key = c.k AND r.path = c.path AND r.read_at > c.at)"
                " GROUP BY 1, 2, 3 LIMIT ?",
                cells,
            ).fetchall()
        return files_not_opened(credited, calls, limit)

    def _checks(self, where: str, params: list[Any]) -> tuple[Any, ...]:
        """Per check tool: usable MCP calls and what they returned.

        Calls are counted in SQL. Outcomes are grouped by their stored text and
        folded in Python, so the read needs no JSON support in SQLite and costs
        one row per distinct outcome rather than one per call.
        """
        import json

        tools = ",".join("?" for _ in CHECK_TOOLS)
        scope = f"{where} AND surface = 'mcp' AND is_usable = 1 AND operation IN ({tools})"
        scoped = [*params, *CHECK_TOOLS]
        tallies: dict[str, CheckTally] = {}
        for tool, calls in self._conn.execute(
            f"SELECT operation, COUNT(*) FROM savings_events WHERE {scope} GROUP BY 1 LIMIT ?",
            [*scoped, len(CHECK_TOOLS)],
        ):
            tallies[tool] = CheckTally(calls=int(calls))
        try:
            for tool, text, calls in self._outcome_groups(scope, scoped):
                try:
                    outcome = json.loads(text)
                except ValueError:
                    continue
                if isinstance(outcome, dict) and tool in tallies:
                    tallies[tool].add_outcome(outcome, tool, int(calls))
        except sqlite3.OperationalError:
            # A sidecar opened read-only before its first upgrade has no column yet.
            pass
        return check_rows(tallies)

    def _outcome_groups(self, scope: str, params: list[Any]) -> Iterator[tuple[Any, ...]]:
        """Every ``(tool, outcome_json, calls)`` group in *scope*, a bounded page at a time.

        Keyset paging on the group key, so no group is dropped however many
        distinct outcomes a window holds, and memory stays at one page.
        """
        after: tuple[str, str] | None = None
        while True:
            keyset = " AND (operation, outcome_json) > (?, ?)" if after else ""
            page = self._conn.execute(
                f"SELECT operation, outcome_json, COUNT(*) FROM savings_events "
                f"WHERE {scope} AND outcome_json IS NOT NULL{keyset} "
                "GROUP BY 1, 2 ORDER BY 1, 2 LIMIT ?",
                [*params, *(after or ()), _CHECK_OUTCOME_PAGE],
            ).fetchall()
            yield from page
            if len(page) < _CHECK_OUTCOME_PAGE:
                return
            after = (page[-1][0], page[-1][1])

    def adopt_rows(self, repository_id: str) -> int:
        """File every row of this sidecar under *repository_id*; return how many moved.

        A sidecar lives inside the repository it describes, so a row naming any
        other id was written before the repository was moved or copied. A row
        whose retry key already exists under the new id keeps its old id.
        Takes no write lock when there is nothing to move.
        """
        if self._read_only():
            return 0
        tables = ("savings_events", "savings_opportunities")
        if not any(
            self._conn.execute(
                f"SELECT 1 FROM {table} WHERE repository_id != ? LIMIT 1", (repository_id,)
            ).fetchone()
            for table in tables
        ):
            return 0
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            moved = sum(
                self._conn.execute(
                    f"UPDATE OR IGNORE {table} SET repository_id = ? WHERE repository_id != ?",
                    (repository_id, repository_id),
                ).rowcount
                for table in tables
            )
            self._conn.commit()
            return moved
        except BaseException:
            self._conn.rollback()
            raise

    def record_opportunity(self, observation: OpportunityObservation) -> bool:
        """Insert an opportunity outside the achieved-event ledger."""
        if self._read_only():
            return False
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO savings_opportunities
                (observation_id, occurred_at, repository_id, integration, kind,
                 estimated_potential_input_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                observation.occurred_at,
                observation.repository_id,
                observation.integration,
                observation.kind,
                observation.estimated_potential_input_tokens,
            ),
        )
        return cursor.rowcount == 1

    def _reduction_p90(
        self, where: str, params: list[Any], population: int
    ) -> float | None:
        """The reduction ratio at the published quantile, over reducing events.

        One bounded row, not the population: a percentile is the value at a
        rank, so the database seeks to that rank rather than handing every
        ratio over to be sorted here, and the method stays as cheap as the
        aggregates beside it. ``reduction_quantile_offset`` is shared with the
        pure builder so the two land on the same row.
        """
        if not population:
            return None
        den = REDUCTION_DENOMINATOR_SQL
        hit = self._conn.execute(
            f"SELECT CAST(saved_input_tokens AS REAL) / {den} "
            f"FROM savings_events WHERE {where} AND {den} > 0 "
            "AND saved_input_tokens > 0 "
            "ORDER BY 1 ASC LIMIT 1 OFFSET ?",
            [*params, reduction_quantile_offset(population)],
        ).fetchone()
        return float(hit[0]) if hit is not None else None

    def report(
        self,
        repository_id: str,
        *,
        as_of: datetime,
        days: int | None = None,
        max_breakdowns: int = 100,
        fallback_rate: FallbackRate | None = None,
    ) -> SavingsReport:
        """Read totals and breakdowns with bounded aggregate queries, no writes.

        Every query is filtered on ``(repository_id, occurred_at)``, which is
        indexed, and every breakdown is capped, so the payload is bounded by
        the caps rather than by how much the repository has accumulated.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        if days is not None and days < 0:
            raise ValueError("days cannot be negative")
        where = "repository_id = ? AND occurred_at <= ?"
        params: list[Any] = [repository_id, utc_text(as_of)]
        if days is not None:
            cutoff = as_of - timedelta(days=days)
            where += " AND occurred_at >= ?"
            params.append(utc_text(cutoff))
        den = REDUCTION_DENOMINATOR_SQL
        row = self._conn.execute(
            f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(CASE WHEN result_state = 'success'
                    OR (result_state = 'partial' AND is_usable = 1) THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN saved_input_tokens > 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN surface = 'mcp' AND (result_state = 'success'
                    OR (result_state = 'partial' AND is_usable = 1)) THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN result_state = 'dead_end' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(saved_input_tokens), 0),
                COALESCE(SUM(CASE WHEN evidence_kind = 'measured'
                    THEN saved_input_tokens ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN evidence_kind = 'inferred'
                    THEN saved_input_tokens ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN input_rate_usd_per_million IS NOT NULL
                    THEN saved_input_tokens ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN input_rate_usd_per_million IS NOT NULL
                    THEN saved_input_tokens * input_rate_usd_per_million / 1000000.0
                    ELSE 0 END), 0.0),
                COUNT(saved_output_tokens),
                COALESCE(SUM(saved_output_tokens), 0),
                COALESCE(SUM(CASE WHEN output_rate_usd_per_million IS NOT NULL
                    THEN COALESCE(saved_output_tokens, 0) ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN output_rate_usd_per_million IS NOT NULL
                    THEN COALESCE(saved_output_tokens, 0) * output_rate_usd_per_million
                         / 1000000.0 ELSE 0 END), 0.0),
                MIN(occurred_at),
                MAX(occurred_at),
                COALESCE(SUM(CASE WHEN {den} > 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN {den} > 0 AND saved_input_tokens > 0
                    THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN {den} > 0 AND saved_input_tokens > 0
                    THEN {den} ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN {den} > 0 AND saved_input_tokens > 0
                    THEN saved_input_tokens ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN subagent_id IS NOT NULL THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN subagent_id IS NOT NULL
                    THEN saved_input_tokens ELSE 0 END), 0)
            FROM savings_events WHERE {where}
            """,
            params,
        ).fetchone()
        limit = max(0, min(int(max_breakdowns), 100))
        operations = self._grouped("operation", where, params, limit)
        surfaces = self._grouped("surface", where, params, limit)
        agents = self._grouped("agent", where, params, limit)
        models = self._grouped("model", where, params, limit)
        # Newest-first then reversed, so a window longer than the cap keeps the
        # recent days a reader is actually looking at rather than the oldest.
        days_rows = list(
            reversed(
                self._grouped(
                    "substr(occurred_at, 1, 10)", where, params, DAY_LIMIT, order="1 DESC"
                )
            )
        )
        opportunity_count, opportunity_tokens = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(estimated_potential_input_tokens), 0) "
            f"FROM savings_opportunities WHERE {where}",
            params,
        ).fetchone()
        opportunity_kinds = self._conn.execute(
            "SELECT kind, COUNT(*), COALESCE(SUM(estimated_potential_input_tokens), 0) "
            f"FROM savings_opportunities WHERE {where} "
            "GROUP BY 1 ORDER BY 3 DESC, 1 ASC LIMIT ?",
            [*params, limit],
        ).fetchall()
        baseline_events = int(row[16])
        reducing_events = int(row[17])
        baseline_input = int(row[18])
        baseline_saved = int(row[19])
        p90 = self._reduction_p90(where, params, reducing_events)
        files = self._files_not_opened(where, params, limit)
        saved_input = int(row[5])
        priced_input = int(row[8])
        saved_output = int(row[11])
        priced_output = int(row[12])
        try:
            backfilled_tokens, backfilled_usd = backfill_usd(
                self._unpriced_groups(where, params, repository_id), fallback_rate
            )
        except Exception:
            # A backfill is a refinement; it must never cost the report.
            backfilled_tokens, backfilled_usd = 0, 0.0
        output_estimate = self._output_estimate(where, params)
        return SavingsReport(
            unique_events=int(row[0]),
            successful_or_usable_partial_events=int(row[1]),
            saving_interactions=int(row[2]),
            mcp_queries_answered=int(row[3]),
            dead_ends=int(row[4]),
            saved_input_tokens=saved_input,
            measured_saved_input_tokens=int(row[6]),
            inferred_saved_input_tokens=int(row[7]),
            priced_saved_input_tokens=priced_input,
            unpriced_saved_input_tokens=saved_input - priced_input,
            priced_input_savings_usd=float(row[9]),
            saved_output_tokens=saved_output if int(row[10]) else None,
            priced_saved_output_tokens=priced_output,
            unpriced_saved_output_tokens=saved_output - priced_output,
            priced_output_savings_usd=float(row[13]),
            opportunity_count=int(opportunity_count),
            opportunity_tokens_excluded=int(opportunity_tokens),
            baseline_events=baseline_events,
            reducing_events=reducing_events,
            baseline_input_tokens=baseline_input,
            baseline_saved_input_tokens=baseline_saved,
            input_reduction_ratio=reduction_ratio(baseline_saved, baseline_input),
            input_reduction_ratio_p90=p90,
            per_operation=breakdown_rows("operation", operations),
            per_surface=breakdown_rows("surface", surfaces),
            per_agent=agent_breakdown_rows(agents),
            per_model=breakdown_rows("model", models),
            per_day=breakdown_rows("day", days_rows),
            per_opportunity_kind=tuple(
                {
                    "kind": item[0],
                    "observations": int(item[1]),
                    "estimated_potential_input_tokens": int(item[2]),
                }
                for item in opportunity_kinds
            ),
            first_event_at=row[14],
            last_event_at=row[15],
            files_not_opened=files.files,
            files_not_opened_tokens=files.tokens,
            tool_calls_answered=files.tool_calls,
            files_per_day=files.per_day,
            files_per_agent=files.per_agent,
            files_per_surface=files.per_surface,
            subagent_events=int(row[20]),
            subagent_saved_input_tokens=int(row[21]),
            backfilled_saved_input_tokens=backfilled_tokens,
            backfilled_input_savings_usd=backfilled_usd,
            output_estimate=output_estimate,
            checks=self._checks(where, params),
        )

    def _unpriced_groups(
        self, where: str, params: list[Any], repository_id: str
    ) -> list[tuple[str, float | None, int]]:
        """``(agent, session rate, tokens)`` over the window's unpriced savings.

        The session rate is the newest one any event of that session captured,
        in or out of the window, looked up only for the sessions the window's
        unpriced groups name. Grouped scans joined here rather than one
        windowed join in SQL, which measured twice as slow on a real ledger.
        The groups are capped at :data:`_BACKFILL_GROUP_LIMIT`, largest first;
        past the cap the rest of the saving simply stays unpriced.
        """
        groups = self._conn.execute(
            "SELECT agent, session_id, SUM(saved_input_tokens) FROM savings_events "
            f"WHERE {where} AND input_rate_usd_per_million IS NULL "
            "AND saved_input_tokens > 0 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT ?",
            [*params, _BACKFILL_GROUP_LIMIT],
        ).fetchall()
        sessions = sorted({str(session) for _agent, session, _tokens in groups if session})
        session_rates: dict[str, float] = {}
        for start in range(0, len(sessions), _REF_QUERY_BATCH):
            batch = sessions[start : start + _REF_QUERY_BATCH]
            placeholders = ",".join("?" for _ in batch)
            # SQLite fills a bare column from the row that holds the MAX, so
            # this is each session's newest rate. Two rows at the very same
            # microsecond resolve to either one.
            session_rates.update(
                (str(session), float(rate))
                for session, _latest, rate in self._conn.execute(
                    "SELECT session_id, MAX(occurred_at), input_rate_usd_per_million "
                    "FROM savings_events WHERE repository_id = ? "
                    f"AND session_id IN ({placeholders}) "
                    "AND input_rate_usd_per_million IS NOT NULL GROUP BY session_id LIMIT ?",
                    [repository_id, *batch, len(batch)],
                )
            )
        return [
            (str(agent), session_rates.get(str(session)) if session else None, int(tokens))
            for agent, session, tokens in groups
        ]

    def _output_estimate(self, where: str, params: list[Any]) -> OutputEstimate | None:
        """The benchmark-calibrated output estimate; ``None`` rather than a failed report.

        Imported here, not at module top: the event writers import this module
        on the hook path and never build a report.
        """
        from repowise.core.savings.output_estimate import SESSION_GROUPS_SQL, estimate_output

        try:
            groups = self._conn.execute(SESSION_GROUPS_SQL.format(where=where), params).fetchall()
        except sqlite3.Error:
            return None
        return estimate_output(groups)
