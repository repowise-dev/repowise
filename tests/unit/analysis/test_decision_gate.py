"""Direct unit tests for the anti-hallucination substring gate."""

from __future__ import annotations

from dataclasses import dataclass

from repowise.core.analysis.decisions.gate import apply_substring_gate


@dataclass
class _Candidate:
    decision: str = ""
    rationale: str = ""
    source_quote: str = ""
    source_text: str = ""
    verification: str = "unverified"


def test_gate_clears_echoed_rationale():
    """An echoed rationale matching decision is cleared and candidate survives."""
    sentence = (
        "ChatMarkdown takes a density prop shared across panels and replaces "
        "per-panel prose styles."
    )
    c = _Candidate(
        decision=sentence,
        rationale=sentence,
        source_text=sentence,
    )
    kept, rejected = apply_substring_gate([c])
    assert rejected == 0
    assert len(kept) == 1
    assert kept[0].decision == sentence
    assert kept[0].rationale == ""
    assert kept[0].verification == "exact"


def test_gate_keeps_distinct_grounded_rationale():
    """A distinct grounded rationale is preserved."""
    c = _Candidate(
        decision="ChatMarkdown takes a density prop shared across panels",
        rationale="to replace per-panel prose styles across views",
        source_text=(
            "ChatMarkdown takes a density prop shared across panels "
            "to replace per-panel prose styles across views."
        ),
    )
    kept, rejected = apply_substring_gate([c])
    assert rejected == 0
    assert len(kept) == 1
    assert kept[0].decision == "ChatMarkdown takes a density prop shared across panels"
    assert kept[0].rationale == "to replace per-panel prose styles across views"
    assert kept[0].verification == "exact"
