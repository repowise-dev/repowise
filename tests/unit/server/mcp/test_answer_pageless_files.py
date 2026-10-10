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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    ["how does the auth service authenticate users", "how does the AuthService login work"],
)
async def test_paged_answers_are_unchanged_by_pageless_files(pageless_mcp, monkeypatch, question):
    from repowise.server.mcp_server import get_answer

    _keyless(monkeypatch)
    with_rows = await get_answer(question)

    symbol_search = _answer_pipeline._safe_symbol_search
    name_search = _answer_pipeline._safe_filename_search

    async def symbols_paged_only(ctx, q, *, pageless=False):
        return await symbol_search(ctx, q)

    async def names_paged_only(ctx, q):
        return [(r, full) for r, full in await name_search(ctx, q) if r.page_type != "file"]

    monkeypatch.setattr(_answer_pipeline, "_safe_symbol_search", symbols_paged_only)
    monkeypatch.setattr(_answer_pipeline, "_safe_filename_search", names_paged_only)
    without = await get_answer(question)

    paged = [p for p in _ranked(with_rows) if p not in _PAGELESS_PATHS]
    assert paged
    assert paged == _ranked(without)[: len(paged)]
