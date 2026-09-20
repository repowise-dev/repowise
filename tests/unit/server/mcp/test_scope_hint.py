"""_meta.scope_hint: naming the index layers a reply never touched."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import KnowledgeGraphLayer
from repowise.server.mcp_server import _scope
from repowise.server.mcp_server._scope import unrelated_scope_hint


@pytest.fixture(autouse=True)
def _clear_layer_cache():
    _scope._CACHE.clear()
    yield
    _scope._CACHE.clear()


def _layer(rid: str, layer_id: str, name: str, node_ids: list[str], order: int = 0):
    return KnowledgeGraphLayer(
        repository_id=rid,
        layer_id=layer_id,
        name=name,
        description="",
        node_ids_json=json.dumps(node_ids),
        display_order=order,
    )


async def _seed(session: AsyncSession, rid: str, layers: list[KnowledgeGraphLayer]) -> None:
    for layer in layers:
        session.add(layer)
    await session.flush()


@pytest.mark.asyncio
async def test_prefixed_and_bare_node_ids_both_parse(session: AsyncSession, repo_id: str):
    await _seed(
        session,
        repo_id,
        [
            _layer(repo_id, "tests", "Automated Test Suites", ["file:tests/a.py", "tests/b.py"]),
            _layer(repo_id, "core", "Analysis Core", ["file:src/core.py"]),
        ],
    )

    hint = await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="k1")

    assert hint == "Unrelated to what was served: Automated Test Suites (2 files)."


@pytest.mark.asyncio
async def test_a_served_path_excludes_its_layer(session: AsyncSession, repo_id: str):
    await _seed(
        session,
        repo_id,
        [
            _layer(repo_id, "ui", "Frontend UI", ["file:web/a.tsx", "file:web/b.tsx"]),
            _layer(repo_id, "docs", "Docs Tooling", ["docs/a.md"]),
        ],
    )

    both = await unrelated_scope_hint(session, repo_id, ["web/a.tsx"], cache_key="k2")
    assert both == "Unrelated to what was served: Docs Tooling (1 file)."

    _scope._CACHE.clear()
    none_left = await unrelated_scope_hint(
        session, repo_id, ["web/a.tsx", "docs/a.md"], cache_key="k3"
    )
    assert none_left is None


@pytest.mark.asyncio
async def test_largest_layers_first_and_capped_at_three(session: AsyncSession, repo_id: str):
    await _seed(
        session,
        repo_id,
        [
            _layer(repo_id, "l1", "Alpha", [f"file:a/{i}.py" for i in range(5)]),
            _layer(repo_id, "l2", "Beta", [f"file:b/{i}.py" for i in range(9)]),
            _layer(repo_id, "l3", "Gamma", [f"file:c/{i}.py" for i in range(7)]),
            _layer(repo_id, "l4", "Delta", ["file:d/0.py"]),
            _layer(repo_id, "l5", "Served", ["file:src/core.py"]),
        ],
    )

    hint = await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="k4")

    assert hint == "Unrelated to what was served: Beta (9 files), Gamma (7), Alpha (5)."
    assert "Delta" not in hint


@pytest.mark.asyncio
async def test_the_sentence_stays_under_200_chars_by_dropping_layers(
    session: AsyncSession, repo_id: str
):
    long_name = "Very Long Layer Name That Eats The Budget " * 2
    await _seed(
        session,
        repo_id,
        [
            _layer(repo_id, "l1", long_name + "One", [f"file:a/{i}.py" for i in range(9)]),
            _layer(repo_id, "l2", long_name + "Two", [f"file:b/{i}.py" for i in range(8)]),
            _layer(repo_id, "l3", long_name + "Three", [f"file:c/{i}.py" for i in range(7)]),
            _layer(repo_id, "l4", "Served", ["file:src/core.py"]),
        ],
    )

    hint = await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="k5")

    assert hint is not None
    assert len(hint) <= 200
    # Names are never cut: the list is what shortens.
    assert (long_name + "One") in hint
    assert (long_name + "Three") not in hint


@pytest.mark.asyncio
async def test_none_without_layers_or_without_served_paths(session: AsyncSession, repo_id: str):
    assert await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="k6") is None

    await _seed(session, repo_id, [_layer(repo_id, "l1", "Alpha", ["file:a/0.py"])])
    _scope._CACHE.clear()
    assert await unrelated_scope_hint(session, repo_id, [], cache_key="k7") is None
    assert await unrelated_scope_hint(session, repo_id, ["", None], cache_key="k7") is None


@pytest.mark.asyncio
async def test_layers_are_read_once_per_cache_key(
    session: AsyncSession, repo_id: str, monkeypatch
):
    await _seed(session, repo_id, [_layer(repo_id, "l1", "Alpha", ["file:a/0.py"])])

    import repowise.core.persistence.crud.knowledge_graph as kg_mod

    calls: list[str] = []
    real = kg_mod.get_kg_layers

    async def counted(sess, rid):
        calls.append(rid)
        return await real(sess, rid)

    monkeypatch.setattr(kg_mod, "get_kg_layers", counted)

    await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="commit-a")
    await unrelated_scope_hint(session, repo_id, ["src/other.py"], cache_key="commit-a")
    assert len(calls) == 1

    await unrelated_scope_hint(session, repo_id, ["src/core.py"], cache_key="commit-b")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_get_context_stamps_the_key_only_with_seeded_layers(
    setup_mcp, session: AsyncSession, monkeypatch
):
    from repowise.server.mcp_server.tool_context.context import get_context

    rid = setup_mcp

    bare = await get_context(targets=["src/auth/service.py"])
    assert "scope_hint" not in bare["_meta"]

    await _seed(
        session,
        rid,
        [
            _layer(rid, "tests", "Automated Test Suites", ["file:tests/a.py", "file:tests/b.py"]),
            _layer(rid, "auth", "Auth", ["file:src/auth/service.py"]),
        ],
    )
    _scope._CACHE.clear()

    seeded = await get_context(targets=["src/auth/service.py"])
    assert seeded["_meta"]["scope_hint"] == (
        "Unrelated to what was served: Automated Test Suites (2 files)."
    )


@pytest.mark.asyncio
async def test_answer_projection_stamps_the_key_from_served_paths(
    setup_mcp, session: AsyncSession
):
    from repowise.server.mcp_server.tool_answer.projection import _refresh_freshness

    rid = setup_mcp
    await _seed(
        session,
        rid,
        [
            _layer(rid, "tests", "Automated Test Suites", ["file:tests/a.py", "file:tests/b.py"]),
            _layer(rid, "auth", "Auth", ["file:src/auth/service.py"]),
        ],
    )
    _scope._CACHE.clear()

    payload = {
        "confidence": "high",
        "citations": ["src/auth/service.py"],
        "_meta": {"scope_hint": "stale sentence"},
    }
    await _refresh_freshness(payload, None)

    assert payload["_meta"]["scope_hint"] == (
        "Unrelated to what was served: Automated Test Suites (2 files)."
    )

    empty = {"confidence": "high", "citations": [], "_meta": {"scope_hint": "stale sentence"}}
    await _refresh_freshness(empty, None)
    assert "scope_hint" not in empty["_meta"]
