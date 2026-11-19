"""Output tokens saved, estimated per session from the paired benchmark.

No surface measures the output an agent did not write: the counterfactual
session never ran. What the paired benchmark does measure is how much shorter
a session's output gets when the agent has Repowise, per model. This module
applies that measured difference once per agent session that used Repowise,
and labels it as an estimate everywhere it appears.

Rules, chosen so every credited token traces back to a benchmark row:

- **Which sessions.** A session with at least one MCP event in the window,
  which is what the benchmark's Repowise arm measured. Hook events are not
  counted as sessions of their own: a stored session id is hashed with its
  surface, so a hook event and an MCP call from the same conversation carry
  different ids and counting both would credit one session twice. Events with
  no session id are not a session and earn nothing here.
- **Which model.** The model on the session's events (the most common priced
  one when a session carries several). Priced at that event's captured output
  rate, so history is never repriced. A session with no model earns tokens at
  the conservative fallback below and no dollars.
- **How much.** A model with a benchmark row is credited its own measured
  difference in output tokens per session (bare mean minus Repowise mean).
  A Claude model without a row is credited the cross-model median reduction,
  or its family's lowest measured reduction when that is smaller, applied to
  the cross-model median bare output. Any other model, or an unknown one, is
  credited the lowest reduction measured on any model: the benchmark has no
  arm for it, and the smallest effect we observed is the most we can defend.
  The best row is never used as a fallback. A measured row is credited its
  point estimate, even where its interval spans zero; the data file keeps
  each row's interval beside it so the basis stays checkable.

The baseline is the benchmark's per-session bare output, not the session's
own transcript. A real session is usually longer than a one-question bench
session, so this understates; it also stays reproducible after transcripts
are rotated away, and costs no transcript scan at report time.

Computed at report time only, from the ledger and the versioned data file in
``_data/output_calibration.json``. Never raises: a missing or malformed file,
or anything unexpected, means no estimate, never a failed report.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CALIBRATION_PATH = Path(__file__).parent / "_data" / "output_calibration.json"

#: Surfaces whose events mark a session that used Repowise. One surface only:
#: session ids are hashed per surface, so two surfaces cannot be joined.
SESSION_SURFACES = ("mcp",)

MEASURED = "measured"
ANTHROPIC_FALLBACK = "anthropic_fallback"
OTHER_FALLBACK = "other_fallback"

#: One row per ``(model, output rate)`` the window's sessions resolved to: the
#: session's model is its most common non-null model, ties broken by name and
#: rate. Rides ``idx_savings_events_repo_time`` through the ``{where}`` filter.
#: The cap is on distinct (model, rate) pairs, which in practice number a
#: handful; it is there so the payload stays bounded like every other grouping.
SESSION_GROUPS_SQL = """
SELECT model, rate, COUNT(*) FROM (
    SELECT model, rate, ROW_NUMBER() OVER (
        PARTITION BY session_id
        ORDER BY model IS NULL, n DESC, model, rate IS NULL, rate
    ) AS pick
    FROM (
        SELECT session_id, model, output_rate_usd_per_million AS rate, COUNT(*) AS n
        FROM savings_events
        WHERE {where} AND surface = 'mcp' AND session_id IS NOT NULL
        GROUP BY session_id, model, output_rate_usd_per_million
    )
)
WHERE pick = 1
GROUP BY model, rate ORDER BY 3 DESC, 1, 2 LIMIT 100
"""


@dataclass(frozen=True, slots=True)
class ModelOutputEstimate:
    """The estimate for the sessions that resolved to one model and rate."""

    model: str | None
    #: ``measured``: this model's own benchmark row. ``anthropic_fallback``:
    #: a Claude model with no row. ``other_fallback``: any other or unknown model.
    basis: str
    sessions: int
    #: Share of bare output the Repowise arm did not write.
    reduction: float
    output_tokens_per_session: int
    output_tokens_saved: int
    output_rate_usd_per_million: float | None
    #: Null when the sessions carry no output rate.
    output_usd_saved: float | None


@dataclass(frozen=True, slots=True)
class OutputEstimate:
    """Output tokens saved over a window, estimated from the benchmark."""

    source: str
    version: str
    sessions: int
    output_tokens_saved: int
    output_usd_saved: float
    unpriced_output_tokens_saved: int
    per_model: tuple[ModelOutputEstimate, ...]


@dataclass(frozen=True, slots=True)
class _Calibration:
    source: str
    version: str
    #: model -> (bare mean, Repowise mean)
    rows: dict[str, tuple[float, float]]


@lru_cache(maxsize=1)
def _load_calibration() -> _Calibration:
    payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    rows = {
        str(model): (float(row["bare_output_tokens"]), float(row["repowise_output_tokens"]))
        for model, row in payload["models"].items()
    }
    if not rows or any(bare <= 0 for bare, _rw in rows.values()):
        raise ValueError("calibration needs at least one row with a positive bare mean")
    return _Calibration(str(payload["source"]), str(payload["version"]), rows)


def _reduction(bare: float, repowise: float) -> float:
    return max(0.0, (bare - repowise) / bare)


def _claude_family(model: str) -> str | None:
    """``opus`` for ``claude-opus-5-5`` and ``claude-3-5-opus-...``; None otherwise."""
    parts = model.lower().split("-")
    if not parts or parts[0] != "claude":
        return None
    return next((part for part in parts[1:] if part.isalpha()), None)


def _per_session(model: str | None, calibration: _Calibration) -> tuple[str, float, int]:
    """``(basis, reduction, output tokens saved per session)`` for *model*."""
    from statistics import median  # report time only; kept off the writers' import path

    from repowise.core.distill.session_model import normalize_model_id

    rows = calibration.rows
    model = normalize_model_id(model) if model else model
    if model in rows:
        bare, repowise = rows[model]
        return MEASURED, _reduction(bare, repowise), max(0, round(bare - repowise))
    reductions = {name: _reduction(bare, rw) for name, (bare, rw) in rows.items()}
    bare_median = median(bare for bare, _rw in rows.values())
    family = _claude_family(model) if model else None
    if model and model.lower().startswith("claude"):
        reduction = median(reductions.values())
        in_family = [value for name, value in reductions.items() if _claude_family(name) == family]
        if family and in_family:
            reduction = min(reduction, min(in_family))
        return ANTHROPIC_FALLBACK, reduction, round(bare_median * reduction)
    reduction = min(reductions.values())
    return OTHER_FALLBACK, reduction, round(bare_median * reduction)


def session_groups(events: Iterable[Any]) -> list[tuple[str | None, float | None, int]]:
    """The pure-Python twin of :data:`SESSION_GROUPS_SQL`, for scoped events.

    Same picking rule and same ordering, so the two report builders agree
    field for field.
    """
    counts: dict[str, dict[tuple[str | None, float | None], int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for event in events:
        if event.surface in SESSION_SURFACES and event.session_id is not None:
            counts[event.session_id][(event.model, event.output_rate_usd_per_million)] += 1
    picked: dict[tuple[str | None, float | None], int] = defaultdict(int)
    for per_session in counts.values():
        key = min(
            per_session,
            key=lambda pair: (
                pair[0] is None,
                -per_session[pair],
                pair[0] or "",
                pair[1] is None,
                pair[1] or 0.0,
            ),
        )
        picked[key] += 1
    ordered = sorted(
        picked.items(),
        key=lambda item: (
            -item[1],
            item[0][0] is not None,
            item[0][0] or "",
            item[0][1] is not None,
            item[0][1] or 0.0,
        ),
    )
    return [(model, rate, sessions) for (model, rate), sessions in ordered[:100]]


def estimate_output(
    groups: Iterable[tuple[str | None, float | None, int]],
) -> OutputEstimate | None:
    """The estimate over *groups*, or ``None`` when it cannot be made.

    Never raises: the estimate is an addition to a report, and a report must
    not fail because its calibration file is missing or malformed.
    """
    try:
        calibration = _load_calibration()
        per_model: list[ModelOutputEstimate] = []
        for model, rate, sessions in groups:
            basis, reduction, per_session = _per_session(model, calibration)
            tokens = per_session * int(sessions)
            usd = tokens * float(rate) / 1_000_000 if rate is not None and model else None
            per_model.append(
                ModelOutputEstimate(
                    model=model,
                    basis=basis,
                    sessions=int(sessions),
                    reduction=reduction,
                    output_tokens_per_session=per_session,
                    output_tokens_saved=tokens,
                    output_rate_usd_per_million=None if usd is None else float(rate),
                    output_usd_saved=usd,
                )
            )
        return OutputEstimate(
            source=calibration.source,
            version=calibration.version,
            sessions=sum(row.sessions for row in per_model),
            output_tokens_saved=sum(row.output_tokens_saved for row in per_model),
            output_usd_saved=sum(row.output_usd_saved or 0.0 for row in per_model),
            unpriced_output_tokens_saved=sum(
                row.output_tokens_saved for row in per_model if row.output_usd_saved is None
            ),
            per_model=tuple(per_model),
        )
    except Exception:  # an estimate is never worth a failed report
        logger.debug("output savings estimate unavailable", exc_info=True)
        return None
