"""Output tokens saved, estimated per question from the paired benchmark.

No surface measures the output an agent did not write: the counterfactual
session never ran. What the paired benchmark does measure is how much shorter
the output for one question gets when the agent has Repowise, per model. This
module applies that measured difference per question asked in each agent
session that used Repowise, and labels it as an estimate everywhere it appears.

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
- **How many questions.** One bench session is one question; a real session
  asks many. Questions per session = the session's MCP calls divided by the
  model's mean Repowise-arm MCP calls per bench question, at least 1 and at
  most :data:`QUESTIONS_PER_SESSION_CAP`, so one marathon session cannot
  dominate the total.
- **How much per question.** A model with a benchmark row is credited its own
  measured difference (bare mean minus Repowise mean output tokens). A Claude
  model without a row is credited the cross-model median reduction, or its
  family's lowest measured reduction when that is smaller, applied to the
  cross-model median bare output, with the median calls-per-question divisor.
  Any other model, or an unknown one, is credited the lowest reduction
  measured on any model: the benchmark has no arm for it, and the smallest
  effect we observed is the most we can defend. The best row is never used as
  a fallback. A measured row is credited its point estimate, even where its
  interval spans zero; the data file keeps each row's interval beside it so
  the basis stays checkable.

The per-question baseline is the benchmark's bare output, not the session's
own transcript, so the figure stays reproducible after transcripts are
rotated away and costs no transcript scan at report time.

Computed at report time only, from the ledger and the versioned data file in
``_data/output_calibration.json``. Never raises: a missing or malformed file,
or anything unexpected, means no estimate, never a failed report.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
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

#: A session is credited at most this many questions. Bench questions take
#: 2 to 9 Repowise calls each on average, so 20 questions is a long working
#: session; past it, more calls more likely mean one hard task than more
#: questions, and the estimate stops growing.
QUESTIONS_PER_SESSION_CAP = 20

#: The rule, in one line, returned with every estimate so a reader can redo it.
FORMULA = (
    "per session: output_tokens_per_question x clamp(session MCP calls / "
    "mcp_calls_per_question, 1, questions_per_session_cap)"
)

#: A trailing ``-YYYYMMDD`` release date. Calibration rows are keyed by the
#: undated id, so a dated session id is looked up without it.
_DATE_SUFFIX = re.compile(r"-\d{8}$")

#: Calls per session are counted up to this, far past the point where every
#: measured divisor already reaches the question cap. It bounds the number of
#: distinct groups the query below can return.
_CALLS_CEILING = 1_000

#: One row per ``(model, output rate, MCP calls)`` the window's sessions
#: resolved to, with how many sessions share it. A session's model is its most
#: common non-null model, ties broken by name and rate; its calls are all its
#: MCP events in the window. The window filter rides an index like every other
#: report query. The limit is far above the distinct groups a real ledger
#: produces (models times call counts); it keeps the payload bounded.
SESSION_GROUPS_SQL = """
SELECT model, rate, calls, COUNT(*) FROM (
    SELECT model, rate, MIN(total, 1000) AS calls, ROW_NUMBER() OVER (
        PARTITION BY session_id
        ORDER BY model IS NULL, n DESC, model, rate IS NULL, rate
    ) AS pick
    FROM (
        SELECT session_id, model, output_rate_usd_per_million AS rate, COUNT(*) AS n,
            SUM(COUNT(*)) OVER (PARTITION BY session_id) AS total
        FROM savings_events
        WHERE {where} AND surface = 'mcp' AND session_id IS NOT NULL
        GROUP BY session_id, model, output_rate_usd_per_million
    )
)
WHERE pick = 1
GROUP BY model, rate, calls ORDER BY 1, 2, 3 LIMIT 5000
"""


@dataclass(frozen=True, slots=True)
class ModelOutputEstimate:
    """The estimate for the sessions that resolved to one model and rate."""

    model: str | None
    #: ``measured``: this model's own benchmark row. ``anthropic_fallback``:
    #: a Claude model with no row. ``other_fallback``: any other or unknown model.
    basis: str
    sessions: int
    #: Questions credited across those sessions, after the floor and the cap.
    questions: float
    #: The divisor: mean Repowise-arm MCP calls per bench question.
    mcp_calls_per_question: float
    #: Share of bare output the Repowise arm did not write.
    reduction: float
    output_tokens_per_question: int
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
    questions: float
    output_tokens_saved: int
    output_usd_saved: float
    unpriced_output_tokens_saved: int
    per_model: tuple[ModelOutputEstimate, ...]
    #: Credited per question, not per session; the rule and its cap ride along.
    per_question: bool = True
    questions_per_session_cap: int = QUESTIONS_PER_SESSION_CAP
    formula: str = FORMULA

    def calibration(self) -> dict[str, Any]:
        """Everything but the two totals: the block both surfaces publish."""
        payload = asdict(self)
        del payload["output_tokens_saved"], payload["output_usd_saved"]
        return payload


@dataclass(frozen=True, slots=True)
class _Calibration:
    source: str
    version: str
    #: model -> (bare mean, Repowise mean, Repowise MCP calls per question)
    rows: dict[str, tuple[float, float, float]]


@lru_cache(maxsize=1)
def _load_calibration() -> _Calibration:
    payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    rows = {
        str(model): (
            float(row["bare_output_tokens"]),
            float(row["repowise_output_tokens"]),
            float(row["repowise_mcp_calls_per_question"]),
        )
        for model, row in payload["models"].items()
    }
    if not rows or any(bare <= 0 or calls <= 0 for bare, _rw, calls in rows.values()):
        raise ValueError("calibration rows need a positive bare mean and call rate")
    return _Calibration(str(payload["source"]), str(payload["version"]), rows)


def _reduction(bare: float, repowise: float) -> float:
    return max(0.0, (bare - repowise) / bare)


def _claude_family(model: str) -> str | None:
    """``opus`` for ``claude-opus-5-5`` and ``claude-3-5-opus-...``; None otherwise."""
    parts = model.lower().split("-")
    if not parts or parts[0] != "claude":
        return None
    return next((part for part in parts[1:] if part.isalpha()), None)


def _per_question(
    model: str | None, calibration: _Calibration
) -> tuple[str, float, int, float]:
    """``(basis, reduction, output tokens saved per question, calls per question)``."""
    from statistics import median  # report time only; kept off the writers' import path

    from repowise.core.distill.session_model import normalize_model_id

    rows = calibration.rows
    model = normalize_model_id(model) if model else model
    if model and model not in rows:
        model = _DATE_SUFFIX.sub("", model)
    if model in rows:
        bare, repowise, calls = rows[model]
        return MEASURED, _reduction(bare, repowise), max(0, round(bare - repowise)), calls
    reductions = {name: _reduction(bare, rw) for name, (bare, rw, _calls) in rows.items()}
    bare_median = median(bare for bare, _rw, _calls in rows.values())
    calls_median = median(calls for _bare, _rw, calls in rows.values())
    family = _claude_family(model) if model else None
    if model and model.lower().startswith("claude"):
        reduction = median(reductions.values())
        in_family = [value for name, value in reductions.items() if _claude_family(name) == family]
        if family and in_family:
            reduction = min(reduction, min(in_family))
        return ANTHROPIC_FALLBACK, reduction, round(bare_median * reduction), calls_median
    reduction = min(reductions.values())
    return OTHER_FALLBACK, reduction, round(bare_median * reduction), calls_median


def _questions(calls: int, calls_per_question: float) -> float:
    return min(max(calls / calls_per_question, 1.0), float(QUESTIONS_PER_SESSION_CAP))


Group = tuple[str | None, float | None, int, int]


def session_groups(events: Iterable[Any]) -> list[Group]:
    """The pure-Python twin of :data:`SESSION_GROUPS_SQL`, for scoped events.

    ``(model, rate, calls, sessions)`` rows. Same picking rule, same call
    ceiling and same ordering, so the two report builders agree field for
    field.
    """
    counts: dict[str, dict[tuple[str | None, float | None], int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for event in events:
        if event.surface in SESSION_SURFACES and event.session_id is not None:
            counts[event.session_id][(event.model, event.output_rate_usd_per_million)] += 1
    picked: dict[tuple[str | None, float | None, int], int] = defaultdict(int)
    for per_session in counts.values():
        model, rate = min(
            per_session,
            key=lambda pair: (
                pair[0] is None,
                -per_session[pair],
                pair[0] or "",
                pair[1] is None,
                pair[1] or 0.0,
            ),
        )
        picked[(model, rate, min(sum(per_session.values()), _CALLS_CEILING))] += 1
    ordered = sorted(
        picked.items(),
        key=lambda item: (
            item[0][0] is not None,
            item[0][0] or "",
            item[0][1] is not None,
            item[0][1] or 0.0,
            item[0][2],
        ),
    )
    return [(model, rate, calls, sessions) for (model, rate, calls), sessions in ordered[:5000]]


def estimate_output(groups: Iterable[Group]) -> OutputEstimate | None:
    """The estimate over *groups*, or ``None`` when it cannot be made.

    Never raises: the estimate is an addition to a report, and a report must
    not fail because its calibration file is missing or malformed.
    """
    try:
        calibration = _load_calibration()
        rows: dict[tuple[str | None, float | None], dict[str, Any]] = {}
        for model, rate, calls, sessions in groups:
            basis, reduction, per_question, divisor = _per_question(model, calibration)
            questions = _questions(int(calls), divisor)
            row = rows.setdefault(
                (model, rate),
                {
                    "basis": basis,
                    "reduction": reduction,
                    "per_question": per_question,
                    "divisor": divisor,
                    "sessions": 0,
                    "questions": 0.0,
                    "tokens": 0,
                },
            )
            row["sessions"] += int(sessions)
            row["questions"] += questions * int(sessions)
            row["tokens"] += round(per_question * questions) * int(sessions)
        per_model: list[ModelOutputEstimate] = []
        for (model, rate), row in sorted(
            rows.items(),
            key=lambda item: (
                -item[1]["sessions"],
                item[0][0] is not None,
                item[0][0] or "",
                item[0][1] is not None,
                item[0][1] or 0.0,
            ),
        ):
            priced = rate is not None and model is not None
            per_model.append(
                ModelOutputEstimate(
                    model=model,
                    basis=row["basis"],
                    sessions=row["sessions"],
                    questions=round(row["questions"], 2),
                    mcp_calls_per_question=row["divisor"],
                    reduction=row["reduction"],
                    output_tokens_per_question=row["per_question"],
                    output_tokens_saved=row["tokens"],
                    output_rate_usd_per_million=float(rate) if priced else None,
                    output_usd_saved=row["tokens"] * float(rate) / 1_000_000 if priced else None,
                )
            )
        return OutputEstimate(
            source=calibration.source,
            version=calibration.version,
            sessions=sum(row.sessions for row in per_model),
            questions=round(sum(row.questions for row in per_model), 2),
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
