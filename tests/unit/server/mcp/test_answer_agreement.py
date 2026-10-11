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
        # The real hybrid_retrieve opens a fresh per-request leg record as its
        # first act, and the confidence grade now reads that record. Mimic it,
        # or this fake inherits whatever leg statuses an earlier test left in
        # the ambient context and the grade gets computed from another test's
        # run. (A test that calls begin_leg_record outside a task leaks it.)
        import repowise.server.mcp_server._answer_pipeline as _pipeline

        _pipeline.begin_leg_record()
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
async def test_grounding_earns_high_only_when_opted_in(setup_mcp, monkeypatch):
    """A fully-grounded answer over a NON-dominant retrieval no longer earns high.

    The lift is only ever reachable over a WEAK retrieval — it needs the top score
    to clear the confidence floor, and a retrieval that clears the floor and
    dominates is already high — so this fires exactly where the pipeline is
    reporting it may have surfaced the wrong material. Frame-term grounding
    cannot speak to that: it establishes the answer named nothing retrieval did
    not show it, which rules out a fabricated mechanism and not a well-described
    wrong file. `high` tells the agent to cite without re-reading, so the two
    cannot both hold.

    Opt back in with REPOWISE_ANSWER_EARN_HIGH_ON_WEAK_RETRIEVAL for the previous
    behaviour, and then the note must say the grounding is what it rests on
    rather than claiming a dominance that was never measured.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "on")
    # No agreement lift (top found by one retriever), so grounding is the only
    # route to high under test.
    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "off")
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=False)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how are large uploads handled")
    assert result["retrieval_quality"] == "weak"
    assert result["confidence"] == "medium", "grounded prose does not out-vote weak retrieval"

    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_ON_WEAK_RETRIEVAL", "on")
    result_on = await get_answer("how are large uploads handled")
    assert result_on["confidence"] == "high"
    assert "clearly dominates" not in result_on["note"], (
        "the lift did not measure dominance, so the note must not claim it"
    )

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


# ---------------------------------------------------------------------------
# Keyless indexes: the second opinion is the symbol leg
# ---------------------------------------------------------------------------
#
# #1378 made a keyless index skip the vector leg outright, so `_vec_rank` is
# never written on any hit for any question. A fixed FTS+vector pair therefore
# made agreement permanently unreachable for every keyless user, and since
# agreement can only LIFT, every keyless answer fell back to the pure RRF ratio
# gate - the exact gate this signal exists because it mis-reads. The symbol leg
# runs on every index and already records `_sym_rank`, which nothing else reads.


class TestKeylessAgreementFallsBackToSymbol:
    def test_fts_and_symbol_consensus_lifts_when_the_leg_is_keyless(self) -> None:
        hits = [
            {"_fts_rank": 0, "_sym_rank": 0},
            {"_fts_rank": 1, "_sym_rank": 1},
        ]
        assert _agreement_dominant(hits, vector_leg_keyless=True) is True

    def test_symbol_disagreement_still_does_not_lift(self) -> None:
        """The fallback must be conservative, not a free pass.

        Note this case is rejected by the rank-0 ceiling, not by the runner-up
        disagreement logic — at that ceiling the gap check cannot reject
        anything, since two hits cannot share rank 0 in one leg. Kept because
        the property (sources disagreeing must not lift) is what matters, not
        which clause enforces it.
        """
        hits = [
            {"_fts_rank": 1, "_sym_rank": 0},
            {"_fts_rank": 0, "_sym_rank": 2},
        ]
        assert _agreement_dominant(hits, vector_leg_keyless=True) is False

    def test_fts_only_does_not_lift_on_a_keyless_index(self) -> None:
        """One retriever is one retriever, whichever legs are available."""
        hits = [{"_fts_rank": 0}, {"_fts_rank": 1}]
        assert _agreement_dominant(hits, vector_leg_keyless=True) is False

    def test_symbol_pair_needs_an_exact_rank_zero_tie(self) -> None:
        """FTS and symbol read overlapping text — the indexed page carries the
        symbol table the symbol leg matches on — so "#1 or #2" is too loose for
        that pair even though it is the right ceiling for FTS+vector."""
        hits = [
            {"_fts_rank": 1, "_sym_rank": 1},
            {"_fts_rank": 3, "_sym_rank": 3},
        ]
        assert _agreement_dominant(hits, vector_leg_keyless=True) is False
        # The identical rank shape DOES lift under the vector pair's ceiling,
        # which is what makes this a deliberate asymmetry rather than a typo.
        assert (
            _agreement_dominant(
                [{"_fts_rank": 1, "_vec_rank": 1}, {"_fts_rank": 3, "_vec_rank": 3}]
            )
            is True
        )

    def test_a_keyed_index_never_substitutes_the_symbol_leg(self) -> None:
        """The regression the explicit parameter exists to prevent.

        `hits` is capped to the top 5 before the grade is computed, so "no
        _vec_rank in this list" is ALSO what a keyed index looks like when its
        vector leg timed out, errored, was scope-filtered, or was outranked by
        five FTS-and-symbol hits. Those are precisely the states where evidence
        is weakest, so inferring the substitution from the hits would
        manufacture high confidence out of a retrieval failure.
        """
        hits = [
            {"_fts_rank": 0, "_sym_rank": 0},
            {"_fts_rank": 1, "_sym_rank": 1},
        ]
        assert _agreement_dominant(hits, vector_leg_keyless=False) is False
        # The default is the safe one, for any caller that forgets to pass it.
        assert _agreement_dominant(hits) is False

    def test_a_live_vector_leg_that_disagrees_still_suppresses(self) -> None:
        hits = [
            {"_fts_rank": 0, "_sym_rank": 0, "_vec_rank": 7},
            {"_fts_rank": 1, "_sym_rank": 1, "_vec_rank": 0},
        ]
        assert _agreement_dominant(hits) is False


# ---------------------------------------------------------------------------
# The call-site seam
# ---------------------------------------------------------------------------
#
# `vector_leg_keyless` defaults to the safe value, which means dropping the
# kwarg at the call site silently restores the keyless hole and every unit test
# above still passes. This is the one seam the explicit-parameter design
# created, so it gets an end-to-end test that goes through `retrieval_legs()`.


def _patch_keyless_agreement_pipeline(monkeypatch, answer_mod, pipeline_mod, *, leg: str):
    """FTS+symbol consensus, no vector ranks, and a declared vector leg status."""

    async def _fake_retrieve(question, ctx):
        # begin_leg_record() installs the per-request contextvar and hands back
        # the dict the real legs mutate by reference; do the same here.
        record = pipeline_mod.begin_leg_record()
        record["vector"] = leg
        record["fts"] = "ok"
        return [
            {
                "page_id": "file_page:pkg/alpha/one.py",
                "score": 6.0,
                "_fts_rank": 0,
                "_sym_rank": 0,
            },
            {
                "page_id": "file_page:pkg/alpha/two.py",
                "score": 5.9,
                "_fts_rank": 1,
                "_sym_rank": 1,
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


@pytest.mark.asyncio
async def test_keyless_leg_status_reaches_the_grade(setup_mcp, monkeypatch):
    """A recorded `keyless` vector leg lets FTS+symbol consensus lift to high."""
    import repowise.server.mcp_server._answer_pipeline as pipeline_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    monkeypatch.setenv("REPOWISE_ANSWER_SYMBOL_AGREEMENT", "on")
    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    _patch_keyless_agreement_pipeline(monkeypatch, answer_mod, pipeline_mod, leg="keyless")
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how does upload chunking work")
    assert result["confidence"] == "high"


@pytest.mark.asyncio
async def test_a_failed_vector_leg_is_not_treated_as_keyless(setup_mcp, monkeypatch):
    """The regression that matters: identical hits, but the vector leg TIMED OUT.

    A keyed index whose vector leg timed out presents exactly like a keyless one
    once `hits` is capped to five, and it must NOT earn the lift — that is the
    state where the evidence is weakest, not strongest.
    """
    import repowise.server.mcp_server._answer_pipeline as pipeline_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    monkeypatch.setenv("REPOWISE_ANSWER_SYMBOL_AGREEMENT", "on")
    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    _patch_keyless_agreement_pipeline(monkeypatch, answer_mod, pipeline_mod, leg="timeout")
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how does upload chunking work")
    assert result["confidence"] == "medium"


@pytest.mark.asyncio
async def test_symbol_agreement_flag_is_independently_reversible(setup_mcp, monkeypatch):
    """Turning the keyless pair off must not turn the measured vector pair off."""
    import repowise.server.mcp_server._answer_pipeline as pipeline_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_AGREEMENT_CONFIDENCE", "on")
    monkeypatch.setenv("REPOWISE_ANSWER_SYMBOL_AGREEMENT", "off")
    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_GROUNDING", "off")
    _patch_keyless_agreement_pipeline(monkeypatch, answer_mod, pipeline_mod, leg="keyless")
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)

    result = await get_answer("how does upload chunking work")
    assert result["confidence"] == "medium"

    # ...and the vector pair still lifts with the symbol flag off.
    _patch_agreement_pipeline(monkeypatch, answer_mod, top_both=True)
    _patch_provider(monkeypatch, answer_mod, _GOOD_ANSWER)
    assert (await get_answer("how does upload chunking work again"))["confidence"] == "high"
