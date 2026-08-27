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
async def test_one_target_and_no_query_answers_about_that_target(setup_mcp):
    """A named file with nothing asked about it is a question about that file.

    The no-query branch reached the health dashboard before it looked at
    targets, so this returned the same repo-wide summary for every file — an
    answer that never mentioned what was asked about.
    """
    from repowise.server.mcp_server import get_why

    result = await get_why(targets=["src/auth/service.py"])

    assert result["mode"] == "path"
    assert result["path"] == "src/auth/service.py"
    assert result["decisions"]


@pytest.mark.asyncio
async def test_targets_and_no_query_differ_by_target(setup_mcp):
    """Two different files must not produce one answer.

    The dashboard is byte-identical whatever it is asked about, so equality
    here is the whole symptom rather than a proxy for it.
    """
    from repowise.server.mcp_server import get_why

    first = await get_why(targets=["src/auth/service.py"])
    second = await get_why(targets=["src/db/models.py"])

    assert first != second


@pytest.mark.asyncio
async def test_every_target_is_answered_when_several_are_named(setup_mcp):
    """Answering only the first target would be the same silent drop, halved."""
    from repowise.server.mcp_server import get_why

    targets = ["src/auth/service.py", "src/db/models.py"]
    result = await get_why(targets=targets)

    assert result["mode"] == "path"
    assert result["paths"] == targets
    assert sorted(result["target_context"]) == sorted(targets)


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
async def test_get_why_semantic_decision_namespace_filtering(session, setup_mcp):
    """Mode 3 semantic path: over-fetch from page store, keep only decision: hits.

    Upserts a decision vector under the 'decision:' prefix and a noise page
    without the prefix into the shared vector store.  Confirms that get_why
    surfaces the decision hit with the prefix stripped, and excludes the noise
    page from the decisions list.

    A stored record is seeded alongside them because the semantic lanes now run
    only once something has cleared the relevance floor: a question no stored
    record answers returns before embedding anything, rather than serving the
    three nearest vectors to a store that has nothing to say.
    """
    import json

    import repowise.server.mcp_server as mcp_mod
    from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
    from repowise.core.persistence.models import DecisionRecord
    from repowise.server.mcp_server import get_why

    session.add(
        DecisionRecord(
            id="dec-redis",
            repository_id=setup_mcp,
            title="Redis for caching",
            status="proposed",
            context="caching",
            decision="Use Redis for caching",
            rationale="Redis caching reduces latency",
            affected_files_json=json.dumps([]),
            affected_modules_json=json.dumps([]),
            source="pr",
            confidence=0.8,
            staleness_score=0.0,
        )
    )
    await session.flush()

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


# ---------------------------------------------------------------------------
# Path-mode cap and projection
#
# Before this, path mode inlined every governing record whole: on a bug-magnet
# file that measured 81 854 chars, over the MCP host cap, which rejects rather
# than truncates — so the mode that answers "what governs this file right
# before I edit it" hard-failed on exactly the files it exists for.
# ---------------------------------------------------------------------------


async def _seed_bulky_decisions(session, rid: str, path: str, count: int) -> None:
    """``count`` records governing *path*, each as heavy as a real one gets."""
    import json

    from repowise.core.persistence.models import DecisionRecord

    for i in range(count):
        session.add(
            DecisionRecord(
                id=f"bulk{i}",
                repository_id=rid,
                title=f"Bulky decision {i}",
                status="superseded" if i else "active",
                context="ctx " * 200,
                decision="dec " * 200,
                rationale="why " * 200,
                affected_files_json=json.dumps([path] + [f"src/f{i}/{n}.py" for n in range(60)]),
                affected_modules_json=json.dumps([]),
                source="pr",
                confidence=0.5,
                staleness_score=0.0,
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_get_why_path_fits_the_transport_budget(session, setup_mcp):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server._budget import effective_char_budget

    await _seed_bulky_decisions(session, setup_mcp, "src/auth/service.py", 30)

    result = await get_why("src/auth/service.py")

    import json as _json

    assert len(_json.dumps(result, default=str)) <= effective_char_budget()


@pytest.mark.asyncio
async def test_get_why_path_fits_a_narrowed_host_cap(session, setup_mcp, monkeypatch, tmp_path):
    """The cap alone is not the guarantee — free text is what the budget bounds.

    A user who lowers ``MAX_MCP_OUTPUT_TOKENS`` pulls the ceiling under the
    projection, which is the case the fixed caps cannot cover on their own.
    """
    import json as _json

    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server._budget import collector as collector_mod
    from repowise.server.mcp_server._budget import effective_char_budget

    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "2000")
    # Drops are recoverable, which means they are written somewhere — send
    # them to tmp_path instead of the developer's own ~/.repowise.
    monkeypatch.setattr(
        collector_mod, "default_store_path", lambda start=None: tmp_path / "omissions.db"
    )
    await _seed_bulky_decisions(session, setup_mcp, "src/auth/service.py", 30)

    result = await get_why("src/auth/service.py")

    assert result["truncated"] is True
    assert result["dropped_decisions"]
    assert len(_json.dumps(result, default=str)) <= effective_char_budget()


def test_path_final_fit_composes_episode_construction_counts(tmp_path, monkeypatch):
    from repowise.server.mcp_server._budget import OmissionCollector
    from repowise.server.mcp_server.tool_why import _fit_path_response

    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "2000")
    response = {
        "mode": "path",
        "path": "src/large.py",
        "decisions": [],
        "episodes": [
            {"subject": f"episode-{index}", "recorded": "x" * 5000}
            for index in range(3)
        ],
        "episodes_total": 8,
        "episodes_emitted": 3,
        "episodes_reduced_reason": "construction_cap",
        "episodes_truncated": True,
        "episodes_omitted": 5,
        "_meta": {},
    }
    collector = OmissionCollector(
        "get_why", store_path=tmp_path / "omissions.sqlite3"
    )

    result = _fit_path_response(response, tmp_path, collector)

    assert result["episodes_total"] == 8
    assert result["episodes_emitted"] == 0
    assert result["episodes_omitted"] == 8
    assert result["episodes_reduced_reason"] == (
        "construction_cap_and_response_budget"
    )
    assert result["_meta"]["omitted"]["refs"]


@pytest.mark.asyncio
async def test_get_why_path_caps_records_and_keeps_the_active_one_first(
    session, setup_mcp, tmp_path, monkeypatch
):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server._budget import collector as collector_mod
    from repowise.server.mcp_server.tool_why import _MAX_PATH_DECISIONS

    monkeypatch.setattr(
        collector_mod, "default_store_path", lambda start=None: tmp_path / "omissions.db"
    )

    await _seed_bulky_decisions(session, setup_mcp, "src/auth/service.py", 30)

    result = await get_why("src/auth/service.py")

    assert len(result["decisions"]) <= _MAX_PATH_DECISIONS
    # Ranked, not table-scan order: the one active record leads.
    assert result["decisions"][0]["status"] == "active"
    # And the count that was capped is still reported honestly.
    assert result["decisions_total"] == 31
    assert result["decisions_emitted"] == _MAX_PATH_DECISIONS
    assert result["decisions_reduced_reason"] == "construction_cap_and_response_budget"
    assert result["_meta"]["omitted"]["refs"]
    assert result["alignment"]["governing_count"] == 31


@pytest.mark.asyncio
async def test_get_why_asks_git_about_the_top_record_only(session, setup_mcp, monkeypatch):
    """The sanctioned read-time query is one call, not one per governing record.

    Measured at ~66 ms against this repo, so eight of them would be ~530 ms on
    a path that also does everything else. The record ranked first is the one
    a reader acts on; the rest keep the stored proportion, which cost nothing.
    """
    from repowise.server.mcp_server import get_why, tool_why

    calls: list[tuple] = []

    def _fake(root, *, created_at, nodes):
        calls.append((root, tuple(nodes)))
        return "nothing in the 1 file it governs has changed since 2026-01-01"

    monkeypatch.setattr(tool_why, "describe_decision_currency", _fake)
    await _seed_bulky_decisions(session, setup_mcp, "src/auth/service.py", 30)

    result = await get_why("src/auth/service.py")

    assert len(calls) == 1
    assert "still_true" in result["decisions"][0]
    assert all("still_true" not in d for d in result["decisions"][1:])


@pytest.mark.asyncio
async def test_get_why_stays_silent_when_git_cannot_decide(session, setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_why, tool_why

    monkeypatch.setattr(
        tool_why, "describe_decision_currency", lambda root, **kw: None
    )

    result = await get_why("src/auth/service.py")

    assert all("still_true" not in d for d in result["decisions"])


@pytest.mark.asyncio
async def test_get_why_path_projects_wide_affected_files(session, setup_mcp):
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server.tool_why import _MAX_AFFECTED_FILES

    await _seed_bulky_decisions(session, setup_mcp, "src/auth/service.py", 2)

    result = await get_why("src/auth/service.py")
    wide = next(d for d in result["decisions"] if d["title"].startswith("Bulky"))

    assert len(wide["affected_files"]) == _MAX_AFFECTED_FILES
    assert wide["affected_files_total"] == 61


@pytest.mark.asyncio
async def test_get_why_path_leaves_a_small_response_untouched(session, setup_mcp):
    """No cap fires on the ordinary case: no truncation flags, full arrays."""
    from repowise.server.mcp_server import get_why

    result = await get_why("src/auth/service.py")

    assert "truncated" not in result
    assert "decisions_total" not in result
    jwt = next(d for d in result["decisions"] if d["title"] == "Use JWT for authentication")
    assert jwt["affected_files"] == ["src/auth/service.py", "src/auth/middleware.py"]
    assert "affected_files_total" not in jwt


@pytest.mark.asyncio
async def test_get_why_path_fits_with_one_enormous_record(session, setup_mcp, monkeypatch, tmp_path):
    """The last record is droppable too — a cap on the count is not a bound.

    One governing record whose free text alone busts the budget is the case a
    per-record cap cannot reach.
    """
    import json as _json

    from repowise.core.persistence.models import DecisionRecord
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server._budget import collector as collector_mod
    from repowise.server.mcp_server._budget import effective_char_budget

    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "2000")
    monkeypatch.setattr(
        collector_mod, "default_store_path", lambda start=None: tmp_path / "omissions.db"
    )
    session.add(
        DecisionRecord(
            id="huge",
            repository_id=setup_mcp,
            title="One very long decision",
            status="active",
            rationale="why " * 6000,
            affected_files_json=_json.dumps(["src/auth/service.py"]),
            affected_modules_json=_json.dumps([]),
            source="pr",
        )
    )
    await session.flush()

    result = await get_why("src/auth/service.py")

    assert result["truncated"] is True
    assert len(_json.dumps(result, default=str)) <= effective_char_budget()


@pytest.mark.asyncio
async def test_get_why_path_fits_on_an_ungoverned_file(session, setup_mcp, monkeypatch, tmp_path):
    """The git-archaeology fallback is budgeted too.

    It is the branch that fires on a file with no decisions — which is most
    files — and none of the decision-shaped caps touch it.
    """
    import json as _json

    from repowise.core.persistence.models import GitMetadata
    from repowise.server.mcp_server import get_why
    from repowise.server.mcp_server._budget import collector as collector_mod
    from repowise.server.mcp_server._budget import effective_char_budget

    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "2000")
    monkeypatch.setattr(
        collector_mod, "default_store_path", lambda start=None: tmp_path / "omissions.db"
    )
    session.add(
        GitMetadata(
            repository_id=setup_mcp,
            file_path="src/other/utils.py",
            commit_count_total=90,
            significant_commits_json=_json.dumps(
                [
                    {
                        "sha": f"{i:08x}",
                        "date": "2026-01-01T00:00:00+00:00",
                        "message": f"refactor: rework the utils helper {i}",
                        "author": "Alice",
                        "body": "why " * 250,
                    }
                    for i in range(50)
                ]
            ),
        )
    )
    await session.flush()

    result = await get_why("src/other/utils.py")

    assert result["decisions"] == []
    # The fallback really was over the line — otherwise this test proves nothing.
    assert result["truncated"] is True
    assert len(_json.dumps(result, default=str)) <= effective_char_budget()
