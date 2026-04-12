"""Tests for repowise.core.workspace.registry — RepoRegistry and RepoContext."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Page, Repository
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import RepoContext, RepoRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 12, 10, 0, 0, tzinfo=UTC)


def _make_workspace(tmp_path: Path, repo_names: list[str], default: str | None = None) -> WorkspaceConfig:
    """Create repo dirs with .repowise/wiki.db and return a WorkspaceConfig."""
    entries = []
    for name in repo_names:
        repo_dir = tmp_path / name
        repo_dir.mkdir(parents=True, exist_ok=True)
        repowise_dir = repo_dir / ".repowise"
        repowise_dir.mkdir(exist_ok=True)
        # Create an empty wiki.db — init_db will create tables on first access
        (repowise_dir / "wiki.db").write_bytes(b"")
        entries.append(RepoEntry(path=name, alias=name))

    if entries and not default:
        default = entries[0].alias
        entries[0].is_primary = True

    config = WorkspaceConfig(
        version=1,
        repos=entries,
        default_repo=default,
    )
    config.save(tmp_path)
    return config


async def _seed_repo_db(repo_path: Path, repo_name: str) -> None:
    """Seed a repo's wiki.db with minimal data for testing."""
    db_path = repo_path / ".repowise" / "wiki.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_async_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        repo = Repository(
            id=f"repo-{repo_name}",
            name=repo_name,
            url=f"https://example.com/{repo_name}",
            local_path=str(repo_path),
            default_branch="main",
            settings_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        )
        session.add(repo)
        session.add(Page(
            id=f"repo_overview:{repo_name}",
            repository_id=repo.id,
            page_type="repo_overview",
            title=f"{repo_name} Overview",
            content=f"# {repo_name}\n\nOverview of {repo_name}.",
            target_path=repo_name,
            source_hash="abc",
            model_name="mock",
            provider_name="mock",
            generation_level=6,
            confidence=1.0,
            freshness_status="fresh",
            metadata_json="{}",
            created_at=_NOW,
            updated_at=_NOW,
        ))
        await session.commit()
    await engine.dispose()


# ---------------------------------------------------------------------------
# resolve_repo_param
# ---------------------------------------------------------------------------


class TestResolveRepoParam:
    def test_none_returns_default(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend"], default="backend")
        registry = RepoRegistry(tmp_path, config)
        assert registry.resolve_repo_param(None) == "backend"

    def test_all_returns_all_aliases(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend", "shared"])
        registry = RepoRegistry(tmp_path, config)
        result = registry.resolve_repo_param("all")
        assert isinstance(result, list)
        assert set(result) == {"backend", "frontend", "shared"}

    def test_valid_alias(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend"])
        registry = RepoRegistry(tmp_path, config)
        assert registry.resolve_repo_param("frontend") == "frontend"

    def test_invalid_alias_raises(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend"])
        registry = RepoRegistry(tmp_path, config)
        with pytest.raises(ValueError, match="Unknown repo"):
            registry.resolve_repo_param("nonexistent")


# ---------------------------------------------------------------------------
# Lazy loading
# ---------------------------------------------------------------------------


class TestLazyLoading:
    @pytest.mark.asyncio
    async def test_get_loads_context(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend"])
        await _seed_repo_db(tmp_path / "backend", "backend")
        registry = RepoRegistry(tmp_path, config)

        ctx = await registry.get("backend")
        assert isinstance(ctx, RepoContext)
        assert ctx.alias == "backend"
        assert ctx.session_factory is not None
        assert ctx.fts is not None

        await registry.close()

    @pytest.mark.asyncio
    async def test_get_default(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend"], default="frontend")
        await _seed_repo_db(tmp_path / "frontend", "frontend")
        registry = RepoRegistry(tmp_path, config)

        ctx = await registry.get_default()
        assert ctx.alias == "frontend"

        await registry.close()

    @pytest.mark.asyncio
    async def test_same_alias_returns_cached(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend"])
        await _seed_repo_db(tmp_path / "backend", "backend")
        registry = RepoRegistry(tmp_path, config)

        ctx1 = await registry.get("backend")
        ctx2 = await registry.get("backend")
        assert ctx1 is ctx2

        await registry.close()


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_evicts_at_capacity(self, tmp_path: Path) -> None:
        # Create 4 repos, set MAX_LOADED=3
        names = ["repo1", "repo2", "repo3", "repo4"]
        config = _make_workspace(tmp_path, names, default="repo1")
        for name in names:
            await _seed_repo_db(tmp_path / name, name)

        registry = RepoRegistry(tmp_path, config)
        registry.MAX_LOADED = 3

        # Load 3 repos — should be at capacity
        await registry.get("repo1")
        await registry.get("repo2")
        await registry.get("repo3")
        assert len(registry._contexts) == 3

        # Loading repo4 should evict the LRU (repo1 is default, so repo2 gets evicted)
        # Access repo1 again to make repo2 the LRU
        await registry.get("repo1")
        await registry.get("repo4")
        assert len(registry._contexts) == 3
        assert "repo2" not in registry._contexts
        assert "repo4" in registry._contexts

        await registry.close()

    @pytest.mark.asyncio
    async def test_default_never_evicted(self, tmp_path: Path) -> None:
        names = ["default_repo", "other1", "other2", "other3"]
        config = _make_workspace(tmp_path, names, default="default_repo")
        for name in names:
            await _seed_repo_db(tmp_path / name, name)

        registry = RepoRegistry(tmp_path, config)
        registry.MAX_LOADED = 2

        # Load default + other1
        await registry.get("default_repo")
        await registry.get("other1")
        assert len(registry._contexts) == 2

        # Load other2 — should evict other1, not default_repo
        await registry.get("other2")
        assert "default_repo" in registry._contexts
        assert "other1" not in registry._contexts

        await registry.close()


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


class TestClose:
    @pytest.mark.asyncio
    async def test_close_disposes_all(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["backend", "frontend"])
        await _seed_repo_db(tmp_path / "backend", "backend")
        await _seed_repo_db(tmp_path / "frontend", "frontend")
        registry = RepoRegistry(tmp_path, config)

        await registry.get("backend")
        await registry.get("frontend")
        assert len(registry._contexts) == 2

        await registry.close()
        assert len(registry._contexts) == 0


# ---------------------------------------------------------------------------
# get_all_aliases
# ---------------------------------------------------------------------------


class TestGetAllAliases:
    def test_returns_all(self, tmp_path: Path) -> None:
        config = _make_workspace(tmp_path, ["a", "b", "c"])
        registry = RepoRegistry(tmp_path, config)
        assert registry.get_all_aliases() == ["a", "b", "c"]
