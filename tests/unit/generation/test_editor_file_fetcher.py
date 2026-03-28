"""Unit tests for EditorFileDataFetcher DB queries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.editor_files.fetcher import EditorFileDataFetcher
from repowise.core.persistence.crud import upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphNode,
    Page,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(async_engine):
    factory = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as sess:
        yield sess


@pytest.fixture
async def repo(session):
    r = await upsert_repository(
        session,
        name="test-repo",
        local_path="/tmp/test-repo",
        url="",
    )
    await session.commit()
    return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _add_graph_node(session, repo_id, node_id, *, is_entry_point=False, pagerank=0.1):
    node = GraphNode(
        repository_id=repo_id,
        node_id=node_id,
        node_type="file",
        language="python",
        is_entry_point=is_entry_point,
        pagerank=pagerank,
    )
    session.add(node)
    await session.flush()
    return node


async def _add_page(session, repo_id, page_id, page_type, target_path, content):
    page = Page(
        id=page_id,
        repository_id=repo_id,
        page_type=page_type,
        title=target_path,
        content=content,
        target_path=target_path,
        source_hash="abc",
        model_name="mock",
        provider_name="mock",
        generation_level=0,
        confidence=0.9,
        freshness_status="fresh",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(page)
    await session.flush()
    return page


async def _add_git_meta(
    session, repo_id, file_path, *, is_hotspot=False, churn_pct=0.5, owner=None
):
    gm = GitMetadata(
        repository_id=repo_id,
        file_path=file_path,
        is_hotspot=is_hotspot,
        churn_percentile=churn_pct,
        commit_count_90d=10,
        primary_owner_name=owner,
    )
    session.add(gm)
    await session.flush()
    return gm


async def _add_decision(session, repo_id, title, status="active", rationale="Some reason"):
    dr = DecisionRecord(
        repository_id=repo_id,
        title=title,
        status=status,
        rationale=rationale,
        decision="Decided to use X",
        context="Context here",
        source="inline_marker",
        staleness_score=0.0,
    )
    session.add(dr)
    await session.flush()
    return dr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_fetch_empty_db_returns_defaults(session, repo, tmp_path):
    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert data.repo_name == "test-repo"
    assert data.architecture_summary == ""
    assert data.key_modules == []
    assert data.entry_points == []
    assert data.hotspots == []
    assert data.decisions == []
    assert data.avg_confidence == 0.0


async def test_fetch_repo_name(session, repo, tmp_path):
    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()
    assert data.repo_name == "test-repo"


async def test_fetch_architecture_summary(session, repo, tmp_path):
    content = (
        "## Overview\n\n"
        "This is a FastAPI application. It handles user authentication. "
        "PostgreSQL is used for persistence. Redis backs the cache.\n"
    )
    await _add_page(session, repo.id, "repo_overview:.", "repo_overview", ".", content)
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert data.architecture_summary != ""
    assert "FastAPI" in data.architecture_summary


async def test_fetch_entry_points(session, repo, tmp_path):
    await _add_graph_node(session, repo.id, "src/main.py", is_entry_point=True, pagerank=0.8)
    await _add_graph_node(session, repo.id, "src/worker.py", is_entry_point=True, pagerank=0.3)
    await _add_graph_node(session, repo.id, "src/utils.py", is_entry_point=False)
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert "src/main.py" in data.entry_points
    assert "src/worker.py" in data.entry_points
    assert "src/utils.py" not in data.entry_points


async def test_fetch_entry_points_sorted_by_pagerank(session, repo, tmp_path):
    await _add_graph_node(session, repo.id, "src/low.py", is_entry_point=True, pagerank=0.1)
    await _add_graph_node(session, repo.id, "src/high.py", is_entry_point=True, pagerank=0.9)
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert data.entry_points[0] == "src/high.py"


async def test_fetch_hotspots(session, repo, tmp_path):
    await _add_git_meta(
        session, repo.id, "src/billing.py", is_hotspot=True, churn_pct=0.95, owner="@alice"
    )
    await _add_git_meta(session, repo.id, "src/utils.py", is_hotspot=False, churn_pct=0.10)
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert len(data.hotspots) == 1
    assert data.hotspots[0].path == "src/billing.py"
    assert data.hotspots[0].owner == "@alice"
    assert data.hotspots[0].churn_percentile == 95.0  # stored 0.95 → displayed 95.0


async def test_fetch_active_decisions_only(session, repo, tmp_path):
    await _add_decision(session, repo.id, "Use JWT", status="active")
    await _add_decision(session, repo.id, "Old choice", status="deprecated")
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    titles = [d.title for d in data.decisions]
    assert "Use JWT" in titles
    assert "Old choice" not in titles


async def test_fetch_avg_confidence(session, repo, tmp_path):
    await _add_page(session, repo.id, "file_page:src/a.py", "file_page", "src/a.py", "content")
    # Update confidence manually
    from sqlalchemy import update

    await session.execute(
        update(Page).where(Page.id == "file_page:src/a.py").values(confidence=0.8)
    )
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert data.avg_confidence == pytest.approx(0.8, abs=0.01)


async def test_fetch_indexed_at_is_date_string(session, repo, tmp_path):
    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    import re

    assert re.match(r"^\d{4}-\d{2}-\d{2}$", data.indexed_at)
