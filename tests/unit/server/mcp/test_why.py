"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_why_natural_language(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("why is JWT used for authentication")
    assert result["mode"] == "search"
    assert result["query"] == "why is JWT used for authentication"
    assert len(result["decisions"]) >= 1
    assert any("JWT" in d["title"] for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_file_path(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")
    assert result["mode"] == "path"
    assert result["path"] == "src/auth/service.py"
    assert len(result["decisions"]) >= 1
    assert any(d["title"] == "Use JWT for authentication" for d in result["decisions"])

    # Origin story
    origin = result["origin_story"]
    assert origin["available"] is True
    assert origin["primary_author"] == "Alice"
    assert origin["total_commits"] == 42
    assert len(origin["key_commits"]) >= 1
    assert len(origin["contributors"]) >= 1
    assert "Alice" in origin["summary"]

    # Alignment — dec1 is "proposed", both service.py and middleware.py share it
    alignment = result["alignment"]
    assert alignment["score"] in ("high", "medium", "low", "none")
    assert alignment["governing_count"] >= 1
    assert "explanation" in alignment


@pytest.mark.asyncio
async def test_get_why_file_path_commit_decision_linkage(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")
    origin = result["origin_story"]

    # "Add JWT support" commit should link to "Use JWT for authentication" decision
    # because "JWT" appears in both the commit message and decision title
    linked = origin["linked_decisions"]
    assert len(linked) >= 1
    jwt_decision = next((d for d in linked if d["title"] == "Use JWT for authentication"), None)
    assert jwt_decision is not None
    assert len(jwt_decision["evidence_commits"]) >= 1
    ec = jwt_decision["evidence_commits"][0]
    assert "JWT" in ec["message"] or "jwt" in ec["message"].lower()
    assert "matching_keywords" in ec


@pytest.mark.asyncio
async def test_get_why_natural_language_with_targets(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search with targets — decisions governing service.py should be boosted
    result = await get_why(
        "authentication approach",
        targets=["src/auth/service.py"],
    )
    assert result["mode"] == "search"
    assert len(result["decisions"]) >= 1

    # target_context should be present
    assert "target_context" in result
    ctx = result["target_context"]["src/auth/service.py"]
    assert len(ctx["governing_decisions"]) >= 1
    assert ctx["origin"]["available"] is True
    assert ctx["origin"]["primary_author"] == "Alice"


@pytest.mark.asyncio
async def test_get_why_expanded_keyword_search(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search for "security" — should match via tags_json on dec1
    result = await get_why("security")
    assert result["mode"] == "search"
    # dec1 has tags=["auth", "security"], should be found
    assert len(result["decisions"]) >= 1
    assert any(d.get("title") == "Use JWT for authentication" for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_file_no_git_metadata(setup_mcp):
    from repowise.server.mcp_server import get_why

    # middleware.py has no GitMetadata in the fixture
    result = await get_why("src/auth/middleware.py")
    assert result["mode"] == "path"
    origin = result["origin_story"]
    assert origin["available"] is False
    assert "No git history" in origin["summary"]

    # But it still has decisions (dec1 affects middleware.py)
    assert len(result["decisions"]) >= 1
    alignment = result["alignment"]
    assert alignment["governing_count"] >= 1


@pytest.mark.asyncio
async def test_get_why_file_ungoverned(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Use a path that has no decisions — triggers git archaeology fallback
    result = await get_why("src/other/utils.py")
    assert result["mode"] == "path"
    assert result["alignment"]["score"] == "none"
    assert "ungoverned" in result["alignment"]["explanation"]

    # Git archaeology fallback should be triggered
    assert "git_archaeology" in result
    arch = result["git_archaeology"]
    assert arch["triggered"] is True
    assert "summary" in arch
    assert "file_commits" in arch
    assert "cross_references" in arch
    assert "git_log" in arch


@pytest.mark.asyncio
async def test_get_why_fallback_with_cross_references(setup_mcp):
    from repowise.server.mcp_server import get_why

    # src/auth/service.py has git metadata with commits mentioning "auth"
    # Query a nonexistent auth file — cross-references should find commits
    # from service.py that mention "auth" terms
    result = await get_why("src/auth/new_handler.py")
    assert result["mode"] == "path"
    assert len(result["decisions"]) == 0  # No decisions for this file

    arch = result["git_archaeology"]
    assert arch["triggered"] is True
    # Cross-references may find commits from service.py whose messages
    # contain "auth" (matching the path stem "new_handler" won't match,
    # but the file_commits will still be empty since no git metadata exists)
    assert isinstance(arch["cross_references"], list)


@pytest.mark.asyncio
async def test_get_why_targets_fallback(setup_mcp):
    from repowise.server.mcp_server import get_why

    # Search with a target that has no governing decisions
    result = await get_why(
        "why does this exist",
        targets=["src/other/unknown.py"],
    )
    assert result["mode"] == "search"
    ctx = result["target_context"]["src/other/unknown.py"]
    assert len(ctx["governing_decisions"]) == 0
    # Fallback should trigger
    assert "git_archaeology" in ctx
    assert ctx["git_archaeology"]["triggered"] is True


@pytest.mark.asyncio
async def test_get_why_no_args(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why()
    assert result["mode"] == "health"
    assert "summary" in result
    assert "counts" in result
    assert "proposed_awaiting_review" in result
    assert "ungoverned_hotspots" in result


@pytest.mark.asyncio
async def test_get_why_module_path(setup_mcp):
    from repowise.server.mcp_server import get_why

    result = await get_why("src/db")
    assert result["mode"] == "path"
    assert len(result["decisions"]) >= 1
    assert any(d["title"] == "SQLAlchemy as ORM" for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_path_surfaces_code_rationale(setup_mcp, tmp_path):
    """Ungoverned file whose 'why' lives in a code comment → code_rationale."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_why

    # Point the repo root at a real dir holding a rationale-bearing source file.
    (tmp_path / "src" / "other").mkdir(parents=True)
    (tmp_path / "src" / "other" / "widget.py").write_text(
        "# We poll every 5s instead of using a webhook because the upstream\n"
        "# service drops connections behind their proxy.\n"
        "POLL_INTERVAL = 5\n",
        encoding="utf-8",
    )
    mcp_mod._repo_path = str(tmp_path)

    result = await get_why("src/other/widget.py")
    assert result["mode"] == "path"
    assert len(result["decisions"]) == 0  # ungoverned → fallback fires
    assert "code_rationale" in result
    top = result["code_rationale"][0]
    assert "webhook" in top["comment"]
    assert top["path"] == "src/other/widget.py"
    assert top["lines"][0] == 1


@pytest.mark.asyncio
async def test_get_why_targets_surfaces_code_rationale(setup_mcp, tmp_path):
    """Search with a target lacking decisions → mine the target's comments."""
    import repowise.server.mcp_server as mcp_mod
    from repowise.server.mcp_server import get_why

    (tmp_path / "src" / "other").mkdir(parents=True)
    (tmp_path / "src" / "other" / "cache.py").write_text(
        "import time\n"
        "# TTL is 30s because shorter windows thrash the backing store\n"
        "TTL = 30\n",
        encoding="utf-8",
    )
    mcp_mod._repo_path = str(tmp_path)

    result = await get_why("why this ttl value", targets=["src/other/cache.py"])
    assert result["mode"] == "search"
    assert "code_rationale" in result
    assert any("thrash" in r["comment"] for r in result["code_rationale"])


@pytest.mark.asyncio
async def test_get_why_semantic_decision_namespace_filtering(setup_mcp):
    """Mode 3 semantic path: over-fetch from page store, keep only decision: hits.

    Upserts a decision vector under the 'decision:' prefix and a noise page
    without the prefix into the shared vector store.  Confirms that get_why
    surfaces the decision hit with the prefix stripped, and excludes the noise
    page from the decisions list.
    """
    import repowise.server.mcp_server as mcp_mod
    from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
    from repowise.server.mcp_server import get_why

    vs = mcp_mod._vector_store

    # Insert a decision under the decision: namespace
    await vs.embed_and_upsert(
        f"{DECISION_VECTOR_PREFIX}dec-vec-1",
        "Use Redis for caching to reduce latency",
        {
            "title": "Use Redis for caching",
            "page_type": "decision_record",
            "target_path": "",
            "content": "Use Redis for caching to reduce latency",
        },
    )

    # Insert a noise page (no decision: prefix) with similar text
    await vs.embed_and_upsert(
        "file_page:src/cache/redis.py",
        "Redis caching implementation module",
        {
            "title": "Redis Cache Module",
            "page_type": "file_page",
            "target_path": "src/cache/redis.py",
            "content": "Redis caching implementation module",
        },
    )

    result = await get_why("why use Redis for caching")
    assert result["mode"] == "search"

    decision_ids = [d["id"] for d in result["decisions"]]
    # The semantic decision hit should appear with the prefix stripped
    assert "dec-vec-1" in decision_ids, f"Expected 'dec-vec-1' in decisions; got {decision_ids}"
    # The noise page must not appear in decisions
    assert not any(d.get("id", "").startswith("file_page:") for d in result["decisions"]), (
        "Noise page should not appear in decisions list"
    )
