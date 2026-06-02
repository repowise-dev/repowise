"""Exclude-pattern filtering for the custom-logic MCP surfaces (PR 4b).

Covers the three pipelines that PR 4a left untouched because filtering has to
happen *inside* their assembly/retrieval/aggregation steps, not at a single
result-list chokepoint:

  * ``get_context``  — target gate + neighbor/related-file filtering
  * ``get_answer``   — retrieval-hit filtering before synthesis/citations
  * ``search`` (federated) — per-repo filtering before RRF aggregation
"""

from __future__ import annotations

import json as _json

import pathspec
import pytest

SPEC = pathspec.PathSpec.from_lines("gitwildmatch", ["src/auth/middleware.py", "src/db/"])


# ---------------------------------------------------------------------------
# Surface 1: get_context enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_context_gates_excluded_file_target(setup_mcp, monkeypatch):
    """A requested file target that is excluded returns an error, not a card."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["src/db/models.py"], include=["docs"], compact=False)
    t = result["targets"]["src/db/models.py"]
    assert "error" in t
    assert "exclude" in t["error"].lower()


@pytest.mark.asyncio
async def test_get_context_gates_excluded_symbol_id_target(setup_mcp, monkeypatch):
    """A ``path::Name`` target whose file is excluded is gated too."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["src/db/models.py::User"], include=["docs"])
    t = result["targets"]["src/db/models.py::User"]
    assert "error" in t


@pytest.mark.asyncio
async def test_get_context_filters_excluded_imported_by(setup_mcp, monkeypatch):
    """An excluded importer must not appear in a non-excluded file's imported_by."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    # src/auth/middleware.py imports src/auth/service.py (edge ge2).
    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["src/auth/service.py"], include=["docs"], compact=False)
    t = result["targets"]["src/auth/service.py"]
    assert "src/auth/middleware.py" not in t["docs"]["imported_by"]


@pytest.mark.asyncio
async def test_get_context_imported_by_unfiltered_without_spec(setup_mcp, monkeypatch):
    """Sanity: with no exclude spec the importer is still present."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: None)

    result = await get_context(["src/auth/service.py"], include=["docs"], compact=False)
    t = result["targets"]["src/auth/service.py"]
    assert "src/auth/middleware.py" in t["docs"]["imported_by"]


@pytest.mark.asyncio
async def test_get_context_suggestions_drop_excluded_files(setup_mcp, monkeypatch):
    """Fuzzy not-found suggestions must not surface excluded files."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    # "models.py" resolves to no page/symbol; fuzzy match finds src/db/models.py.
    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["models.py"], include=["docs"])
    t = result["targets"]["models.py"]
    assert "error" in t
    assert "src/db/models.py" not in t.get("suggestions", [])


@pytest.mark.asyncio
async def test_get_context_suggestions_present_without_spec(setup_mcp, monkeypatch):
    """Control: without a spec the fuzzy suggestion is still offered."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: None)

    result = await get_context(["models.py"], include=["docs"])
    t = result["targets"]["models.py"]
    assert "src/db/models.py" in t.get("suggestions", [])


@pytest.mark.asyncio
async def test_get_context_filters_excluded_community_member(setup_mcp, monkeypatch):
    """Excluded community co-members must not leak into the community block."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    # service.py and middleware.py share community 1; middleware is excluded.
    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["src/auth/service.py"], include=["community"])
    community = result["targets"]["src/auth/service.py"]["community"]
    assert community is not None
    assert "src/auth/middleware.py" not in community["top_members"]
    assert "src/auth/service.py" in community["top_members"]


@pytest.mark.asyncio
async def test_get_context_filters_excluded_module_child_files(setup_mcp, monkeypatch):
    """Module child-file listings drop excluded files."""
    import repowise.server.mcp_server.tool_context.context as context_mod
    from repowise.server.mcp_server import get_context

    monkeypatch.setattr(context_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_context(["src/auth"], include=["docs"], compact=False)
    files = result["targets"]["src/auth"]["docs"]["files"]
    paths = [f["path"] for f in files]
    assert "src/auth/middleware.py" not in paths
    assert "src/auth/service.py" in paths


# ---------------------------------------------------------------------------
# Surface 2: get_answer retrieval + citations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_answer_filters_excluded_hits(setup_mcp, monkeypatch):
    """Excluded files never become fallback_targets / retrieval / citations."""
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.server.mcp_server import get_answer

    async def _fake_retrieve(question, ctx):
        return [
            {"page_id": "file_page:src/auth/service.py", "score": 5.0},
            {"page_id": "file_page:src/db/models.py", "score": 4.0},
        ]

    async def _fake_hydrate(hits, ctx, *, scope=None):
        mapping = {
            "file_page:src/auth/service.py": "src/auth/service.py",
            "file_page:src/db/models.py": "src/db/models.py",
        }
        for h in hits:
            h["target_path"] = mapping[h["page_id"]]
            h["title"] = h["target_path"]
            h["summary"] = ""
            h["snippet"] = ""
        return hits

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _fake_retrieve)
    monkeypatch.setattr(answer_mod, "_hydrate_hits", _fake_hydrate)
    monkeypatch.setattr(answer_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_answer("how does the db layer work")

    assert "src/db/models.py" not in result.get("fallback_targets", [])
    retrieved_paths = [h.get("target_path") for h in result.get("retrieval", [])]
    assert "src/db/models.py" not in retrieved_paths
    best = [g.get("file") for g in result.get("best_guesses", [])]
    assert "src/db/models.py" not in best
    # The non-excluded hit survives.
    assert "src/auth/service.py" in result.get("fallback_targets", [])


@pytest.mark.asyncio
async def test_get_answer_bypasses_cache_with_excluded_path(setup_mcp, monkeypatch):
    """A cached payload citing a now-excluded file is bypassed, not returned."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_answer.answer as answer_mod
    from repowise.core.persistence.models import AnswerCache
    from repowise.server.mcp_server import get_answer
    from repowise.server.mcp_server.tool_answer.config import _ANSWER_SCHEMA_VERSION
    from repowise.server.mcp_server.tool_answer.synthesis import _hash_question

    question = "how does the db layer work"
    async with mcp_mod._session_factory() as s:
        s.add(
            AnswerCache(
                id="ac_excluded",
                repository_id="repo1",
                question_hash=_hash_question(question),
                question=question,
                payload_json=_json.dumps(
                    {
                        "_schema_version": _ANSWER_SCHEMA_VERSION,
                        "answer": "CACHED ANSWER about src/db/models.py",
                        "citations": ["src/db/models.py"],
                        "fallback_targets": ["src/db/models.py"],
                        "confidence": "high",
                    }
                ),
            )
        )
        await s.commit()

    # Empty retrieval keeps the bypassed path deterministic (no LLM call).
    async def _empty_retrieve(question, ctx):
        return []

    monkeypatch.setattr(answer_mod, "_hybrid_retrieve", _empty_retrieve)
    monkeypatch.setattr(answer_mod, "_get_exclude_spec", lambda _p: SPEC)

    result = await get_answer(question)

    # Cache was bypassed (not the stored answer) and the excluded path is gone.
    assert result.get("answer") != "CACHED ANSWER about src/db/models.py"
    assert "src/db/models.py" not in result.get("citations", [])
    assert "src/db/models.py" not in result.get("fallback_targets", [])


# ---------------------------------------------------------------------------
# Surface 3: federated ("all repos") search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_single_repo_attaches_and_filters_target_path(setup_mcp, monkeypatch):
    """_search_single_repo attaches target_path and honours the repo's spec."""
    import repowise.server.mcp_server as mcp_mod
    import repowise.server.mcp_server.tool_search as search_mod
    from repowise.server.mcp_server._helpers import _resolve_repo_context
    from repowise.server.mcp_server.tool_search import _search_single_repo

    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/auth/service.py",
        "Auth Service — Main authentication service class",
        {"title": "Auth Service", "page_type": "file_page", "target_path": "src/auth/service.py"},
    )
    await mcp_mod._vector_store.embed_and_upsert(
        "file_page:src/db/models.py",
        "DB Models — SQLAlchemy ORM models",
        {"title": "DB Models", "page_type": "file_page", "target_path": "src/db/models.py"},
    )

    monkeypatch.setattr(search_mod, "_get_exclude_spec", lambda _p: SPEC)

    ctx = await _resolve_repo_context(None)
    results, _method = await _search_single_repo(ctx, "models service", limit=10, page_type=None)

    paths = [r.get("target_path") for r in results]
    assert paths, "expected at least one hit"
    assert all(p is not None for p in paths), "every federated hit must carry target_path"
    assert "src/db/models.py" not in paths
