"""The reason a get_answer response is `high`, and whether it survives weak retrieval.

Three claims, all about the same seam between the confidence grade and the note
the payload writes from it:

  * **One owner for dominance.** ``answer.py`` rated retrieval with a two-tier
    test (absolute gap above a strong top score, ratio below it) while the grade
    cascade re-derived a ratio-only version. The two disagreed, so a retrieval
    the pipeline treats as dominant everywhere else could still be graded as if
    it were not.
  * **The note must not claim a reason it did not check.** "Top retrieval result
    clearly dominates (dominance ratio 1.00x)" was written on the bare fact that
    confidence was high, and a ratio of 1.00 is a tie. Printed beside the
    ambiguous-retrieval caveat it produced a payload that asserted dominance and
    denied it in the same breath.
  * **Grounding does not out-vote weak retrieval; a served body does.** Both
    routes to an earned high used to bypass the non-dominance ceiling. Only one
    of them should: a question-named symbol body is live source sitting in the
    payload, so the ranking's ambiguity says nothing about it, whereas
    "every mechanism term the answer names appears in what it was shown" only
    measures the prose against material whose selection is exactly what
    ``retrieval_quality: weak`` reports as ambiguous.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# A symbol name distinctive enough for _distinctive_terms (underscore), so an
# answer that names it has a GROUNDED mechanism term and can earn high through
# the grounding route. Deliberately absent from every question below: a term the
# question itself supplied is excluded from the grounding count.
_SYMBOL = "run_sweep"


def _patch_pipeline(monkeypatch, answer_mod, scores, *, with_symbols=True, matched=False):
    """Two file-page hits at *scores*, the top one carrying a hydrated symbol."""

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:pkg/alpha/one.py", "score": scores[0], "_sources": {"fts"}},
            {"page_id": "file_page:pkg/alpha/two.py", "score": scores[1], "_sources": {"fts"}},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for i, h in enumerate(hits):
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "Module summary for the page. " * 4
            h["snippet"] = ""
            h["page_type"] = "file_page"
            if with_symbols and i == 0:
                h["symbols"] = [
                    {
                        "name": _SYMBOL,
                        "kind": "function",
                        "signature": f"def {_SYMBOL}()",
                        "docstring": "",
                        "start_line": 1,
                        "end_line": 3,
                        **({"_matched": True} if matched else {}),
                    }
                ]
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _patch_provider(monkeypatch, answer_mod, content):
    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=content)

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())


# Names the top hit (so it is cited) and one grounded mechanism term (so the
# grounding route to an earned high is open).
_GROUNDED_ANSWER = f"The `{_SYMBOL}` helper drives it (pkg/alpha/one.py)."
# Same citation, no distinctive term at all — the grounding route stays shut, so
# only the dominance route can produce a high.
_PLAIN_ANSWER = "The module drives it (pkg/alpha/one.py)."
_QUESTION = "how does the alpha module clear out stale rows"


@pytest.mark.asyncio
async def test_ambiguous_retrieval_never_reads_as_dominant(setup_mcp, monkeypatch):
    """The reported defect: one payload claiming dominance and ambiguity at once.

    A tied pair (ratio 1.00x) above the score floor, answered with fully grounded
    prose. Driven with the weak-retrieval opt-in ON so the grade still reaches
    high, which is what puts the note on the branch that produced the report —
    testing this at the default `medium` would prove nothing about the note,
    since a non-high grade never enters that branch at all.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_EARN_HIGH_ON_WEAK_RETRIEVAL", "on")
    _patch_pipeline(monkeypatch, answer_mod, scores=(2.91, 2.91))
    _patch_provider(monkeypatch, answer_mod, _GROUNDED_ANSWER)

    result = await get_answer(_QUESTION)
    note = result.get("note") or ""
    assert result["confidence"] == "high", "the opt-in must actually reach the high branch"
    assert result["retrieval_quality"] == "weak", "a tied top pair is ambiguous retrieval"
    assert not ("dominates" in note and "ambiguous" in note), (
        f"note asserts dominance and ambiguity together: {note!r}"
    )
    assert "dominance ratio 1.00x" not in note, "a 1.00x ratio is a tie, not dominance"
    assert "do not re-read the source" not in note


@pytest.mark.asyncio
async def test_grounded_prose_does_not_earn_high_on_weak_retrieval(setup_mcp, monkeypatch):
    """Grounding measures the prose against the material, not the material's fit.

    On weak retrieval the answer can be perfectly consistent with everything it
    was shown and still be about the wrong page, so this must not reach the
    "cite this; do not re-read the source" contract.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(2.91, 2.91))
    _patch_provider(monkeypatch, answer_mod, _GROUNDED_ANSWER)

    result = await get_answer(_QUESTION)
    assert result["retrieval_quality"] == "weak"
    assert result["confidence"] == "medium", (
        "answer-grounding alone must not out-vote an ambiguous retrieval"
    )
    assert "do not re-read the source" not in (result.get("note") or "")


@pytest.mark.asyncio
async def test_dominance_grade_honours_the_absolute_gap_tier(setup_mcp, monkeypatch):
    """One owner: the grade uses the same dominance test the pipeline does.

    Fusion scales these to 5.60 / 5.04, which is dominant under the two-tier rule
    (top >= 3.0, gap 0.56 >= 0.5) but NOT under a bare ratio (1.11 < 1.2). The
    grade re-derived the ratio-only version, so this retrieval was capped at
    medium while the same numbers were treated as dominant for the caveat and the
    ceiling — the two answering differently about one retrieval.

    The plain answer keeps the grounding route shut, so dominance is the only
    thing under test.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(8.0, 7.2))
    _patch_provider(monkeypatch, answer_mod, _PLAIN_ANSWER)

    result = await get_answer(_QUESTION)
    assert result["confidence"] == "high"
    assert "ambiguous" not in (result.get("note") or ""), "no caveat on a dominant retrieval"
    assert "clearly dominates" in result["note"]


@pytest.mark.asyncio
async def test_the_gap_tier_does_not_quote_the_ratio_it_did_not_use(setup_mcp, monkeypatch):
    """The same self-refuting number, one tier down.

    The gap tier exists precisely because the ratio is uninformative when both
    scores are strong, so a note that earns high by gap and then quotes "clearly
    dominates (dominance ratio 1.11x)" is citing the measurement it declined to
    make. Report the gap, which is what was measured.
    """
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(8.0, 7.2))
    _patch_provider(monkeypatch, answer_mod, _PLAIN_ANSWER)

    result = await get_answer(_QUESTION)
    note = result["note"]
    assert result["confidence"] == "high"
    assert "dominance ratio" not in note, "the ratio is not what this tier measured"
    assert "points over the runner-up" in note


@pytest.mark.asyncio
async def test_agreement_note_says_agreement_not_a_compressed_ratio(setup_mcp, monkeypatch):
    """Fusion compresses agreeing pairs to roughly 1.02x, so the ratio is again
    not the measurement — the two retrievers concurring is."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    async def _fake_retrieve(question, ctx):
        import repowise.server.mcp_server._answer_pipeline as _pipeline

        _pipeline.begin_leg_record()
        return [
            {
                "page_id": "file_page:pkg/alpha/one.py",
                "score": 6.0,
                "_fts_rank": 0,
                "_vec_rank": 0,
            },
            {
                "page_id": "file_page:pkg/alpha/two.py",
                "score": 5.9,
                "_fts_rank": 1,
                "_vec_rank": 1,
            },
        ]

    _patch_pipeline(monkeypatch, answer_mod, scores=(6.0, 5.9))
    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    _patch_provider(monkeypatch, answer_mod, _PLAIN_ANSWER)

    result = await get_answer(_QUESTION)
    note = result["note"]
    assert result["confidence"] == "high"
    assert "both retrievers independently rank this page at the top" in note
    assert "clearly dominates" not in note


def _patch_anchor(monkeypatch, answer_mod, anchored):
    """Attach *anchored* to the top hit, as if the question named an indexed
    symbol whose defining file was promoted into the candidate set."""

    async def _fake_anchor(session, repo_id, question_ids, hits, **kwargs):
        if hits:
            hits[0]["_anchor_symbols"] = [anchored]
        return hits, {"union": {}, "qualified_miss": []}

    monkeypatch.setattr(answer_mod, "_anchor_symbol_hits", _fake_anchor)


@pytest.mark.asyncio
async def test_a_served_named_body_still_earns_high_on_weak_retrieval(
    setup_mcp, monkeypatch, tmp_path
):
    """The other half of the split: a body in the payload DOES out-vote the ranking.

    The symbol was resolved by its name, not by the ranking, and its live source
    is inlined in the response — so "retrieval was ambiguous" is a statement
    about the candidate pages, not about the evidence this answer rests on.
    The note has to say that rather than claim a dominance of 1.00x, and the
    caveat must not send the agent off to verify against best_guesses when the
    confidence deliberately does not rest on them.
    """
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    (tmp_path / "pkg" / "alpha").mkdir(parents=True)
    (tmp_path / "pkg" / "alpha" / "one.py").write_text(
        f"def {_SYMBOL}() -> int:\n    # drop rows past the horizon\n    return PURGED\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))

    # A tied pair is non-dominant at any scale, so this stays weak however fusion
    # rescales it; the magnitude is only there to clear the confidence floor,
    # which anchoring lowers the fused score enough to matter for.
    _patch_pipeline(monkeypatch, answer_mod, scores=(8.0, 8.0), matched=True)
    _patch_anchor(
        monkeypatch,
        answer_mod,
        {"name": _SYMBOL, "kind": "function", "start_line": 1, "end_line": 3},
    )
    _patch_provider(monkeypatch, answer_mod, _GROUNDED_ANSWER)

    result = await get_answer(f"How does {_SYMBOL} work?")
    assert result["retrieval_quality"] == "weak"
    assert result["symbol_bodies"], "the named body is what the grade rests on"
    assert result["confidence"] == "high"
    # Also what keeps REPOWISE_ANSWER_LEAN_HIGH from stripping symbol_bodies out
    # of a high-confidence payload whose note points straight at it.
    assert result["grounding"] == "symbol_body"
    note = result["note"]
    assert "symbol_bodies" in note, "the note must name the evidence it actually used"
    assert "clearly dominates" not in note
    assert "verify against best_guesses" not in note
    assert result["next_action_hint"] == "Use the answer and citations directly."
