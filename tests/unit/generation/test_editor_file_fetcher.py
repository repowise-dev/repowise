"""Unit tests for EditorFileDataFetcher DB queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    session,
    repo_id,
    file_path,
    *,
    is_hotspot=False,
    churn_pct=0.5,
    owner=None,
    prior_defect_count=0,
    fix_mass=0.0,
    bug_magnet=False,
    last_fix_at=None,
):
    gm = GitMetadata(
        repository_id=repo_id,
        file_path=file_path,
        is_hotspot=is_hotspot,
        churn_percentile=churn_pct,
        commit_count_90d=10,
        primary_owner_name=owner,
        prior_defect_count=prior_defect_count,
        fix_mass=fix_mass,
        bug_magnet=bug_magnet,
        last_fix_at=last_fix_at,
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


async def test_fetch_entry_points_prefers_curated_list(session, repo, tmp_path):
    # The raw is_entry_point flag tags package-export sinks (a high-pagerank
    # cn.ts-style leaf). When the curation pass has run, its orientation list
    # wins over the flag — the sink must not surface as an entry point.
    import json

    from repowise.core.persistence.models import KnowledgeGraphProjectMeta

    await _add_graph_node(session, repo.id, "src/ui/cn.ts", is_entry_point=True, pagerank=0.99)
    session.add(
        KnowledgeGraphProjectMeta(
            repository_id=repo.id,
            entry_points_json=json.dumps(["src/cli/main.py"]),
            entry_candidates_json=json.dumps(["src/cli/main.py"]),
        )
    )
    await session.commit()

    fetcher = EditorFileDataFetcher(session, repo.id, tmp_path)
    data = await fetcher.fetch()

    assert data.entry_points == ["src/cli/main.py"]
    assert "src/ui/cn.ts" not in data.entry_points


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


async def test_fetch_hotspots_admits_bug_magnets_that_are_not_churn_hotspots(
    session, repo, tmp_path
):
    # The filter used to be is_hotspot-only, so a file fixed four times last
    # month that simply is not busy could never appear no matter how it ranked.
    await _add_git_meta(session, repo.id, "src/busy.py", is_hotspot=True, churn_pct=0.99)
    await _add_git_meta(
        session,
        repo.id,
        "src/broken.py",
        is_hotspot=False,
        churn_pct=0.10,
        bug_magnet=True,
        fix_mass=9.0,
        prior_defect_count=4,
        last_fix_at=datetime.now(UTC) - timedelta(days=14),
    )
    await session.commit()

    data = await EditorFileDataFetcher(session, repo.id, tmp_path).fetch()

    paths = [h.path for h in data.hotspots]
    assert paths == ["src/broken.py", "src/busy.py"]  # fix evidence leads
    assert data.hotspots[0].fix_count == 4
    assert data.hotspots[0].bug_magnet is True
    assert data.hotspots[0].last_fix_age == "2 weeks ago"


async def test_fetch_hotspots_falls_back_to_churn_order_without_fix_data(session, repo, tmp_path):
    # A repo with no fix convention has zero fix mass everywhere; the ordering
    # must degrade to exactly the churn ranking it had before, not to nothing.
    await _add_git_meta(session, repo.id, "src/low.py", is_hotspot=True, churn_pct=0.20)
    await _add_git_meta(session, repo.id, "src/high.py", is_hotspot=True, churn_pct=0.90)
    await session.commit()

    data = await EditorFileDataFetcher(session, repo.id, tmp_path).fetch()

    assert [h.path for h in data.hotspots] == ["src/high.py", "src/low.py"]
    assert all(h.fix_count == 0 and h.last_fix_age is None for h in data.hotspots)


async def test_fetch_hotspots_drops_the_magnet_flag_when_recency_is_unknown(
    session, repo, tmp_path
):
    # bug_magnet with a NULL last_fix_at would render an unanchored accusation.
    await _add_git_meta(
        session,
        repo.id,
        "src/a.py",
        is_hotspot=True,
        bug_magnet=True,
        prior_defect_count=9,
        last_fix_at=None,
    )
    await session.commit()

    data = await EditorFileDataFetcher(session, repo.id, tmp_path).fetch()

    assert data.hotspots[0].bug_magnet is False
    assert data.hotspots[0].last_fix_age is None


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
