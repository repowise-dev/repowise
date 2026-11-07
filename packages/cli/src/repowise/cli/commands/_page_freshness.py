"""Read the persisted documentation freshness for CLI consumers."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from repowise.cli.helpers import db_configured, get_db_url_for_repo
from repowise.core.generation.page_selection import STALE_STATUSES
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_repository_by_path,
    get_session,
)
from repowise.core.persistence.models import Page


async def stale_page_count(repo_path: Path) -> int | None:
    """Count stale pages without loading their bodies; None means unreadable."""
    if not (repo_path / ".repowise" / "wiki.db").exists() and not db_configured():
        return 0
    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return 0
            result = await session.execute(
                select(func.count())
                .select_from(Page)
                .where(
                    Page.repository_id == repo.id,
                    Page.freshness_status.in_(STALE_STATUSES),
                )
            )
            return int(result.scalar_one())
    except SQLAlchemyError:
        return None
    finally:
        await engine.dispose()
