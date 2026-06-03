"""Runtime LLM cost tracking for repowise.

Tracks token usage and cost per session, and optionally persists rows to
the ``llm_costs`` table for historical reporting via ``repowise costs``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing table — USD per 1 million tokens
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.8, "output": 4.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    # Google Gemini
    "gemini-2.0-flash": {"input": 0.075, "output": 0.3},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.0},
    # Gemini preview / experimental models
    "gemini-3.1-flash-lite-preview": {"input": 0.075, "output": 0.30},
    "gemini-3-flash-preview": {"input": 0.075, "output": 0.30},
    # DeepSeek
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"input": 1.74, "output": 3.48},
}

_FALLBACK_PRICING: dict[str, float] = {"input": 3.0, "output": 15.0}

# Track which unknown models we've already warned about (per-process)
_warned_models: set[str] = set()


def _get_pricing(model: str) -> dict[str, float]:
    """Return pricing for *model*, falling back and warning if unknown."""
    if model.startswith("codex_cli/"):
        return {"input": 0.0, "output": 0.0}
    if model in _PRICING:
        return _PRICING[model]
    if model not in _warned_models:
        log.warning("cost_tracker.unknown_model", model=model, fallback=_FALLBACK_PRICING)
        _warned_models.add(model)
    return _FALLBACK_PRICING


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Tracks LLM token usage and cost for a session.

    Optionally persists each call to the ``llm_costs`` table when a
    *session_factory* (async SQLAlchemy sessionmaker) is supplied.

    Parameters
    ----------
    session_factory:
        Async SQLAlchemy sessionmaker. When ``None``, only in-memory
        tracking is performed.
    repo_id:
        Repository primary key to associate with persisted rows.
    buffered:
        When ``True``, ``record()`` accumulates rows in memory instead of
        writing each one immediately; the caller persists them in a single
        transaction via :meth:`flush` once heavy DB work is done. This keeps
        cost writes out of the doc-generation window, where a per-call
        ``INSERT`` opened on a *second* engine loses WAL's single-writer race
        against the generation writer and stalls for the full ``busy_timeout``
        (issue #326). Defaults to ``False`` so existing callers that rely on
        immediate persistence are unaffected; the CLI opts in.
    """

    def __init__(
        self,
        session_factory: Any | None = None,
        repo_id: str | None = None,
        *,
        buffered: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._repo_id = repo_id
        self._session_cost: float = 0.0
        self._session_tokens: int = 0
        self._buffered = buffered
        # Pending rows when buffered; flushed in one transaction by ``flush``.
        self._pending: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_cost(self) -> float:
        """Cumulative USD cost for this tracker instance."""
        return self._session_cost

    @property
    def session_tokens(self) -> int:
        """Cumulative tokens (input + output) for this tracker instance."""
        return self._session_tokens

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str,
        file_path: str | None = None,
    ) -> float:
        """Record a single LLM call and return its cost in USD.

        Parameters
        ----------
        model:
            Model identifier, e.g. ``"claude-sonnet-4-6"``.
        input_tokens:
            Number of input/prompt tokens consumed.
        output_tokens:
            Number of output/completion tokens consumed.
        operation:
            Logical operation label, e.g. ``"doc_generation"`` or
            ``"embedding"``.
        file_path:
            Source file being processed, if available.

        Returns
        -------
        float
            Cost in USD for this call.
        """
        pricing = _get_pricing(model)
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

        self._session_cost += cost
        self._session_tokens += input_tokens + output_tokens

        log.debug(
            "cost_tracker.record",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            operation=operation,
            file_path=file_path,
        )

        if self._session_factory is not None and self._repo_id is not None:
            row = {
                "model": model,
                "operation": operation,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "file_path": file_path,
            }
            if self._buffered:
                # Defer the write — flushed in one transaction later, outside the
                # contended generation window (issue #326).
                self._pending.append(row)
            else:
                await self._persist_rows([row])

        return cost

    async def flush(self) -> int:
        """Persist any buffered cost rows in a single transaction.

        Best-effort: a contended or failed flush drops the buffered rows (cost
        telemetry is non-critical) rather than raising into the caller. Safe to
        call on an in-memory or non-buffered tracker — it is then a no-op.
        Returns the number of rows persisted.
        """
        if not self._pending:
            return 0
        rows = self._pending
        self._pending = []
        return await self._persist_rows(rows)

    async def _persist_rows(self, rows: list[dict[str, Any]]) -> int:
        """Write *rows* to the ``llm_costs`` table. Best-effort.

        Returns the number of rows written (0 if the write was dropped on
        contention or error). A single transaction is used so a buffered flush
        of hundreds of rows takes one write-lock acquisition, not one per row.
        """
        if self._session_factory is None or self._repo_id is None or not rows:
            return 0
        try:
            from repowise.core.persistence import get_session
            from repowise.core.persistence.models import LlmCost

            async with get_session(self._session_factory) as session:
                session.add_all(
                    [
                        LlmCost(
                            repository_id=self._repo_id,
                            model=r["model"],
                            operation=r["operation"],
                            input_tokens=r["input_tokens"],
                            output_tokens=r["output_tokens"],
                            cost_usd=r["cost_usd"],
                            file_path=r["file_path"],
                        )
                        for r in rows
                    ]
                )
                await session.commit()
            return len(rows)
        except Exception as exc:
            log.warning("cost_tracker.persist_failed", error=str(exc), dropped=len(rows))
            return 0

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def totals(
        self,
        since: datetime | None = None,
        group_by: str = "operation",
    ) -> list[dict]:
        """Query aggregate cost totals from the database.

        Parameters
        ----------
        since:
            Only include rows whose ``ts`` is on or after this datetime.
        group_by:
            Grouping dimension: ``"operation"``, ``"model"``, or ``"day"``.

        Returns
        -------
        list[dict]
            Each dict has keys: ``group``, ``calls``, ``input_tokens``,
            ``output_tokens``, ``cost_usd``.
        """
        if self._session_factory is None or self._repo_id is None:
            return []

        try:
            import sqlalchemy as sa

            from repowise.core.persistence import get_session
            from repowise.core.persistence.models import LlmCost

            async with get_session(self._session_factory) as session:
                if group_by == "model":
                    group_col = LlmCost.model
                elif group_by == "day":
                    # SQLite strftime; works for Postgres too with cast
                    group_col = sa.func.strftime("%Y-%m-%d", LlmCost.ts)
                else:
                    group_col = LlmCost.operation

                stmt = (
                    sa.select(
                        group_col.label("group"),
                        sa.func.count().label("calls"),
                        sa.func.sum(LlmCost.input_tokens).label("input_tokens"),
                        sa.func.sum(LlmCost.output_tokens).label("output_tokens"),
                        sa.func.sum(LlmCost.cost_usd).label("cost_usd"),
                    )
                    .where(LlmCost.repository_id == self._repo_id)
                    .group_by(group_col)
                    .order_by(sa.func.sum(LlmCost.cost_usd).desc())
                )

                if since is not None:
                    stmt = stmt.where(LlmCost.ts >= since)

                result = await session.execute(stmt)
                rows = result.fetchall()

            return [
                {
                    "group": row.group,
                    "calls": row.calls,
                    "input_tokens": row.input_tokens or 0,
                    "output_tokens": row.output_tokens or 0,
                    "cost_usd": row.cost_usd or 0.0,
                }
                for row in rows
            ]
        except Exception as exc:
            log.warning("cost_tracker.totals_failed", error=str(exc))
            return []
