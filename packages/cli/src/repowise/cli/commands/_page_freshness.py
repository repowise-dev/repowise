"""Read the persisted documentation freshness for CLI consumers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from repowise.cli.helpers import db_configured, get_db_url_for_repo
from repowise.core.generation.models import (
    MODEL_FREE_PAGE_TYPES,
    MODEL_WRITTEN_PAGE_TYPES,
    is_model_free,
)
from repowise.core.generation.page_selection import STALE_STATUSES
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_repository_by_path,
    get_session,
)
from repowise.core.persistence.models import Page


async def stale_page_count(repo_path: Path, *, model_written: bool | None = None) -> int | None:
    """Count stale pages without loading their bodies; None means unreadable.

    *model_written* narrows the count to the pages a model writes (True) or to
    the structural ones (False). A model-free page (``is_model_free``) is structural.
    """
    if not (repo_path / ".repowise" / "wiki.db").exists() and not db_configured():
        return 0
    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return 0
            stale = (Page.repository_id == repo.id, Page.freshness_status.in_(STALE_STATUSES))
            query = select(func.count()).select_from(Page).where(*stale)
            if model_written is None:
                return int((await session.execute(query)).scalar_one())
            written = Page.page_type.in_(MODEL_WRITTEN_PAGE_TYPES)
            query = query.where(written if model_written else ~written)
            count = int((await session.execute(query)).scalar_one())
            free = await _stale_model_free_count(session, stale)
            return count - free if model_written else count + free
    except SQLAlchemyError:
        return None
    finally:
        await engine.dispose()


async def _stale_model_free_count(session: Any, stale: tuple) -> int:
    """Stale model-free pages: metadata is read only for the types that can be one."""
    rows = await session.execute(
        select(Page.metadata_json).where(*stale, Page.page_type.in_(MODEL_FREE_PAGE_TYPES))
    )
    return sum(map(is_model_free, rows.scalars()))
