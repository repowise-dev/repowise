"""Tests for KG layer/tour enrichment in MCP tools (Phase 9)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    GraphEdge,
    GraphNode,
    KnowledgeGraphLayer,
    KnowledgeGraphTourStep,
    Page,
    Repository,
    WikiSymbol,
)
from repowise.core.persistence.search import FullTextSearch
from repowise.core.persistence.vector_store import InMemoryVectorStore
from repowise.core.providers.embedding.base import MockEmbedder

_NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
async def session(factory):
    async with factory() as s:
        yield s
        await s.commit()


@pytest.fixture
async def fts(engine):
    f = FullTextSearch(engine)
    await f.ensure_index()
    return f


@pytest.fixture
async def vector_store():
    embedder = MockEmbedder()
    vs = InMemoryVectorStore(embedder=embedder)
    yield vs
    await vs.close()


@pytest.fixture
async def repo_id(session: AsyncSession) -> str:
    repo = Repository(
        id="repo-kg",
        name="kg-test-repo",
        url="https://github.com/example/kg-test-repo",
        local_path="/tmp/kg-test-repo",
        default_branch="main",
        settings_json="{}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(repo)
    await session.flush()
    return repo.id


@pytest.fixture
async def populated_db_with_kg(session: AsyncSession, repo_id: str) -> str:
    rid = repo_id

    pages = [
        Page(
            id="repo_overview:kg-test-repo",
            repository_id=rid,
            page_type="repo_overview",
            title="KG Test Repo Overview",
            content="# KG Test Repo\n\nA repo with knowledge graph data.",
            target_path="kg-test-repo",
            source_hash="abc",
            model_name="mock",
            provider_name="mock",
            generation_level=6,
            confidence=1.0,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="file_page:src/core/graph.py",
            repository_id=rid,
            page_type="file_page",
            title="Graph Builder",
            content="# Graph Builder\n\nBuilds the dependency graph.",
            summary="Builds the dependency graph from parsed files.",
            target_path="src/core/graph.py",
            source_hash="f1",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.9,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
        Page(
            id="file_page:src/utils/helpers.py",
            repository_id=rid,
            page_type="file_page",
            title="Helpers",
            content="# Helpers\n\nUtility functions.",
            summary="General utility helpers.",
            target_path="src/utils/helpers.py",
            source_hash="f2",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.8,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    ]
    for p in pages:
        session.add(p)

    nodes = [
        GraphNode(
            id="gn-graph",
            repository_id=rid,
            node_id="src/core/graph.py",
            node_type="file",
            language="python",
            symbol_count=5,
            is_entry_point=True,
            pagerank=0.8,
            community_id=1,
            created_at=_NOW,
        ),
        GraphNode(
            id="gn-helpers",
            repository_id=rid,
            node_id="src/utils/helpers.py",
            node_type="file",
            language="python",
            symbol_count=3,
            is_entry_point=False,
            pagerank=0.3,
            community_id=2,
            created_at=_NOW,
        ),
    ]
    for n in nodes:
        session.add(n)

    edges = [
        GraphEdge(
            id="ge-cross",
            repository_id=rid,
            source_node_id="src/utils/helpers.py",
            target_node_id="src/core/graph.py",
            edge_type="imports",
            imported_names_json='["build_graph"]',
            created_at=_NOW,
        ),
    ]
    for e in edges:
        session.add(e)

    kg_layers = [
        KnowledgeGraphLayer(
            id="kgl1",
            repository_id=rid,
            layer_id="layer:core",
            name="Core Ingestion",
            description="Core data ingestion and graph building",
            node_ids_json=json.dumps(["file:src/core/graph.py"]),
            display_order=0,
        ),
        KnowledgeGraphLayer(
            id="kgl2",
            repository_id=rid,
            layer_id="layer:utils",
            name="Utilities",
            description="Shared utility code",
            node_ids_json=json.dumps(["file:src/utils/helpers.py"]),
            display_order=1,
        ),
    ]
    for l in kg_layers:
        session.add(l)

    kg_tour = [
        KnowledgeGraphTourStep(
            id="kgt1",
            repository_id=rid,
            step_order=1,
            title="Start with the Graph",
            description="Begin by understanding the graph builder",
            node_ids_json=json.dumps(["file:src/core/graph.py"]),
        ),
        KnowledgeGraphTourStep(
            id="kgt2",
            repository_id=rid,
            step_order=2,
            title="Utility Functions",
            description="Then look at utility code",
            node_ids_json=json.dumps(["file:src/utils/helpers.py"]),
        ),
    ]
    for t in kg_tour:
        session.add(t)

    await session.flush()
    return rid


@pytest.fixture
async def populated_db_no_kg(session: AsyncSession, repo_id: str) -> str:
    rid = repo_id
    session.add(
        Page(
            id="repo_overview:kg-test-repo",
            repository_id=rid,
            page_type="repo_overview",
            title="KG Test Repo Overview",
            content="# KG Test Repo\n\nA repo without knowledge graph data.",
            target_path="kg-test-repo",
            source_hash="abc",
            model_name="mock",
            provider_name="mock",
            generation_level=6,
            confidence=1.0,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    session.add(
        Page(
            id="file_page:src/main.py",
            repository_id=rid,
            page_type="file_page",
            title="Main",
            content="# Main\n\nEntry point.",
            summary="Application entry point.",
            target_path="src/main.py",
            source_hash="f1",
            model_name="mock",
            provider_name="mock",
            generation_level=2,
            confidence=0.9,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ),
    )
    session.add(
        GraphNode(
            id="gn-main",
            repository_id=rid,
            node_id="src/main.py",
            node_type="file",
            language="python",
            symbol_count=2,
            is_entry_point=True,
            pagerank=0.5,
            created_at=_NOW,
        ),
    )
    await session.flush()
    return rid


@pytest.fixture
async def setup_mcp_with_kg(factory, fts, vector_store, populated_db_with_kg):
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._decision_store = InMemoryVectorStore(embedder=MockEmbedder())
    mcp_mod._repo_path = "/tmp/kg-test-repo"

    yield populated_db_with_kg

    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None
    mcp_mod._registry = None
    mcp_mod._workspace_root = None


@pytest.fixture
async def setup_mcp_no_kg(factory, fts, vector_store, populated_db_no_kg):
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    mcp_mod._decision_store = InMemoryVectorStore(embedder=MockEmbedder())
    mcp_mod._repo_path = "/tmp/kg-test-repo"

    yield populated_db_no_kg

    mcp_mod._session_factory = None
    mcp_mod._fts = None
    mcp_mod._vector_store = None
    mcp_mod._decision_store = None
    mcp_mod._repo_path = None
    mcp_mod._registry = None
    mcp_mod._workspace_root = None


# ---------------------------------------------------------------------------
# get_overview tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_includes_architecture_when_kg_exists(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert "architecture" in result
    arch = result["architecture"]
    assert "layers" in arch
    assert len(arch["layers"]) == 2
    assert any(l["name"] == "Core Ingestion" for l in arch["layers"])
    assert any(l["name"] == "Utilities" for l in arch["layers"])
    assert arch["tour_available"] is True
    assert arch["tour_step_count"] == 2


@pytest.mark.asyncio
async def test_overview_omits_architecture_without_kg(setup_mcp_no_kg):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    assert "architecture" not in result


@pytest.mark.asyncio
async def test_overview_layer_file_count(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    core_layer = next(l for l in result["architecture"]["layers"] if l["name"] == "Core Ingestion")
    assert core_layer["file_count"] == 1


@pytest.mark.asyncio
async def test_overview_layer_description_truncated(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_overview

    result = await get_overview()
    for layer in result["architecture"]["layers"]:
        assert len(layer["description"]) <= 120


# ---------------------------------------------------------------------------
# get_context tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_includes_layer_for_file(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/core/graph.py"])
    t = result["targets"]["src/core/graph.py"]
    assert "architectural_layer" in t
    assert t["architectural_layer"]["name"] == "Core Ingestion"
    assert t["architectural_layer"]["role"] in ("entry_point", "internal", "edge_connector")


@pytest.mark.asyncio
async def test_context_edge_connector_role(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/core/graph.py"])
    t = result["targets"]["src/core/graph.py"]
    assert t["architectural_layer"]["role"] == "edge_connector"


@pytest.mark.asyncio
async def test_context_includes_tour_when_on_tour(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/core/graph.py"])
    t = result["targets"]["src/core/graph.py"]
    assert "tour_context" in t
    assert t["tour_context"]["step"] == 1
    assert t["tour_context"]["title"] == "Start with the Graph"


@pytest.mark.asyncio
async def test_context_tour_for_second_file(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/utils/helpers.py"])
    t = result["targets"]["src/utils/helpers.py"]
    assert "tour_context" in t
    assert t["tour_context"]["step"] == 2


@pytest.mark.asyncio
async def test_context_works_without_kg(setup_mcp_no_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/main.py"])
    t = result["targets"]["src/main.py"]
    assert "docs" in t
    assert t["docs"]["summary"] == "Application entry point."
    assert "architectural_layer" not in t
    assert "tour_context" not in t


@pytest.mark.asyncio
async def test_context_layer_description_truncated(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/core/graph.py"])
    t = result["targets"]["src/core/graph.py"]
    assert len(t["architectural_layer"]["description"]) <= 200


@pytest.mark.asyncio
async def test_context_multiple_targets_with_kg(setup_mcp_with_kg):
    from repowise.server.mcp_server import get_context

    result = await get_context(targets=["src/core/graph.py", "src/utils/helpers.py"])
    targets = result["targets"]
    assert "src/core/graph.py" in targets
    assert "src/utils/helpers.py" in targets
    assert "architectural_layer" in targets["src/core/graph.py"]
    assert "architectural_layer" in targets["src/utils/helpers.py"]
    assert targets["src/core/graph.py"]["architectural_layer"]["name"] == "Core Ingestion"
    assert targets["src/utils/helpers.py"]["architectural_layer"]["name"] == "Utilities"
