"""Confidence calibration gates for get_answer.

Covers the three A-series gates that ground confidence in answer *content*
rather than retrieval scores alone:

  * expanded hedge markers ("do not include", "can't enumerate", …)
  * value-grounding gate — numbers asserted on value-shaped questions must
    appear in the retrieved material, else confidence caps at low
  * citation-source gate — high confidence requires ≥1 cited page that
    contributed actual source (hydrated symbols), not just summaries
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_answer.confidence import (
    _answer_is_hedged,
    _is_value_question,
    _ungrounded_numbers,
)

# ---------------------------------------------------------------------------
# Pure-predicate unit tests
# ---------------------------------------------------------------------------


class TestHedgeMarkers:
    def test_do_not_include_detected(self) -> None:
        assert _answer_is_hedged("The provided wiki excerpts do not include the source body.")

    def test_cannot_enumerate_detected(self) -> None:
        assert _answer_is_hedged("I can't enumerate the exact callers from the excerpts.")

    def test_unable_to_determine_detected(self) -> None:
        assert _answer_is_hedged("The exact default is unable to determine from this material.")

    def test_marker_late_in_answer_detected(self) -> None:
        long_preamble = "The module handles ingestion. " * 40
        assert _answer_is_hedged(long_preamble + "However, the excerpts do not include the value.")

    def test_direct_answer_not_hedged(self) -> None:
        assert not _answer_is_hedged("The default is 2, set in git_indexer/_constants.py.")


class TestValueQuestionShape:
    def test_default_question_is_value_shaped(self) -> None:
        assert _is_value_question("What is the default value of _MIN_COUNT?")

    def test_threshold_question_is_value_shaped(self) -> None:
        assert _is_value_question("what threshold gates the dominance ratio")

    def test_how_many_is_value_shaped(self) -> None:
        assert _is_value_question("How many retrieval hits are returned?")

    def test_mechanism_question_is_not(self) -> None:
        assert not _is_value_question("How does the ingestion pipeline parse imports?")


class TestUngroundedNumbers:
    HITS = [
        {
            "title": "constants",
            "summary": "Co-change tuning constants.",
            "snippet": "",
            "symbols": [
                {
                    "name": "_DEFAULT_CO_CHANGE_MIN_COUNT",
                    "signature": "_DEFAULT_CO_CHANGE_MIN_COUNT = 2",
                    "docstring": "",
                }
            ],
        }
    ]

    def test_invented_number_is_ungrounded(self) -> None:
        assert _ungrounded_numbers("The default minimum count is 3.", self.HITS) == ["3"]

    def test_number_present_in_signature_is_grounded(self) -> None:
        assert _ungrounded_numbers("The default minimum count is 2.", self.HITS) == []

    def test_file_line_citations_are_not_value_assertions(self) -> None:
        text = "The default is 2 (git_indexer/_constants.py:114)."
        assert _ungrounded_numbers(text, self.HITS) == []

    def test_no_numbers_in_answer_is_grounded(self) -> None:
        assert _ungrounded_numbers("It reads the tuning constant from config.", self.HITS) == []


# ---------------------------------------------------------------------------
# End-to-end gate behaviour through get_answer
# ---------------------------------------------------------------------------

_SYMBOL = {
    "name": "MIN_COUNT",
    "kind": "constant",
    "signature": "MIN_COUNT = 2",
    "docstring": "",
    "start_line": 10,
    "end_line": 10,
    "_matched": True,
}


def _patch_pipeline(monkeypatch, answer_mod, *, with_symbols: bool):
    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": 5.0},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 4.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Auth service summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if with_symbols and i == 0:
                h["symbols"] = [dict(_SYMBOL)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content: str):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


@pytest.mark.asyncio
async def test_ungrounded_value_caps_confidence_low(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The default of MIN_COUNT is 3 (pkg/alpha/one.py).",
    )

    result = await get_answer("What is the default value of MIN_COUNT?")
    assert result["confidence"] == "low"
    assert "3" in result["note"]
    assert "next_action_hint" in result


@pytest.mark.asyncio
async def test_grounded_value_keeps_high_confidence(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The default of MIN_COUNT is 2 (pkg/alpha/one.py).",
    )

    result = await get_answer("What is the default value of MIN_COUNT?")
    assert result["confidence"] == "high"
    assert "High confidence" in result["note"]


@pytest.mark.asyncio
async def test_high_confidence_requires_source_backed_citation(setup_mcp, monkeypatch):
    """No cited page contributed symbols → high is downgraded to medium."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=False)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "Auth flows through middleware into the service (pkg/alpha/one.py).",
    )

    result = await get_answer("how does the auth flow work end to end")
    assert result["confidence"] == "medium"


@pytest.mark.asyncio
async def test_expanded_hedge_marker_downgrades_through_pipeline(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, with_symbols=True)
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not include the body of MIN_COUNT.",
    )

    result = await get_answer("What is the default value of MIN_COUNT?")
    assert result["confidence"] == "low"
    assert result["retrieval"] == []
