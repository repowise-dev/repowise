"""Agreement-aware confidence for get_answer.

RRF fusion compresses retrieval scores: a page BOTH retrievers rank #1 barely
outscores one they rank #2 (fused ratio ~1.017), so the numeric dominance-ratio
gate calls the *most* confident retrieval "non-dominant" and demotes it to
medium/low. These tests pin the fix: retriever *agreement* (the same page at/near
the top of both FTS and vector) lifts confidence to high even when the fused
scores don't numerically dominate — subject to the existing demotion gates, and
fully reversible via ``REPOWISE_ANSWER_AGREEMENT_CONFIDENCE=off``.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server.tool_answer.answer import _agreement_dominant


# ---------------------------------------------------------------------------
# Pure-predicate unit tests for the agreement signal
# ---------------------------------------------------------------------------


class TestAgreementDominant:
    def test_consensus_top_lifts(self) -> None:
        # Top page is #1 in both retrievers; runner-up is #2 in both.
        hits = [
            {"_fts_rank": 0, "_vec_rank": 0},
            {"_fts_rank": 1, "_vec_rank": 1},
        ]
        assert _agreement_dominant(hits) is True

    def test_top_found_by_one_retriever_does_not_lift(self) -> None:
        # Top hit missing from vector → ambiguous, must NOT lift.
        hits = [
            {"_fts_rank": 0},
            {"_fts_rank": 1, "_vec_rank": 0},
        ]
        assert _agreement_dominant(hits) is False

    def test_runner_up_by_one_retriever_lifts(self) -> None:
        # Top is a consensus pick; runner-up surfaced in only one source.
        hits = [
            {"_fts_rank": 0, "_vec_rank": 0},
            {"_fts_rank": 1},
        ]
        assert _agreement_dominant(hits) is True

    def test_top_not_near_top_does_not_lift(self) -> None:
        hits = [
            {"_fts_rank": 3, "_vec_rank": 3},
            {"_fts_rank": 4, "_vec_rank": 4},
        ]
        assert _agreement_dominant(hits) is False

    def test_source_disagreement_does_not_lift(self) -> None:
        # Runner-up beats the (RRF-)top in FTS → no clean consensus.
        hits = [
            {"_fts_rank": 1, "_vec_rank": 0},
            {"_fts_rank": 0, "_vec_rank": 2},
        ]
        assert _agreement_dominant(hits) is False

    def test_single_hit_is_not_agreement(self) -> None:
        assert _agreement_dominant([{"_fts_rank": 0, "_vec_rank": 0}]) is False


# ---------------------------------------------------------------------------
# End-to-end grade tests (agreement lifts a compressed-score retrieval)
# ---------------------------------------------------------------------------


def _patch_provider(monkeypatch, answer_mod, content: str):
    from types import SimpleNamespace

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


_MATCHED_SYMBOL = {
    "name": "chunk_upload",
    "kind": "function",
    "signature": "def chunk_upload(body) -> None",
    "docstring": "Streams the body in fixed chunks.",
    "start_line": 10,
    "end_line": 20,
    "_matched": False,
}


def _patch_agreement_pipeline(monkeypatch, answer_mod, *, top_both: bool):
    """Two near-tied hits (fused ratio < 1.2) with per-source ranks.

    ``top_both`` toggles whether the top hit is a consensus pick (found by both
    retrievers at rank 0) or a one-retriever hit — the difference between
    agreement-lift firing and not.
    """

    async def _fake_retrieve(question, ctx):
        top = {"page_id": "file_page:pkg/alpha/one.py", "score": 6.0, "_fts_rank": 0}
        if top_both:
            top["_vec_rank"] = 0
        return [
            top,
            {
                "page_id": "file_page:pkg/alpha/two.py",
                "score": 5.9,
                "_fts_rank": 1,
                "_vec_rank": 1,
            },
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Upload module summary."
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if i == 0:
                h["symbols"] = [dict(_MATCHED_SYMBOL)]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


# A non-hedged answer that cites the top file so the citation-source gate passes.
_GOOD_ANSWER = "Uploads are streamed in fixed-size chunks by chunk_upload in pkg/alpha/one.py."
# A hedged answer — the demotion gate must still pull an agreement-dominant hit down.
_HEDGED_ANSWER = "The excerpts do not contain the chunking logic; you should inspect the source."


@pytest.mark.asyncio
async def test_agreement_lifts_compressed_retrieval_to_high(setup_mcp, monkeypatch):
    """(a) Both retrievers rank the top page #1; fused ratio < 1.2 → still high."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=True)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how does upload chunking work")
    assert result["confidence"] == "high"
    assert result["retrieval_quality"] == "high"


@pytest.mark.asyncio
async def test_one_retriever_top_does_not_get_agreement_lift(setup_mcp, monkeypatch):
    """(b) Top hit found by only one retriever → no lift; stays medium."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    # Isolate agreement: disable the grounding-earn lift, a separate path to high
    # (this well-grounded answer would otherwise earn high on its own).
    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=False)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how are large uploads handled")
    assert result["confidence"] == "medium"


@pytest.mark.asyncio
async def test_demotion_gate_still_fires_on_agreement_hit(setup_mcp, monkeypatch):
    """(c) Agreement-dominant retrieval + hedged synthesis → still demoted."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=True)
    _patch_provider(monkeypatch, answer_mod, _HEDGED_ANSWER)

    result = await get_answer("how does chunk buffering behave")
    assert result["confidence"] != "high"


@pytest.mark.asyncio
async def test_earn_high_via_grounding_lifts_nondominant(setup_mcp, monkeypatch):
    """A NON-dominant retrieval (no agreement, ratio < 1.2) whose answer is fully
    grounded — cites a hit carrying the named symbol body and introduces no
    ungrounded mechanism term — EARNS high. Flag off → medium."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "on")
    # No agreement lift (top found by one retriever); the only path to high is
    # the grounding-earn.
    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "off")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=False)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how are large uploads handled")
    assert result["confidence"] == "high"

    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    result_off = await get_answer("how are large uploads handled")
    assert result_off["confidence"] == "medium"


@pytest.mark.asyncio
async def test_flag_off_restores_pure_ratio(setup_mcp, monkeypatch):
    """(d) Flag off → agreement is ignored, compressed ratio grades medium."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "off")
    # Isolate the ratio path: the grounding-earn is a separate high-lift lever.
    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=True)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how does upload streaming chunk data")
    assert result["confidence"] == "medium"
