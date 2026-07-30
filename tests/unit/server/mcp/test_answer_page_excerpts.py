"""Every retrieval gets the page's prose, including the confident ones.

Page content used to be attached only when retrieval was *not* dominant — and
dominance is what earns high confidence. So the more certain retrieval was,
the less prose the model was given: a confident answer was built from the
page's one-line summary plus a symbol table of names, while an ambiguous one
got 1500 chars of the actual page. Answers written from names read as
confident and reconstruct rationale that is not there.

These tests hold the two halves of that: prose reaches the prompt on a
dominant retrieval, and a hit that reaches synthesis with no page content
behind it is counted and logged rather than passing silently.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import Page

_PAGE_IDS = ("file_page:pkg/alpha/one.py", "file_page:pkg/alpha/two.py")
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

# Long enough that a summary-sized snippet would not reach the marker, short
# enough that the marker survives any per-hit cap in play — what is under test
# here is whether page prose arrives at all, not how much of it does.
_BODY = ("## Overview\n\nThe alpha module owns the write path. " * 10) + (
    "HARD INVARIANT: it only ever writes the returned result object."
)
_MARKER = "HARD INVARIANT: it only ever writes the returned result object."


async def _add_pages(factory, repo_id: str, content: str) -> None:
    async with factory() as s:
        for page_id in _PAGE_IDS:
            s.add(
                Page(
                    id=page_id,
                    repository_id=repo_id,
                    page_type="file_page",
                    title=page_id.removeprefix("file_page:"),
                    content=content,
                    target_path=page_id.removeprefix("file_page:"),
                    source_hash=page_id,
                    model_name="mock",
                    provider_name="mock",
                    generation_level=2,
                    confidence=0.9,
                    freshness_status="fresh",
                    created_at=_NOW,
                    updated_at=_NOW,
                )
            )
        await s.commit()


def _patch_pipeline(monkeypatch, answer_mod, *, dominant: bool) -> None:
    """``dominant=True`` is a runaway top score — the branch that used to be
    denied page content. ``False`` is the ambiguous pair that always had it."""
    scores = (5.0, 1.0) if dominant else (2.0, 1.9)

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": _PAGE_IDS[0], "score": scores[0], "_sources": {"fts"}},
            {"page_id": _PAGE_IDS[1], "score": scores[1], "_sources": {"fts"}},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h["title"] = h["target_path"]
            h["summary"] = "One-line summary of the page."
            h["snippet"] = ""
            h["page_type"] = "file_page"
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


def _capture_prompt(monkeypatch, answer_mod, answer_text: str) -> list[str]:
    """Record the user prompt handed to synthesis; short-circuit the LLM."""
    seen: list[str] = []

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

        async def generate(self, **kwargs):
            return SimpleNamespace(content=answer_text)

    async def _fake_synthesize(provider, system_prompt, user_prompt, **_kwargs):
        seen.append(user_prompt)
        return answer_text, None

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())
    monkeypatch.setattr(answer_mod, "synthesize", _fake_synthesize)
    return seen


@pytest.mark.asyncio
async def test_dominant_retrieval_prompt_carries_page_prose(setup_mcp, factory, monkeypatch):
    """The regression: a confident retrieval's context block contains the
    page's body text, not only its summary."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _add_pages(factory, setup_mcp, _BODY)
    _patch_pipeline(monkeypatch, answer_mod, dominant=True)
    prompts = _capture_prompt(
        monkeypatch, answer_mod, "The alpha module owns the write path (pkg/alpha/one.py)."
    )

    await get_answer("how does the alpha module handle the write path")

    assert prompts, "synthesis must run"
    assert _MARKER in prompts[0], (
        "a dominant retrieval's prompt must carry the page's real prose, not "
        "only its one-line summary"
    )


@pytest.mark.asyncio
async def test_ambiguous_retrieval_still_carries_page_prose(setup_mcp, factory, monkeypatch):
    """The path that already worked keeps working — this change moves the call,
    it does not trade one branch for the other."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _add_pages(factory, setup_mcp, _BODY)
    _patch_pipeline(monkeypatch, answer_mod, dominant=False)
    prompts = _capture_prompt(monkeypatch, answer_mod, "Alpha owns writes (pkg/alpha/one.py).")

    await get_answer("how does the alpha module handle the write path")

    assert prompts, "synthesis must run"
    assert _MARKER in prompts[0]


@pytest.mark.asyncio
async def test_hit_with_no_page_content_is_counted_and_logged(
    setup_mcp, factory, monkeypatch, caplog
):
    """A hit reaching synthesis with nothing to read is the state that made the
    original inversion invisible. It must be counted, and it must not raise."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _add_pages(factory, setup_mcp, "")  # rows exist, bodies empty
    _patch_pipeline(monkeypatch, answer_mod, dominant=True)
    prompts = _capture_prompt(monkeypatch, answer_mod, "Alpha owns writes (pkg/alpha/one.py).")

    with caplog.at_level(logging.WARNING, logger="repowise.mcp.answer"):
        result = await get_answer("how does the alpha module handle the write path")

    assert result["answer"], "a missing page body degrades the prompt, never the response"
    assert prompts and "One-line summary of the page." in prompts[0]
    assert any("no page content" in r.getMessage() for r in caplog.records), (
        "hits served to synthesis without page content must be reported"
    )


@pytest.mark.asyncio
async def test_page_fetch_failure_warns_and_does_not_break_the_answer(
    setup_mcp, monkeypatch, caplog
):
    """The fetch is best-effort, but its failure is not silent: the prompt
    quietly falling back to summaries is exactly the invisible degradation
    this whole change is about."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    import repowise.server.mcp_server.tool_answer.retrieval as retrieval_mod
    from repowise.server.mcp_server import get_answer

    _patch_pipeline(monkeypatch, answer_mod, dominant=True)
    _capture_prompt(monkeypatch, answer_mod, "Alpha owns writes (pkg/alpha/one.py).")

    def _boom(*a, **kw):
        raise RuntimeError("database gone")

    monkeypatch.setattr(retrieval_mod, "get_session", _boom)

    with caplog.at_level(logging.WARNING, logger="repowise.mcp.answer"):
        result = await get_answer("how does the alpha module handle the write path")

    assert result["answer"]
    assert any("page-content fetch failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_every_hit_the_prompt_shows_is_offered_page_content(setup_mcp, monkeypatch):
    """The enrichment bound must not sit below the number of hits synthesis is
    asked to read — a hit in the prompt with no prose can only be answered
    from its symbol names."""
    from repowise.server.mcp_server.tool_answer.config import _PAGE_EXCERPT_HITS

    # Retrieval is capped at 5 hits for the prompt and the payload.
    assert _PAGE_EXCERPT_HITS >= 5


@pytest.mark.asyncio
async def test_enriched_prompt_stays_within_the_synthesis_budget(setup_mcp, factory, monkeypatch):
    """Five hits of page prose on top of the symbol block must still fit the
    budgeter's ceiling."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer.config import (
        _GATED_EXCERPT_CHARS,
        _PAGE_EXCERPT_HITS,
    )

    await _add_pages(factory, setup_mcp, "x" * 20_000)
    _patch_pipeline(monkeypatch, answer_mod, dominant=True)
    prompts = _capture_prompt(monkeypatch, answer_mod, "Alpha owns writes (pkg/alpha/one.py).")

    await get_answer("how does the alpha module handle the write path")

    assert prompts
    # Generous headroom over the excerpt budget for the prelude, symbol block
    # and template; the point is that no excerpt escapes its cap.
    excerpt_budget = _GATED_EXCERPT_CHARS * _PAGE_EXCERPT_HITS
    assert len(prompts[0]) < excerpt_budget + 8000
