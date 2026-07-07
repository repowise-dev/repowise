"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GitMetadata, Page
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def multi_module_db(session, populated_db):
    """populated_db + extra module pages that trigger partial-match collisions.

    Adds path-shaped module ids that share the "api" segment so a bare "api"
    target hits more than one module path (the MultipleResultsFound trigger),
    plus a git-only file whose name substring-matches a module path.
    """
    rid = populated_db

    def _module(path: str, title: str) -> Page:
        return Page(
            id=f"module_page:{path}",
            repository_id=rid,
            page_type="module_page",
            title=title,
            content=f"# {title}\n\nmodule body.",
            target_path=path,
            source_hash=f"mod-{path}",
            model_name="mock",
            provider_name="mock",
            generation_level=4,
            confidence=0.9,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        )

    # "api" appears as a segment in two distinct module paths -> would raise
    # MultipleResultsFound under the old substring + scalar_one_or_none rung.
    session.add(_module("src/api", "API Module"))
    session.add(_module("pkg/api", "Pkg API Module"))
    # A different segment that must NOT match a bare "api" target.
    session.add(_module("src/rapid", "Rapid Module"))
    # A module path containing a literal "_" so LIKE-metachar escaping matters.
    session.add(_module("src/a_b", "Underscore Module"))

    # A real file present in git_metadata, with NO file_page, whose path
    # substring-matches the "src/api" module path. Must resolve via the git
    # fallback (target_type file/git), not as the module.
    session.add(
        GitMetadata(
            id="gm-apiclient",
            repository_id=rid,
            file_path="src/api/client.py",
            commit_count_total=3,
            commit_count_90d=1,
            commit_count_30d=0,
            first_commit_at=datetime(2025, 6, 1, tzinfo=UTC),
            last_commit_at=datetime(2026, 1, 2, tzinfo=UTC),
            primary_owner_name="Carol",
            primary_owner_email="carol@example.com",
            primary_owner_commit_pct=1.0,
            top_authors_json=json.dumps([{"name": "Carol", "count": 3}]),
            significant_commits_json=json.dumps([]),
            co_change_partners_json=json.dumps([]),
            is_hotspot=False,
            is_stable=True,
            churn_percentile=0.1,
            age_days=200,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    await session.flush()
    return rid


@pytest.fixture
async def setup_mcp_multi(factory, fts, vector_store, multi_module_db):
    """Wire MCP globals to the multi-module database."""
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._decision_store = InMemoryVectorStore(embedder=MockEmbedder())
    mcp_mod._repo_path = "/tmp/test-repo"

    yield multi_module_db

    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None
    mcp_mod._registry = None
    mcp_mod._workspace_root = None
    mcp_mod._embedder_status = None


@pytest.mark.asyncio
async def test_get_context_single_file(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["src/auth/service.py"],
        include=["docs", "full_doc", "ownership", "last_change", "decisions", "freshness"],
        compact=False,
    )
    targets = result["targets"]
    assert "src/auth/service.py" in targets
    t = targets["src/auth/service.py"]
    assert t["type"] == "file"
    # Docs
    assert t["docs"]["title"] == "Auth Service"
    assert "AuthService" in t["docs"]["content_md"]
    assert len(t["docs"]["symbols"]) == 2
    assert any(s["name"] == "AuthService" for s in t["docs"]["symbols"])
    assert "src/auth/middleware.py" in t["docs"]["imported_by"]
    # Ownership
    assert t["ownership"]["primary_owner"] == "Alice"
    assert t["ownership"]["owner_pct"] == 0.65
    assert t["ownership"]["contributor_count"] == 2
    # Last change
    assert t["last_change"]["author"] == "Alice"
    assert t["last_change"]["days_ago"] == 443
    # Decisions
    assert len(t["decisions"]) >= 1
    assert any(d["title"] == "Use JWT for authentication" for d in t["decisions"])
    # Freshness
    assert t["freshness"]["confidence_score"] == 0.85
    assert t["freshness"]["freshness_status"] == "fresh"
    assert t["freshness"]["is_stale"] is False


@pytest.mark.asyncio
async def test_get_context_single_module(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["src/auth"],
        include=["docs", "full_doc", "ownership", "last_change", "decisions", "freshness"],
        compact=False,
    )
    targets = result["targets"]
    assert "src/auth" in targets
    t = targets["src/auth"]
    assert t["type"] == "module"
    assert t["docs"]["title"] == "Auth Module"
    assert "authentication" in t["docs"]["content_md"].lower()
    assert len(t["docs"]["files"]) == 2  # service.py and middleware.py
    # Freshness from module page
    assert t["freshness"]["confidence_score"] == 0.95


@pytest.mark.asyncio
async def test_get_context_single_symbol(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(
        ["AuthService"],
        include=["docs", "full_doc"],
        compact=False,
    )
    targets = result["targets"]
    assert "AuthService" in targets
    t = targets["AuthService"]
    assert t["type"] == "symbol"
    assert t["docs"]["name"] == "AuthService"
    assert t["docs"]["kind"] == "class"
    assert t["docs"]["signature"] == "class AuthService"
    assert t["docs"]["file_path"] == "src/auth/service.py"
    assert t["docs"]["documentation"]  # Has content from file page


@pytest.mark.asyncio
async def test_get_context_multiple_targets(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py", "src/auth", "AuthService"])
    targets = result["targets"]
    assert len(targets) == 3
    assert targets["src/auth/service.py"]["type"] == "file"
    assert targets["src/auth"]["type"] == "module"
    assert targets["AuthService"]["type"] == "symbol"


@pytest.mark.asyncio
async def test_get_context_include_filter(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"], include=["docs"])
    t = result["targets"]["src/auth/service.py"]
    # docs + freshness are the always-on defaults (the tool contract says
    # "defaults are always returned"); include only ADDS blocks.
    assert "docs" in t
    assert "freshness" in t
    assert "ownership" not in t
    assert "last_change" not in t
    assert "decisions" not in t


@pytest.mark.asyncio
async def test_get_context_skeleton_keeps_default_card(setup_mcp):
    """include=["skeleton"] must not silently drop the summary/freshness card."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"], include=["skeleton"])
    t = result["targets"]["src/auth/service.py"]
    assert "freshness" in t
    assert "docs" in t
    assert t["docs"].get("summary")
    # ...but the symbol list is suppressed: the skeleton already carries
    # every signature, so repeating it in docs would double the response.
    assert "symbols" not in t["docs"]


@pytest.mark.asyncio
async def test_get_context_not_found(setup_mcp):
    from repowise.server.mcp_server import get_context

    result = await get_context(["nonexistent_thing_xyz"])
    t = result["targets"]["nonexistent_thing_xyz"]
    assert "error" in t


@pytest.mark.asyncio
async def test_get_context_legacy_community_id_hint(setup_mcp):
    """Old community-ordinal module ids get a redirect to path-shaped ids."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["community-12"])
    t = result["targets"]["community-12"]
    assert "error" in t
    assert "directory" in t["error"]
    # Suggestions list the real module pages by their directory path.
    assert "src/auth" in t["suggestions"]
    assert "src/db" in t["suggestions"]


def _make_big_response(n_targets: int = 5, n_symbols: int = 80, body_chars: int = 4000) -> dict:
    """Build a synthetic get_context response well over the 32 KB budget."""
    targets = {}
    for i in range(n_targets):
        name = f"pkg/mod_{i}/file_{i}.ext"
        targets[name] = {
            "target": name,
            "type": "file",
            "docs": {
                "title": f"File {i}",
                "summary": "s" * 200,
                "content_md": "x" * body_chars,
                "symbols": [
                    {
                        "name": f"Sym{i}_{j}",
                        "kind": "class" if j % 5 == 0 else "function",
                        "signature": f"sig_{j}(...)",
                        "start_line": j * 10,
                        "end_line": j * 10 + 8,
                        "docstring": "d" * 300,
                    }
                    for j in range(n_symbols)
                ],
            },
        }
    return {"targets": targets, "_meta": {"timing_ms": 1.0}}


def test_truncate_to_budget_enforces_cap():
    from repowise.server.mcp_server.tool_context import (
        _CHAR_BUDGET,
        _truncate_to_budget,
    )

    big = _make_big_response()
    raw_size = len(json.dumps(big, separators=(",", ":"), default=str))
    assert raw_size > _CHAR_BUDGET, "fixture must exceed budget to be meaningful"

    out = _truncate_to_budget(big)
    final_size = len(json.dumps(out, separators=(",", ":"), default=str))
    assert final_size <= _CHAR_BUDGET
    assert out["truncated"] is True
    # At least one target must survive.
    assert len(out["targets"]) >= 1


def test_truncate_flags_and_dropped_fields_populate():
    from repowise.server.mcp_server.tool_context import _truncate_to_budget

    big = _make_big_response(n_targets=6, n_symbols=60, body_chars=5000)
    out = _truncate_to_budget(big)

    assert out["truncated"] is True
    # Either whole targets were dropped, or individual symbols were dropped —
    # both are acceptable outcomes; at least one must be populated.
    dropped_any = bool(out["dropped_targets"]) or bool(out["dropped_symbols"])
    assert dropped_any
    # Heavy optional fields should have been stripped from surviving targets.
    for tgt in out["targets"].values():
        assert "content_md" not in tgt.get("docs", {})
    # Dropped symbol lists (if any) must reference actual symbol names.
    for tgt_name, names in out["dropped_symbols"].items():
        assert tgt_name in big["targets"] or tgt_name not in out["targets"]
        assert all(isinstance(n, str) for n in names)


def test_truncate_noop_when_under_budget():
    from repowise.server.mcp_server.tool_context import _truncate_to_budget

    small = {
        "targets": {
            "a.py": {
                "target": "a.py",
                "type": "file",
                "docs": {"title": "A", "symbols": [{"name": "f", "kind": "function"}]},
            }
        },
        "_meta": {},
    }
    out = _truncate_to_budget(small)
    assert out["truncated"] is False
    assert out["dropped_targets"] == []
    assert out["dropped_symbols"] == {}
    assert "content_md" not in out["targets"]["a.py"]["docs"]  # wasn't there anyway


# --- Partial-module-match hardening (segment boundary + LIKE escaping) -------


@pytest.mark.asyncio
async def test_partial_module_multi_match_does_not_raise(setup_mcp_multi):
    """A target matching 2+ module paths resolves deterministically, no raise."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["api"])
    t = result["targets"]["api"]
    # Old code raised MultipleResultsFound here. Now it picks the shortest,
    # lexicographically-first matching module path: "pkg/api" vs "src/api"
    # both len 7 -> lexicographic -> "pkg/api".
    assert t.get("type") == "module"
    assert t["docs"]["title"] == "Pkg API Module"


@pytest.mark.asyncio
async def test_partial_module_segment_boundary(setup_mcp_multi):
    """Substrings that aren't whole path segments must not match a module."""
    from repowise.server.mcp_server import get_context

    # "apiclient" / "pi" are substrings of "src/api" but not path segments.
    result = await get_context(["apiclient", "pi", "rapid"])
    targets = result["targets"]
    # apiclient: not a module, not a file -> error
    assert "error" in targets["apiclient"]
    assert targets["apiclient"].get("type") != "module"
    # pi: substring of "rapid"/"api" but not a segment -> not a module
    assert targets["pi"].get("type") != "module"
    # rapid: a full segment of "src/rapid" -> resolves as that module, and a
    # bare "api" must NOT have matched it.
    assert targets["rapid"].get("type") == "module"
    assert targets["rapid"]["docs"]["title"] == "Rapid Module"


@pytest.mark.asyncio
async def test_partial_module_api_matches_only_api_segment(setup_mcp_multi):
    """'api' resolves to an api module, never to 'src/rapid'."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["api"])
    t = result["targets"]["api"]
    assert t["type"] == "module"
    assert "Rapid" not in t["docs"]["title"]


@pytest.mark.asyncio
async def test_partial_module_like_metachars_not_wildcards(setup_mcp_multi):
    """A target with '_' / '%' must match literally, not as a SQL wildcard."""
    from repowise.server.mcp_server import get_context

    # "a%b" would wildcard-match "a_b" if % were treated as a metachar.
    # "axb" would match "a_b" if _ were treated as a single-char wildcard.
    result = await get_context(["a%b", "axb"])
    targets = result["targets"]
    assert targets["a%b"].get("type") != "module"
    assert "error" in targets["a%b"]
    assert targets["axb"].get("type") != "module"
    assert "error" in targets["axb"]
    # Sanity: the literal "a_b" segment DOES resolve as the module.
    result2 = await get_context(["a_b"])
    assert result2["targets"]["a_b"]["type"] == "module"


@pytest.mark.asyncio
async def test_file_on_git_preferred_over_partial_module(setup_mcp_multi):
    """A git-only file substring-matching a module resolves via git, not module."""
    from repowise.server.mcp_server import get_context

    # "src/api/client.py" is in git_metadata, has no file_page, and contains
    # the "src/api" module path as a prefix segment. It must resolve via the
    # git fallback rung, not be captured as the "src/api" module.
    result = await get_context(["src/api/client.py"])
    t = result["targets"]["src/api/client.py"]
    assert t.get("type") != "module"
    assert t.get("exists_in_git") is True
    assert t["primary_owner"] == "Carol"
    assert "error" in t  # "exists but has no wiki page" shape


@pytest.mark.asyncio
async def test_legacy_community_hint_unaffected_by_multi_module(setup_mcp_multi):
    """Extra module pages don't break the legacy community-N redirect rung."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["community-12"])
    t = result["targets"]["community-12"]
    assert "error" in t
    assert "directory" in t["error"]
    assert "src/auth" in t["suggestions"]


@pytest.mark.asyncio
async def test_batch_isolation_one_target_errors(setup_mcp, monkeypatch):
    """One target raising internally yields an error entry; siblings resolve."""
    import repowise.server.mcp_server.tool_context.context as ctx_mod
    from repowise.server.mcp_server import get_context

    real_resolver = ctx_mod._resolve_one_target

    async def flaky(session, repository, target, *args, **kwargs):
        if target == "boom":
            raise RuntimeError("synthetic resolver failure")
        return await real_resolver(session, repository, target, *args, **kwargs)

    monkeypatch.setattr(ctx_mod, "_resolve_one_target", flaky)

    result = await get_context(["src/auth/service.py", "boom"])
    targets = result["targets"]
    # Sibling still resolves normally.
    assert targets["src/auth/service.py"]["type"] == "file"
    # Failing target carries a per-target error entry (keyed on its target).
    assert "boom" in targets
    assert "error" in targets["boom"]
    assert targets["boom"]["target"] == "boom"


@pytest.mark.asyncio
async def test_file_target_callers_rolls_up_importers(setup_mcp):
    """B5: include=["callers"] on a FILE target returns the import rollup
    instead of an empty list (which forced a second round-trip)."""
    from repowise.server.mcp_server import get_context

    result = await get_context(["src/auth/service.py"], include=["callers"], compact=False)
    t = result["targets"]["src/auth/service.py"]
    callers = t.get("callers")
    assert callers, "file-level callers must not be empty when importers exist"
    [middleware] = [c for c in callers if c["file"] == "src/auth/middleware.py"]
    assert middleware.get("imports") is True
    assert "rollup" in t.get("_call_graph_note", "")


@pytest.mark.asyncio
async def test_symbol_callers_carry_definition_line(setup_mcp, session):
    """Symbol-level callers must carry the caller's definition line so the
    agent jumps to it instead of grepping for the call-site position."""
    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
    from repowise.server.mcp_server import get_context

    repo = (await session.execute(__import__("sqlalchemy").select(Repository))).scalars().first()
    target_id = "src/auth/service.py::login"
    caller_id = "src/auth/service.py::caller_fn"
    session.add_all(
        [
            GraphNode(
                id="sgn_login",
                repository_id=repo.id,
                node_id=target_id,
                node_type="symbol",
                name="login",
                file_path="src/auth/service.py",
                kind="method",
                start_line=20,
                end_line=40,
                created_at=_NOW,
            ),
            GraphNode(
                id="sgn_caller",
                repository_id=repo.id,
                node_id=caller_id,
                node_type="symbol",
                name="caller_fn",
                file_path="src/auth/service.py",
                kind="function",
                start_line=55,
                end_line=70,
                created_at=_NOW,
            ),
            GraphEdge(
                id="sge_call",
                repository_id=repo.id,
                source_node_id=caller_id,
                target_node_id=target_id,
                edge_type="calls",
                confidence=0.95,
                created_at=_NOW,
            ),
        ]
    )
    await session.flush()

    result = await get_context([target_id], include=["callers"], compact=False)
    t = result["targets"][target_id]
    [caller] = [c for c in t.get("callers", []) if c["symbol_id"] == caller_id]
    assert caller["line"] == 55


@pytest.mark.asyncio
async def test_high_fan_in_callers_signal_truncation(setup_mcp, session):
    """A symbol with more callers than the display cap must report the TRUE
    total + a truncation flag, so a find-all-callers sweep is not silently
    misled into thinking the partial list is complete (S2 dogfood bug)."""
    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository
    from repowise.server.mcp_server import get_context

    repo = (await session.execute(__import__("sqlalchemy").select(Repository))).scalars().first()
    target_id = "src/db/util.py::hot"
    nodes = [
        GraphNode(
            id="hot_tgt",
            repository_id=repo.id,
            node_id=target_id,
            node_type="symbol",
            name="hot",
            file_path="src/db/util.py",
            kind="function",
            start_line=10,
            end_line=20,
            created_at=_NOW,
        )
    ]
    edges = []
    n_callers = 75  # > the cap of 50
    for i in range(n_callers):
        cid = f"src/callers/c{i}.py::caller_{i}"
        nodes.append(
            GraphNode(
                id=f"hot_caller_{i}",
                repository_id=repo.id,
                node_id=cid,
                node_type="symbol",
                name=f"caller_{i}",
                file_path=f"src/callers/c{i}.py",
                kind="function",
                start_line=i + 1,
                end_line=i + 5,
                created_at=_NOW,
            )
        )
        edges.append(
            GraphEdge(
                id=f"hot_edge_{i}",
                repository_id=repo.id,
                source_node_id=cid,
                target_node_id=target_id,
                edge_type="calls",
                confidence=0.95,
                created_at=_NOW,
            )
        )
    session.add_all(nodes + edges)
    await session.flush()

    result = await get_context([target_id], include=["callers"], compact=False)
    t = result["targets"][target_id]
    assert len(t["callers"]) == 50  # capped display
    assert t["callers_total"] == n_callers  # true total surfaced
    assert t["callers_truncated"] is True
    assert "grep" in t["_callers_note"]
