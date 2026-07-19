"""Precision tests for ``is_mechanism_question`` (union-deferral signal).

The predicate decides whether a question that NAMES an indexed symbol should
defer the exact_symbol union fast path to synthesis (mechanism/how questions) or
answer by dumping the symbol's bodies (naming/lookup/value questions). These
cases are deliberately NOT drawn from the get_answer gold eval set — they probe
the question SHAPES the predicate must separate, so a pass here is evidence the
signal generalises rather than fits the 22 benchmarked questions.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._answer_context import is_mechanism_question

# Mechanism / "how does X work" — MUST defer the union to synthesis, because the
# mechanism can live in a file other than the named symbol's body.
_MECHANISM = [
    "How does get_symbol verify that stored symbol bounds match the live file?",
    "how do ingestion symbol bounds end up used by MCP skeleton rendering",
    "How is unreachable dead code detected across the repository?",
    "how does retrieval feed synthesis in get_answer",
    "How does search_codebase route a query to symbol, path, or concept mode?",
    "Explain how the hydrator turns stored bounds into signatures and bodies.",
    "Trace how a parsed symbol's line range travels into a get_answer body.",
    "Walk me through how decision records are down-weighted when ranking.",
    "Describe how get_why surfaces architectural rationale with evidence.",
    "Outline the flow from a changed file to re-persisted symbols.",
    "step through how a retrieval hit becomes an enriched symbol body",
]

# Naming / lookup / value — MUST NOT defer; the union-of-bodies reply is correct.
_NOT_MECHANISM = [
    # naming: what/where lookups, including ones that contain a mechanism VERB
    "What does get_answer return when a question names a symbol with several defs?",
    "Where is filter_dicts_by_key defined?",
    "What are the definitions of render_widget in this repo?",
    "where is the compute_score function defined",  # 'compute' is a verb, still a lookup
    "what does route_request return",               # 'route' is a verb, still a lookup
    "which file defines reconcile_symbols_for_files",
    "reconcile_symbols_for_files",                  # bare identifier
    # value / quantity: "how many/much/long/large" are value questions
    "How many definitions of get_symbol are there?",
    "how much memory does the ComplexityWalker use",
    "how long is the default retry budget",
    "how large is the token budget for skeletons",
    # "how come" is a why-in-disguise, not a mechanism ask
    "how come the dominance threshold is 1.2",
]


@pytest.mark.parametrize("q", _MECHANISM)
def test_mechanism_questions_defer(q: str) -> None:
    assert is_mechanism_question(q) is True, q


@pytest.mark.parametrize("q", _NOT_MECHANISM)
def test_naming_and_value_questions_do_not_defer(q: str) -> None:
    assert is_mechanism_question(q) is False, q


def test_empty_is_not_mechanism() -> None:
    assert is_mechanism_question("") is False
    assert is_mechanism_question(None) is False  # type: ignore[arg-type]
