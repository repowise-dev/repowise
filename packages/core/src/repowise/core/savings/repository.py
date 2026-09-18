"""Short transactional writes and bounded aggregate reads for savings data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from repowise.core.savings.contracts import (
    OpportunityObservation,
    SavingsEvent,
    SavingsReport,
    utc_text,
)
from repowise.core.savings.reporting import (
    DAY_LIMIT,
    agent_breakdown_rows,
    breakdown_rows,
)

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
)


class SavingsRepository:
    """Repository over an initialized omissions-sidecar connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

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
            f"SELECT {expression}, COUNT(*), COALESCE(SUM(saved_input_tokens), 0) "
            f"FROM savings_events WHERE {where} "
            f"GROUP BY 1 ORDER BY {order} LIMIT ?",
            [*params, limit],
        ).fetchall()

    def record_event(self, event: SavingsEvent) -> bool:
        """Atomically insert an event and its links; return false for a retry."""
        values = [getattr(event, column) for column in _EVENT_COLUMNS[:-1]]
        values[18] = int(event.is_usable)
        values.append(event.metadata_json())
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

    def record_opportunity(self, observation: OpportunityObservation) -> bool:
        """Insert an opportunity outside the achieved-event ledger."""
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

    def report(
        self,
        repository_id: str,
        *,
        as_of: datetime,
        days: int | None = None,
        max_breakdowns: int = 100,
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
                MAX(occurred_at)
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
        saved_input = int(row[5])
        priced_input = int(row[8])
        saved_output = int(row[11])
        priced_output = int(row[12])
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
        )
