"""A scoped get_answer searches inside its scope when the repository's top rows hold nothing there.

Each retrieval leg takes its top rows over the whole repository and the scope
is applied afterwards. When files outside the scope filled every row, a scoped
get_answer found nothing, even though the scope held a file on the question's
subject.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import Page, WikiSymbol

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_SCOPE = "pkg/vault"
_IN = "pkg/vault/ledger_reconcile.py"
# More matches outside the scope than any leg's window holds, all ranked ahead
# of the scope's file; one sits in a directory sharing the scope's spelling.
_OUT = [f"pkg/billing/b{i:02d}/ledger_reconcile.py" for i in range(24)] + [
    "pkg/vaultkeeper/ledger_reconcile.py"
]
_QUESTION = "how does the ledger reconcile work?"


def _page(path: str, content: str) -> Page:
    return Page(
        id=f"file_page:{path}",
        repository_id="repo1",
        page_type="file_page",
        title=f"File: {path}",
        content=content,
        summary=content[:80],
        target_path=path,
        source_hash="h",
        model_name="mock",
        provider_name="mock",
        generation_level=2,
        confidence=1.0,
        freshness_status="fresh",
        metadata_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _symbol(i: int, path: str) -> WikiSymbol:
    return WikiSymbol(
        id=f"rs{i}",
        repository_id="repo1",
        file_path=path,
        symbol_id=f"{path}::reconcile_ledger",
        name="reconcile_ledger",
        qualified_name="reconcile_ledger",
        kind="function",
        signature="def reconcile_ledger()",
        start_line=1,
        end_line=5,
        docstring="",
        visibility="public",
        is_async=False,
        complexity_estimate=1,
        language="python",
        parent_name=None,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
async def crowded(setup_mcp, session, fts, factory, tmp_path):
    strong = "Ledger reconcile: reconcile the ledger, ledger reconcile totals."
    pages = [_page(p, strong) for p in _OUT]
    pages.append(_page(_IN, "Writes a vault entry after each ledger reconcile."))
    session.add_all(pages)
    session.add_all(_symbol(i, p) for i, p in enumerate([*_OUT, _IN]))
    await session.flush()
    for p in pages:
        await fts.index(p.id, p.title, p.content, summary=p.summary, target_path=p.target_path)
    return SimpleNamespace(session_factory=factory, path=str(tmp_path), fts=fts)


@pytest.mark.asyncio
async def test_scoped_answer_finds_the_file_under_a_crowded_scope(crowded, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)

    result = await get_answer(_QUESTION, scope=_SCOPE)

    text = json.dumps(result)
    assert _IN in text
    assert "No wiki hits" not in result.get("note", "")
    assert not any(p in text for p in _OUT)


@pytest.mark.asyncio
async def test_each_leg_fills_its_window_under_the_scope(crowded):
    from repowise.server.mcp_server._prose_symbols import (
        filename_backed_pages,
        search_symbols_by_terms,
    )

    ctx = crowded
    terms = ["ledger", "reconcile"]

    # Unscoped, the scope's file falls outside every leg's window.
    assert _IN not in [r.target_path for r in await ctx.fts.search(_QUESTION, limit=15)]
    assert _IN not in [h["file"] for h in await search_symbols_by_terms(ctx, terms, 20)]
    unscoped = await filename_backed_pages(ctx, _QUESTION, max_files=8)
    assert _IN not in [r["target_path"] for r in unscoped]

    # Scoped, the window is spent under the scope, as a directory.
    fts_rows = await ctx.fts.search(_QUESTION, limit=15, path_prefix=_SCOPE)
    assert [r.target_path for r in fts_rows] == [_IN]
    symbols = await search_symbols_by_terms(ctx, terms, 20, scope=_SCOPE)
    assert [h["file"] for h in symbols] == [_IN]
    named = await filename_backed_pages(ctx, _QUESTION, max_files=8, scope=_SCOPE)
    assert [r["target_path"] for r in named] == [_IN]


@pytest.mark.asyncio
async def test_a_scope_the_first_pass_serves_is_not_searched_again(crowded, monkeypatch):
    """Control: in-scope rows in the repository's top rows keep the ranks they had."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)
    real = answer_mod._hybrid_retrieve
    scopes: list[str | None] = []

    async def _recording(question, ctx, *, scope=None):
        scopes.append(scope)
        return await real(question, ctx, scope=scope)

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _recording)

    result = await get_answer(_QUESTION, scope="pkg/billing")

    assert scopes == [None]
    assert "pkg/billing/" in json.dumps(result)
    scopes.clear()
    await get_answer(_QUESTION, scope=_SCOPE)
    assert scopes == [None, _SCOPE]
