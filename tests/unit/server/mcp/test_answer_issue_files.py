"""Files a question names by stack trace or defined identifier lead retrieval.

``populated_db`` indexes ``src/auth/service.py`` (defines ``AuthService``),
``src/auth/middleware.py``, ``src/db/models.py`` and ``tests/test_service.py``.
"""

from __future__ import annotations

import pytest

TRACE = """\
Login fails with this:
Traceback (most recent call last):
  File "/srv/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 70, in app
  File "/srv/app/src/auth/middleware.py", line 33, in dispatch
  File "/usr/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
KeyError: 'sub'
"""

_FAKE_FILES = [f"pkg/f{i}.py" for i in range(7)]


def _patch_retrieval(monkeypatch, answer_mod) -> None:
    """Seven unrelated files rank first; none is a trace file."""

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": f"file_page:{p}", "score": 10.0 - i} for i, p in enumerate(_FAKE_FILES)
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        for h in hits:
            h["target_path"] = h["page_id"].removeprefix("file_page:")
            h.update(title=h["target_path"], summary="", snippet="", page_type="file_page")
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)


async def _pool(answer_mod, question: str) -> tuple[list[str], list[str]]:
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context

    ctx = await _resolve_repo_context(None)
    async with get_session(ctx.session_factory) as session:
        repo_id = (await _get_repo(session)).id
    got = await answer_mod._run_retrieval_pipeline(
        question, ctx, scope=None, exclude_spec=None, repo_id=repo_id
    )
    return [h.get("target_path") for h in got.resolved_pool], [h.get("target_path") for h in got.hits]


@pytest.mark.asyncio
async def test_trace_file_outside_top_five_leads(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod

    _patch_retrieval(monkeypatch, answer_mod)
    pool, hits = await _pool(answer_mod, TRACE)
    assert pool[0] == "src/auth/middleware.py"
    assert hits[0] == "src/auth/middleware.py"
    assert len(hits) == 5


@pytest.mark.asyncio
async def test_trace_files_then_defined_identifier_files(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod

    _patch_retrieval(monkeypatch, answer_mod)
    pool, _ = await _pool(answer_mod, TRACE + "\nIs AuthService caching the token?")
    # The symbol anchor already lifts AuthService's file; the trace file leads.
    assert pool[0] == "src/auth/middleware.py"
    assert "src/auth/service.py" in pool[1:4]


@pytest.mark.asyncio
async def test_plain_question_ranking_unchanged(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_answer.answer as answer_mod

    _patch_retrieval(monkeypatch, answer_mod)
    calls: list = []
    monkeypatch.setattr(answer_mod, "lead_with_files", lambda *a: calls.append(a))
    monkeypatch.setattr(answer_mod, "place_named_files", lambda *a: calls.append(a))
    question = "How are expired login tokens refreshed?"
    pool, _ = await _pool(answer_mod, question)
    assert calls == []
    assert pool == _FAKE_FILES


async def _no_wait(ctx) -> None:
    return None


@pytest.mark.asyncio
async def test_search_candidates_lead_with_trace_file(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_search as search_mod

    monkeypatch.setattr(search_mod, "_wait_for_vector_store", _no_wait)
    result = await search_mod.search_codebase(TRACE)
    assert result["candidates"][0] == {"path": "src/auth/middleware.py"}
    assert len(result["candidates"]) <= 5


@pytest.mark.asyncio
async def test_search_plain_query_candidates_untouched(setup_mcp, monkeypatch):
    import repowise.server.mcp_server.tool_search as search_mod

    calls: list = []
    real = search_mod._lead_candidates

    def _spy(response, issue, limit):
        calls.append(issue)
        return real(response, issue, limit)

    monkeypatch.setattr(search_mod, "_lead_candidates", _spy)
    monkeypatch.setattr(search_mod, "_wait_for_vector_store", _no_wait)
    await search_mod.search_codebase("how are tokens refreshed")
    assert calls == [([], [])]


def _hit(path: str, score: float) -> dict:
    return {"page_id": f"file_page:{path}", "target_path": path, "score": score}


def test_named_file_joins_below_top_three():
    from repowise.server.mcp_server.tool_answer.symbols import place_named_files

    hits = [_hit(f"f{i}.py", 10.0 - i) for i in range(6)]
    out = place_named_files(hits, ["f5.py", "new.py"])
    assert [h["target_path"] for h in out] == [
        "f0.py", "f1.py", "f2.py", "f5.py", "new.py", "f3.py", "f4.py"
    ]
    assert out[3]["score"] == out[2]["score"]
    assert out[4]["page_type"] == "file_page"


def test_named_file_already_in_top_three_stays():
    from repowise.server.mcp_server.tool_answer.symbols import place_named_files

    hits = [_hit(f"f{i}.py", 10.0 - i) for i in range(4)]
    out = place_named_files(hits, ["f1.py"])
    assert [h["target_path"] for h in out] == ["f0.py", "f1.py", "f2.py", "f3.py"]


def test_search_named_files_only_fill_free_slots():
    from repowise.server.mcp_server.tool_search import _lead_candidates
    from repowise.server.mcp_server.tool_search_symbols import IssueFiles

    response = {"candidates": [{"path": f"f{i}.py"} for i in range(3)]}
    _lead_candidates(response, IssueFiles(["trace.py"], ["f2.py", "def.py", "x.py"]), 5)
    assert response["candidates"] == [
        {"path": p} for p in ["trace.py", "f0.py", "f1.py", "f2.py", "def.py"]
    ]
    full = {"candidates": [{"path": f"f{i}.py"} for i in range(5)]}
    _lead_candidates(full, IssueFiles([], ["def.py"]), 5)
    assert full["candidates"] == [{"path": f"f{i}.py"} for i in range(5)]


@pytest.mark.asyncio
async def test_long_trace_queries_basenames_in_chunks(setup_mcp):
    """60 distinct basenames span three lookups; the real frame still maps."""
    from repowise.server.mcp_server._helpers import _resolve_repo_context
    from repowise.server.mcp_server.tool_search_symbols import issue_files

    deps = [
        f'  File "/srv/.venv/lib/python3.12/site-packages/dep/m{i}.py", line 1, in g'
        for i in range(25)
    ]
    user = [f'  File "/srv/app/pkg/mod{i}.py", line 1, in f' for i in range(45)]
    real = '  File "/srv/app/src/auth/middleware.py", line 33, in dispatch'
    lines = ["Traceback (most recent call last):", *deps, *user, real, "KeyError: 'sub'"]
    issue = await issue_files(await _resolve_repo_context(None), "\n".join(lines))
    assert issue.traced == ["src/auth/middleware.py"]
