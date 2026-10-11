"""The formatter must not shrink a page excerpt below what the fetch bought.

``_GATED_EXCERPT_CHARS`` decides how much page content a hit's excerpt is
worth fetching from the database. The context-block formatter then applied
its own, smaller cap to the same string, so half of every fetched excerpt
was thrown away after being paid for. The two constants live in different
modules, which is why the mismatch went unnoticed; the relationship is now
checked at import time and asserted here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import Page

_PAGE_IDS = ("file_page:pkg/alpha/one.py", "file_page:pkg/alpha/two.py")
_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


async def _add_pages(factory, repo_id: str, content: str) -> None:
    """Two file pages carrying `content` as their body."""
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


def _patch_pipeline(monkeypatch, answer_mod, page_id: str) -> None:
    """An ambiguous hit pair (ratio 1.05) on a real Page row — the path that
    already fetches page content today."""

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": page_id, "score": 2.0, "_sources": {"fts"}},
            {"page_id": "file_page:pkg/alpha/two.py", "score": 1.9, "_sources": {"fts"}},
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
async def test_whole_fetched_excerpt_survives_formatting(setup_mcp, factory, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer.config import _GATED_EXCERPT_CHARS

    body = "abcdefghij" * 400  # 4000 chars — longer than any cap in play
    await _add_pages(factory, setup_mcp, body)
    _patch_pipeline(monkeypatch, answer_mod, _PAGE_IDS[0])
    prompts = _capture_prompt(monkeypatch, answer_mod, "Alpha writes (pkg/alpha/one.py).")

    await get_answer("how does the alpha module handle the write path")

    assert prompts, "synthesis must run"
    assert body[:_GATED_EXCERPT_CHARS] in prompts[0], (
        f"the whole {_GATED_EXCERPT_CHARS}-char fetched excerpt must reach the prompt"
    )


@pytest.mark.asyncio
async def test_a_hit_without_page_content_still_formats(setup_mcp, factory, monkeypatch):
    """No page body is a normal state, not an error: the hit falls back to its
    summary and the prompt is still built."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    await _add_pages(factory, setup_mcp, "")
    _patch_pipeline(monkeypatch, answer_mod, _PAGE_IDS[0])
    prompts = _capture_prompt(monkeypatch, answer_mod, "Alpha writes (pkg/alpha/one.py).")

    await get_answer("how does the alpha module handle the write path")

    assert prompts, "synthesis must run"
    assert "One-line summary of the page." in prompts[0]


def test_format_excerpt_cap_is_at_least_the_fetch_size():
    """Guard the relationship directly, so an edit to either constant fails
    here rather than silently halving the prompt."""
    from repowise.server.mcp_server._answer_context import _MAX_CHARS_PER_HIT_EXCERPT
    from repowise.server.mcp_server.tool_answer.config import _GATED_EXCERPT_CHARS

    assert _MAX_CHARS_PER_HIT_EXCERPT >= _GATED_EXCERPT_CHARS
