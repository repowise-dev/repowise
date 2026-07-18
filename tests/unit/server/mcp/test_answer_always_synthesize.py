"""Always-synthesize behaviour for get_answer.

The pre-synthesis dominance gate used to abstain (empty answer + pointer list)
whenever retrieval was not clearly dominant — ~58% of questions. Synthesis now
runs for every retrieval and the post-synthesis grading cascade demotes
confidence instead of abstaining:

  * a non-dominant retrieval returns synthesized PROSE at medium (never high),
    with best_guesses folded in as ambiguous-retrieval evidence;
  * a hedged / ungrounded synthesis still demotes to low, but carries the prose
    and evidence — never an empty answer;
  * REPOWISE_ANSWER_ALWAYS_SYNTHESIZE=off restores the legacy abstain path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _patch_pipeline(monkeypatch, answer_mod, scores=(2.0, 1.9), *, with_symbols=False):
    """Two hits at the given scores; scores default to a non-dominant pair
    (ratio 1.05 < 1.2, top < 3.0)."""

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
                        "name": "go",
                        "kind": "function",
                        "signature": "def go()",
                        "docstring": "",
                        "start_line": 3,
                        "end_line": 9,
                        "_matched": True,
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


@pytest.mark.asyncio
async def test_non_dominant_returns_prose_at_medium_with_evidence(setup_mcp, monkeypatch):
    """(a) A non-dominant retrieval now returns a non-empty answer at
    confidence=medium with best_guesses present (was: empty abstain)."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(2.0, 1.9))
    _patch_provider(monkeypatch, answer_mod, "The go() function drives it (pkg/alpha/one.py).")

    result = await get_answer("how does the alpha module go function work")
    assert result["answer"], "synthesis must run — no abstention"
    assert result["confidence"] == "medium", "non-dominant retrieval caps at medium"
    assert result["best_guesses"], "ambiguous-retrieval evidence folded in"
    assert "ambiguous" in result["note"]


@pytest.mark.asyncio
async def test_hedged_non_dominant_demotes_low_but_keeps_prose(setup_mcp, monkeypatch):
    """(b) A hedged synthesis on non-dominant retrieval still demotes to low,
    but now carries the prose + best_guesses instead of an empty answer."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, scores=(2.0, 1.9))
    _patch_provider(
        monkeypatch,
        answer_mod,
        "The provided excerpts do not contain the implementation details.",
    )

    result = await get_answer("how does the alpha module go function work")
    assert result["confidence"] == "low", "a hedge demotes to low"
    assert result["answer"], "the hedged prose is still served, not an empty answer"
    assert result["best_guesses"], "ambiguous-retrieval evidence folded in"


@pytest.mark.asyncio
async def test_flag_off_restores_legacy_abstain(setup_mcp, monkeypatch):
    """(c) REPOWISE_ANSWER_ALWAYS_SYNTHESIZE=off restores the pre-synthesis
    abstain: empty answer + best_guesses, and synthesis never runs."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setenv("REPOWISE_ANSWER_ALWAYS_SYNTHESIZE", "off")
    _patch_pipeline(monkeypatch, answer_mod, scores=(2.0, 1.9))

    def _no_provider(_p):
        raise AssertionError("flag-off abstain path must not resolve a provider")

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", _no_provider)

    result = await get_answer("how does the alpha module go function work")
    assert result["answer"] == "", "flag off abstains — no synthesized prose"
    assert result["confidence"] == "low"
    assert result["best_guesses"], "abstain path still hands back candidates"


@pytest.mark.asyncio
async def test_dominant_retrieval_unaffected_no_best_guesses(setup_mcp, monkeypatch):
    """Negative control: a dominant retrieval keeps the high-confidence contract
    (no best_guesses fold-in, no ambiguity caveat)."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    # 5.0 vs 4.0: top >= 3.0, gap 1.0 >= 0.5 → dominant; ratio 1.25 → high.
    _patch_pipeline(monkeypatch, answer_mod, scores=(5.0, 4.0), with_symbols=True)
    _patch_provider(monkeypatch, answer_mod, "The go() function drives it (pkg/alpha/one.py).")

    result = await get_answer("how does the alpha module go function work")
    assert result["confidence"] == "high"
    assert "best_guesses" not in result
    assert "ambiguous" not in (result.get("note") or "")
