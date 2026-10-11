"""The token estimator's error against a real tokenizer, on recorded responses.

The MCP host caps a tool result in tokens and spills an over-cap result to a
sidecar file the agent has to read back. The budget is declared in characters,
so :func:`estimate_response_tokens` is what converts between the two, and an
estimate that runs low is the failure that costs a caller the extra read.

The rates were fitted on this same corpus, so these bounds are a regression
guard rather than evidence that they generalise to an unrecorded payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from repowise.server.mcp_server._budget.budgeter import estimate_response_tokens

#: Budgeted payloads recorded by ``scripts/measure_mcp_response_sizes.py
#: --responses`` against an indexed repository. Regenerate when a tool's
#: payload changes shape.
_RESPONSES_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "mcp"
    / "token_calibration_responses.json"
)

#: Mean absolute percentage error the rate table has to hold across the corpus.
_MAX_MEAN_ERROR = 0.10

#: No single payload may hide behind the mean.
_MAX_SINGLE_ERROR = 0.15

#: Undercounting is the error that costs a caller a read, so it is held
#: tighter than the symmetric bound.
_MAX_UNDERCOUNT = 0.12


@pytest.fixture(scope="module")
def errors() -> dict[str, float]:
    """Signed relative error per recorded response, estimate against tokenizer."""
    tiktoken = pytest.importorskip(
        "tiktoken", reason="ships transitively with litellm; run `uv sync`"
    )
    try:
        encoding = tiktoken.get_encoding("o200k_base")
    except Exception as exc:  # pragma: no cover - cold vocab cache, no network
        pytest.skip(f"o200k_base unavailable: {exc}")
    payloads: dict[str, Any] = json.loads(_RESPONSES_FIXTURE.read_text(encoding="utf-8"))
    assert payloads, "the recorded corpus is empty"
    measured = {}
    for name, payload in payloads.items():
        wire = json.dumps(payload, separators=(",", ":"), default=str)
        actual = len(encoding.encode(wire))
        measured[name] = (estimate_response_tokens(payload) - actual) / actual
    return measured


def test_estimate_holds_its_mean_error_across_recorded_responses(
    errors: dict[str, float],
) -> None:
    mean = sum(abs(error) for error in errors.values()) / len(errors)

    assert mean <= _MAX_MEAN_ERROR, f"mean error {mean:.3f}: {errors}"


def test_no_single_recorded_response_is_mis_estimated(errors: dict[str, float]) -> None:
    outside = {name: error for name, error in errors.items() if abs(error) > _MAX_SINGLE_ERROR}
    under = {name: error for name, error in errors.items() if error < -_MAX_UNDERCOUNT}

    assert not outside, f"outside +/-{_MAX_SINGLE_ERROR}: {outside}"
    assert not under, f"undercounted past -{_MAX_UNDERCOUNT}: {under}"
