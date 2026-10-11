"""get_answer retrieves indexed files that have no wiki page.

The only file that answers each question below has no page. It must reach the
answer's evidence through the symbol or filename leg, may lead it, and is served
with its path and matched code but no page prose. Fixtures are shared with the
search-side tests: TypeScript, Python and Go files plus distractors.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server import _answer_pipeline
from tests.unit.server.mcp.test_filename_leg import _PAGELESS, _QUERIES, _seed_pageless

_TS_TARGET = "src/config/exec-command-highlighting.ts"
_PAGELESS_PATHS = {path for path, _, _ in _PAGELESS}


@pytest.fixture
async def pageless_mcp(setup_mcp, session, tmp_path, monkeypatch):
    await _seed_pageless(session, setup_mcp)
    # Live source behind every seeded symbol (line 12), so hydration has real
    # code to serve for a file with no page.
    for path, name, _language in _PAGELESS:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"// line {i}" for i in range(1, 12)]
        lines += [f"function {name}() {{", "  return 'MATCHED_BODY';", "}"]
        file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("REPOWISE_ANSWER_DISABLE_CACHE", "1")
    return setup_mcp


def _keyless(monkeypatch) -> None:
    import repowise.server.mcp_server.tool_answer.answer as answer_mod

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: None)


def _ranked(result: dict) -> list[str]:
    """The files an answer ranks, whichever projection its confidence chose."""
    if result.get("candidate_files"):
        return result["candidate_files"]
    return [r["path"] for r in result.get("retrieval", [])] or result.get("fallback_targets", [])


async def _ctx():
    from repowise.server.mcp_server._helpers import _resolve_repo_context

    return await _resolve_repo_context(None)


@pytest.mark.asyncio
async def test_pipeline_keeps_pageless_hits_with_their_path(pageless_mcp):
    ctx = await _ctx()
    hits = await _answer_pipeline.hybrid_retrieve("exec command highlighting", ctx)
    hits = await _answer_pipeline.hydrate_hits(hits, ctx)
    row = next(h for h in hits if h["target_path"] == _TS_TARGET)
    assert row["page_type"] == "file"
    assert row["summary"] == ""
    assert {"filename", "symbol"} <= row["_sources"]


@pytest.mark.asyncio
async def test_scope_drops_pageless_hits_outside_it(pageless_mcp):
    ctx = await _ctx()
    hits = await _answer_pipeline.hybrid_retrieve("exec command highlighting", ctx)
    scoped = await _answer_pipeline.hydrate_hits(hits, ctx, scope="app/")
    assert _TS_TARGET not in [h["target_path"] for h in scoped]


@pytest.mark.asyncio
@pytest.mark.parametrize(("question", "path"), _QUERIES)
async def test_keyless_answer_leads_with_the_file(pageless_mcp, monkeypatch, question, path):
    from repowise.server.mcp_server import get_answer

    _keyless(monkeypatch)
    result = await get_answer(question)
    lead = result["best_guesses"][0]
    assert lead["file"] == path
    assert result["candidate_files"][0] == path
    # Its evidence is the matched code, never invented page prose.
    assert lead["functions"][0]["line"] == 12


@pytest.mark.asyncio
async def test_keyed_synthesis_reads_the_pageless_file(pageless_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    class _Provider:
        provider_name = "mock"
        model_name = "mock-1"

    prompts: list[str] = []

    async def _capture(provider, system_prompt, user_prompt, **kwargs):
        prompts.append(user_prompt)
        return f"It is resolved in {_TS_TARGET}.", None

    monkeypatch.setattr(answer_mod, "_resolve_provider_for_answer", lambda _p: _Provider())
    monkeypatch.setattr(answer_mod, "synthesize", _capture)
    result = await get_answer("where is exec command highlighting resolved")
    assert _TS_TARGET in prompts[0]
    assert "MATCHED_BODY" in prompts[0]
    assert _TS_TARGET in result["citations"]


@pytest.mark.asyncio
async def test_a_pageless_test_file_ranks_below_its_source(pageless_mcp, monkeypatch):
    from repowise.server.mcp_server import get_answer

    _keyless(monkeypatch)
    files = (await get_answer("exec command highlighting"))["candidate_files"]
    test_file = "src/config/exec-command-highlighting.test.ts"
    assert files.index(_TS_TARGET) < files.index(test_file)


def _pages_only(monkeypatch) -> None:
    """Restrict both legs to paged rows: retrieval as it was before pageless files."""
    symbol_search = _answer_pipeline._safe_symbol_search
    name_search = _answer_pipeline._safe_filename_search

    async def symbols_paged_only(ctx, q, *, pageless=False):
        return await symbol_search(ctx, q)

    async def names_paged_only(ctx, q):
        return [(r, full) for r, full in await name_search(ctx, q) if r.page_type != "file"]

    monkeypatch.setattr(_answer_pipeline, "_safe_symbol_search", symbols_paged_only)
    monkeypatch.setattr(_answer_pipeline, "_safe_filename_search", names_paged_only)


async def _paged_pool(question: str, repo_id: str) -> list[tuple]:
    from repowise.server.mcp_server.tool_answer.answer import _run_retrieval_pipeline

    retrieved = await _run_retrieval_pipeline(
        question, await _ctx(), scope=None, exclude_spec=None, repo_id=repo_id
    )
    return [
        (h["target_path"], h.get("_hybrid_rank"), round(h["score"], 6))
        for h in retrieved.resolved_pool
        if h.get("page_type") != "file"
    ]


# Each spells a pageless file name and reaches several pages.
_MIXED_QUESTIONS = [
    "auth service middleware exec command highlighting",
    "database models and the retry backoff policy for auth service login",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", _MIXED_QUESTIONS)
async def test_pageless_files_leave_the_pages_alone(pageless_mcp, monkeypatch, question):
    from repowise.server.mcp_server import get_answer

    _keyless(monkeypatch)
    with_rows = await _paged_pool(question, pageless_mcp)
    with_answer = await get_answer(question)
    assert any(p in _PAGELESS_PATHS for p in _ranked(with_answer))

    _pages_only(monkeypatch)
    without = await _paged_pool(question, pageless_mcp)
    without_answer = await get_answer(question)

    # Same pages, same order, same hybrid ranks and scores.
    assert len(with_rows) > 2
    assert with_rows == without
    assert with_answer["confidence"] == without_answer["confidence"]
    paged = [p for p in _ranked(with_answer) if p not in _PAGELESS_PATHS]
    assert paged == _ranked(without_answer)[: len(paged)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
async def test_a_failed_filename_leg_is_reported(pageless_mcp, monkeypatch, failure):
    from repowise.server.mcp_server import get_answer

    async def broken(*_args, **_kwargs):
        raise failure

    _keyless(monkeypatch)
    monkeypatch.setattr(_answer_pipeline, "filename_backed_pages", broken)
    result = await get_answer("exec command highlighting")
    assert "filename" in result["_meta"]["retrieval_degraded"]


def test_rerank_keeps_page_order_and_places_pageless_rows_by_the_whole_window():
    from repowise.server.mcp_server._retrieval_rank import (
        rerank_by_context_coverage,
        rerank_pages_first,
    )

    def rows():
        pages = [
            {"page_type": "file_page", "target_path": f"pkg/{name}.py", "score": s}
            for name, s in (("retry_queue", 3.0), ("backoff_policy", 2.9), ("misc_util", 2.8))
        ]
        pageless = {
            "page_type": "file",
            "target_path": "pkg/retry_backoff_policy.go",
            "score": 2.7,
        }
        return pages, pageless

    def paths(ranked):
        return [h["target_path"] for h in ranked]

    question = "retry backoff policy"
    kwargs = {"score_key": "score", "floor": 0.5}
    pages, _ = rows()
    alone = paths(rerank_by_context_coverage(pages, question, **kwargs))
    pages, pageless = rows()
    whole = paths(rerank_by_context_coverage([*pages, pageless], question, **kwargs))
    pages, pageless = rows()
    mixed = rerank_pages_first([*pages, pageless], question, **kwargs)
    order = paths(mixed)
    assert [p for p in order if p != pageless["target_path"]] == alone
    # The pageless row spells every word, so the whole window ranks it first.
    assert whole[0] == order[0] == pageless["target_path"]
    assert [h["score"] for h in mixed] == sorted((h["score"] for h in mixed), reverse=True)
