"""Pure, clamped token-accounting formulas."""

from __future__ import annotations

from dataclasses import dataclass

#: ``savings_events.estimator`` of the events recovered from agent transcripts
#: (``savings/transcript.py``). Here rather than there so the report can name
#: the population without importing the transcript readers.
TRANSCRIPT_ESTIMATOR = "transcript_marker_v1"

#: Codex's measured truncation plateau, across 95,814 shell results in this
#: repository. Above 5,000 characters the commonest lengths are 24,133 (164
#: texts) and a tight cluster at 40,100-40,104, over 200 texts within four
#: characters of one another: a cap with a variable-length truncation notice
#: after it. Claude Code's plateau is the same shape and sharper, 24 texts at
#: exactly 30,000 and nothing above it.
#:
#: Codex does not always stop there: a few results run to 159,585, 434,762,
#: 1,510,994 and 2,552,250 characters. Capping at that maximum would credit
#: one command with up to 638k tokens, more than a model's context can hold.
#: The plateau is what Codex usually hands the model, so it is the defensible
#: claim. A result that escaped it still banks what was actually delivered:
#: the transcript accounting never clips below the delivery.
#:
#: The report applies the same cap to rows stored above it
#: (``service.load_report``), so a ledger shows what a fresh backfill records.
CODEX_OUTPUT_CAP_CHARS = 40_100
#: The same cap in tokens. Token estimates are ``len // 4`` throughout, so
#: capping characters and capping tokens agree exactly.
CODEX_OUTPUT_CAP_TOKENS = CODEX_OUTPUT_CAP_CHARS // 4


@dataclass(frozen=True, slots=True)
class TokenAccounting:
    baseline_input_tokens: int | None
    pre_budget_input_tokens: int | None
    delivered_input_tokens: int | None
    dropped_input_tokens: int | None
    saved_input_tokens: int
    baseline_output_tokens: int | None
    delivered_output_tokens: int | None
    saved_output_tokens: int | None


def _token(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("token dimensions must be integers or None")
    return max(value, 0)


def _delta(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return max(before - after, 0)


def calculate_token_accounting(
    *,
    surface: str,
    evidence_kind: str,
    result_state: str,
    is_usable: bool,
    baseline_input_tokens: int | None,
    pre_budget_input_tokens: int | None,
    delivered_input_tokens: int | None,
    baseline_output_tokens: int | None = None,
    delivered_output_tokens: int | None = None,
) -> TokenAccounting:
    """Normalize dimensions and apply the v1 decision table."""
    baseline = _token(baseline_input_tokens)
    pre_budget = _token(pre_budget_input_tokens)
    delivered = _token(delivered_input_tokens)
    output_baseline = _token(baseline_output_tokens)
    output_delivered = _token(delivered_output_tokens)
    dropped = _delta(pre_budget, delivered)

    achieved = result_state == "success" or (result_state == "partial" and is_usable)
    if not achieved:
        saved = 0
        saved_output = 0 if output_baseline is not None and output_delivered is not None else None
    else:
        formula_baseline = baseline
        if evidence_kind == "measured" and surface == "mcp":
            formula_baseline = pre_budget
        saved = _delta(formula_baseline, delivered) or 0
        saved_output = _delta(output_baseline, output_delivered)

    return TokenAccounting(
        baseline_input_tokens=baseline,
        pre_budget_input_tokens=pre_budget,
        delivered_input_tokens=delivered,
        dropped_input_tokens=dropped,
        saved_input_tokens=saved,
        baseline_output_tokens=output_baseline,
        delivered_output_tokens=output_delivered,
        saved_output_tokens=saved_output,
    )


#: The quantile the report publishes beside the aggregate reduction. High
#: enough to describe the large outputs where a reduction is worth having,
#: low enough not to be the single best call ever recorded.
REDUCTION_QUANTILE = 0.90


def reduction_quantile_offset(population: int, quantile: float = REDUCTION_QUANTILE) -> int:
    """The 0-based rank in an ascending ratio list that names *quantile*.

    Defined once because two report builders have to land on the same row: the
    SQL path reaches it with ``ORDER BY ratio LIMIT 1 OFFSET n`` and the pure
    path indexes a sorted list. Nearest-rank, no interpolation -- an
    interpolating definition would make the two disagree in the last decimal
    and the parity test compares them exactly.
    """
    if population <= 0:
        return 0
    return min(population - 1, int(quantile * population))


#: The same rule as :func:`reduction_denominator`, for the SQL report builder.
#: It has to exist twice because one builder is Python over domain records and
#: the other is a single aggregate query, but it is written once per language
#: and pinned by ``test_the_sql_report_and_the_pure_report_agree``, whose
#: fixture carries a measured MCP event whose pre-budget and baseline differ --
#: without that event the two definitions could disagree and still pass.
REDUCTION_DENOMINATOR_SQL = (
    "(CASE WHEN evidence_kind = 'measured' AND surface = 'mcp' "
    "THEN pre_budget_input_tokens ELSE baseline_input_tokens END)"
)


def reduction_denominator(
    *,
    surface: str,
    evidence_kind: str,
    baseline_input_tokens: int | None,
    pre_budget_input_tokens: int | None,
) -> int | None:
    """What the saving on this event was actually computed against.

    The decision table gives a measured MCP event its ``pre_budget`` size as
    the formula baseline, not ``baseline_input_tokens``; the two differ, and
    that event's ``baseline`` can be an estimator floor an order of magnitude
    smaller. Dividing the saving by the wrong one of those produces reductions
    over 100%, which is how this was found -- a ledger read 424%.

    Because ``saved`` is ``clamp(this - delivered)``, the ratio it forms is in
    ``[0, 1]`` by construction on every event, whatever the surface.
    """
    if evidence_kind == "measured" and surface == "mcp":
        chosen = pre_budget_input_tokens
    else:
        chosen = baseline_input_tokens
    if chosen is None or chosen <= 0:
        return None
    return chosen


def reduction_ratio(saved_input_tokens: int, baseline_input_tokens: int) -> float | None:
    """How much smaller the input got, over events that carry a baseline.

    ``None`` rather than zero when nothing in the window carried a baseline:
    "no basis to say" and "measured, and it was nothing" are different claims,
    which is the same rule the report applies to ``saved_output_tokens``.
    """
    if baseline_input_tokens <= 0:
        return None
    return saved_input_tokens / baseline_input_tokens
