"""Pure, clamped token-accounting formulas."""

from __future__ import annotations

from dataclasses import dataclass


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
