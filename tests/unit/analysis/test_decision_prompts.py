"""Every gated extraction prompt asks for ``decision`` and ``rationale`` verbatim.

The substring gate blanks any field the source does not contain, so a prompt
that invites a reworded decision or reason stores the record without it.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.decisions.prompts import (
    _SYSTEM_PROMPT,
    COMMENT_ARCHAEOLOGY_PROMPT,
    GIT_ARCHAEOLOGY_PROMPT,
    INLINE_MARKER_PROMPT,
    PR_BODY_MINING_PROMPT,
    README_MINING_PROMPT,
)

_GATED_PROMPTS = [
    INLINE_MARKER_PROMPT,
    GIT_ARCHAEOLOGY_PROMPT,
    README_MINING_PROMPT,
    PR_BODY_MINING_PROMPT,
    COMMENT_ARCHAEOLOGY_PROMPT,
]


def _field_line(prompt: str, field: str) -> str:
    return next(ln for ln in prompt.splitlines() if ln.strip().startswith(f'"{field}"'))


@pytest.mark.parametrize("prompt", _GATED_PROMPTS)
def test_the_decision_field_is_asked_for_verbatim(prompt: str) -> None:
    assert "copied verbatim" in _field_line(prompt, "decision")


@pytest.mark.parametrize("prompt", _GATED_PROMPTS)
def test_the_rationale_field_is_asked_for_verbatim(prompt: str) -> None:
    line = _field_line(prompt, "rationale")
    assert "copied verbatim" in line
    # An invitation to reword or infer is what the gate blanks.
    assert "paraphrase" not in line
    assert "infer" not in line


def test_the_system_prompt_forbids_rewording_the_rationale() -> None:
    assert "decision and rationale fields word for word" in _SYSTEM_PROMPT
