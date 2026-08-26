"""``get_why(repo="all")`` answers with the same machinery as one repo.

This path kept the whole pre-relevance tool alive one argument away: substring
match on any query word, appended in workspace-resolution order, fifteen whole
records, no floor and no redirect, over several stores at once. These pin that
it now loads its corpus the way single-repo mode does, ranks, caps and refuses.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import DecisionRecord
from tests.unit.server.test_mcp_workspace import _make_repo_context, _MockRegistry

_MCP_STATE = (
    "_registry",
    "_workspace_root",
    "_session_factory",
    "_fts",
    "_vector_store",
    "_decision_store",
    "_repo_path",
    "_vector_store_ready",
    "_cross_repo_enricher",
)


def _decision(rec_id: str, repo_id: str, title: str, body: str, **kw) -> DecisionRecord:
    return DecisionRecord(
        id=rec_id,
        repository_id=repo_id,
        title=title,
        decision=body,
        rationale=kw.pop("rationale", ""),
        status=kw.pop("status", "active"),
        source="cli",
        affected_files_json="[]",
        affected_modules_json="[]",
        **kw,
    )


def _install(contexts: dict, default: str, root) -> _MockRegistry:
    import repowise.server.mcp_server as mcp_mod

    registry = _MockRegistry(
        contexts=contexts, default_alias=default, workspace_root=root
    )
    primary = contexts[default]
    mcp_mod._registry = registry
    mcp_mod._workspace_root = str(root)
    mcp_mod._session_factory = primary.session_factory
    mcp_mod._fts = primary.fts
    mcp_mod._vector_store = primary.vector_store
    mcp_mod._decision_store = primary.decision_store
    mcp_mod._repo_path = str(primary.path)
    mcp_mod._vector_store_ready = primary.vector_store_ready
    return registry


async def _uninstall(registry: _MockRegistry) -> None:
    import repowise.server.mcp_server as mcp_mod

    await registry.close()
    for attr in _MCP_STATE:
        setattr(mcp_mod, attr, None)


async def _store(
    tmp_path, alias: str, records: list, *, repo_name: str | None = None
) -> tuple:
    repo_dir = tmp_path / alias
    repo_dir.mkdir()
    return await _make_repo_context(
        alias,
        str(repo_dir),
        pages=[],
        extra_models=records,
        repo_name=repo_name,
    )


@pytest.fixture
async def two_repos(tmp_path):
    """A workspace whose answer lives in the second store to be resolved."""
    alpha = await _store(
        tmp_path,
        "alpha",
        [
            # Carries some of the question's words and not the rest, so it lands
            # under the floor. The old substring match served it.
            _decision("a-1", "repo-alpha", "Session cache warmup", "We warm the cache on boot"),
            # A tombstone that would otherwise match a question outright.
            _decision(
                "a-2",
                "repo-alpha",
                "Redis for rate limiting",
                "Use Redis for rate limiting counters",
                status="dismissed",
            ),
        ],
    )
    beta = await _store(
        tmp_path,
        "beta",
        [
            _decision(
                "b-1",
                "repo-beta",
                "Redis for the session cache",
                "Use Redis as the session cache backend",
                rationale="Shared across web workers",
            )
        ],
    )
    registry = _install({"alpha": alpha, "beta": beta}, "alpha", tmp_path)
    yield registry
    await _uninstall(registry)


@pytest.mark.asyncio
async def test_a_partial_match_in_an_earlier_store_is_left_under_the_floor(two_repos):
    """``alpha`` resolves first and holds a partial match; ``beta`` holds the answer.

    This pins the floor rather than the merge. ``a-1`` covers two of the question's
    four terms and scores 0.5, so it never reaches the merge to be ordered. The
    merge has its own test below.
    """
    from repowise.server.mcp_server import get_why

    result = await get_why(query="why is Redis used for the session cache", repo="all")

    assert result["workspace"] is True
    assert [d["id"] for d in result["decisions"]] == ["b-1"]
    decision = result["decisions"][0]
    assert decision["repo"] == "beta"
    assert decision["source"] == "cli"
    assert decision["provenance"] == "human_decision"
    assert decision["evidence_refs"] == [
        {
            "id": "ev_fb245a52dbd371b929d4",
            "repository": "beta",
            "kind": "legacy",
            "content_id": "338f3ae3ca60c54e7741",
            "provenance": "human_decision",
            "source": "cli",
            "source_kind": "cli",
            "verification_basis": "indexed",
        }
    ]


@pytest.mark.asyncio
async def test_the_merge_keeps_each_store_s_own_tiebreaks(tmp_path):
    """Two stores, one score: what separates them must be the ranker's rules.

    Both records carry every term of the question, so ``relevance`` is exactly
    1.0 for each, and the occurrence count is equal too because the text is
    identical.
    The only thing left to separate them is status, which is the third rule the
    single-repo ranker applies and which a merge ordering on repo alias would
    throw away. Alias order is deliberately the opposite of the right answer.
    """
    from repowise.server.mcp_server import get_why

    body = "Use Redis as the session cache backend"
    alpha = await _store(
        tmp_path, "alpha", [_decision("a-1", "repo-alpha", "Redis", body, status="deprecated")]
    )
    beta = await _store(
        tmp_path, "beta", [_decision("b-1", "repo-beta", "Redis", body, status="active")]
    )
    registry = _install({"alpha": alpha, "beta": beta}, "alpha", tmp_path)
    try:
        result = await get_why(query="why is Redis the session cache backend", repo="all")
        assert [d["id"] for d in result["decisions"]] == ["b-1", "a-1"]
    finally:
        await _uninstall(registry)


@pytest.mark.asyncio
async def test_a_dismissed_record_is_not_served(two_repos):
    """Dismissed is a tombstone everywhere else; this path used to serve it."""
    from repowise.server.mcp_server import get_why

    result = await get_why(query="why is Redis used for rate limiting", repo="all")

    assert all(d["id"] != "a-2" for d in result["decisions"])


@pytest.mark.asyncio
async def test_a_question_the_workspace_cannot_answer_gets_a_redirect(two_repos):
    from repowise.server.mcp_server import get_why

    result = await get_why(query="how does the parser handle Kotlin generics", repo="all")

    assert result["decisions"] == []
    assert result["try_instead"]
    assert "reason" in result


@pytest.mark.asyncio
async def test_workspace_search_serves_five_of_nine_answers(tmp_path, monkeypatch):
    """The count is written out rather than read from the constant.

    Comparing the result against ``_MAX_WORKSPACE_DECISIONS`` would hold for any
    value of it, including one that caps nothing. The number is a judgement
    about an agent's context, which is exactly the kind of thing a test should
    fail on when it changes.
    """
    from repowise.server.mcp_server import get_symbol, get_why, tool_middleware

    monkeypatch.setattr(
        "repowise.server.mcp_server._budget.collector.default_store_path",
        lambda _root: tmp_path / "omissions.sqlite3",
    )
    monkeypatch.setattr(
        "repowise.core.distill.store.default_store_path",
        lambda _root=None: tmp_path / "omissions.sqlite3",
    )

    ctx = await _store(
        tmp_path,
        "solo",
        [
            # Distinct evidence per record, so the restatement collapse does not
            # fold them and the cap is what the count is measuring.
            _decision(
                f"s-{i}",
                "repo-solo",
                f"Redis session cache decision {i}",
                "Use Redis as the session cache backend",
                evidence_file=f"src/cache_{i}.py",
            )
            for i in range(9)
        ],
    )
    registry = _install({"solo": ctx}, "solo", tmp_path)
    try:
        # Every content word occurs in every record, so all nine clear the floor
        # and the cap is the only thing deciding the count. A question carrying a
        # word none of them holds would be refused outright instead, since an unseen
        # term takes the rarest weight the store can award.
        result = await tool_middleware(get_why)(
            query="why is Redis the session cache backend", repo="all"
        )
        assert len(result["decisions"]) == 5
        assert result["decisions_total"] == 9
        assert result["decisions_emitted"] == 5
        assert result["decisions_reduced_reason"] == "construction_cap"
        [ref] = result["_meta"]["omitted"]["refs"]
        recovered = await get_symbol(ref)
        assert "Redis session cache decision 8" in recovered["content"]
        assert '"evidence_refs"' in recovered["content"]
    finally:
        await _uninstall(registry)


@pytest.mark.asyncio
async def test_workspace_and_single_repo_share_alias_scoped_evidence_ids(tmp_path):
    """A persisted display name must not change identity across query modes."""
    import json

    from repowise.server.mcp_server import get_why, tool_middleware

    ctx = await _store(
        tmp_path,
        "frontend",
        [
            _decision(
                "fe-1",
                "repo-frontend",
                "Redis session cache",
                "Use Redis as the session cache backend",
                evidence_commits_json='["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]',
            )
        ],
        repo_name="web-client",
    )
    registry = _install({"frontend": ctx}, "frontend", tmp_path)
    try:
        query = "why is Redis the session cache backend"
        call = tool_middleware(get_why)
        single = await call(query=query, repo="frontend")
        workspace = await call(query=query, repo="all")

        single_ref = single["decisions"][0]["evidence_refs"][0]
        workspace_ref = workspace["decisions"][0]["evidence_refs"][0]
        assert single_ref == workspace_ref
        assert single_ref["repository"] == "frontend"
        for result in (single, workspace):
            accounting = result["_meta"]["response_budget"]
            assert accounting["serialized_chars"] == len(
                json.dumps(result, separators=(",", ":"), default=str)
            )
    finally:
        await _uninstall(registry)
