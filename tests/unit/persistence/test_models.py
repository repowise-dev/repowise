"""Unit tests for SQLAlchemy ORM models.

Tests cover model construction, default values, constraints, and field types.
No CRUD or database queries — just ORM-layer behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from repowise.core.persistence.models import (
    Base,
    GenerationJob,
    GraphEdge,
    GraphNode,
    Page,
    PageVersion,
    Repository,
    WebhookEvent,
    WikiSymbol,
    _new_uuid,
)

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _repo(**kwargs) -> Repository:
    return Repository(
        id=_new_uuid(),
        name=kwargs.get("name", "test-repo"),
        local_path=kwargs.get("local_path", "/tmp/test"),
        url=kwargs.get("url", ""),
    )


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def test_repository_has_expected_tablename():
    assert Repository.__tablename__ == "repositories"


def test_repository_defaults():
    """SQLAlchemy INSERT-time defaults; verify the column metadata, not Python init."""
    col_defaults = {
        c.name: c.default for c in Repository.__table__.columns if c.default is not None
    }
    assert "default_branch" in col_defaults
    assert col_defaults["default_branch"].arg == "main"
    # head_commit is nullable
    assert Repository.__table__.c.head_commit.nullable is True


def test_repository_id_is_string():
    repo = _repo()
    assert isinstance(repo.id, str)
    assert len(repo.id) == 32  # UUID hex


# ---------------------------------------------------------------------------
# GenerationJob
# ---------------------------------------------------------------------------


def test_generation_job_has_expected_tablename():
    assert GenerationJob.__tablename__ == "generation_jobs"


def test_generation_job_defaults():
    """Verify column-level defaults (INSERT-time); not Python constructor defaults."""
    col_defaults = {
        c.name: c.default for c in GenerationJob.__table__.columns if c.default is not None
    }
    assert col_defaults["status"].arg == "pending"
    assert col_defaults["total_pages"].arg == 0
    assert col_defaults["completed_pages"].arg == 0
    assert col_defaults["failed_pages"].arg == 0
    # started_at / finished_at are nullable
    assert GenerationJob.__table__.c.started_at.nullable is True
    assert GenerationJob.__table__.c.finished_at.nullable is True


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def test_page_has_expected_tablename():
    assert Page.__tablename__ == "wiki_pages"


def test_page_id_format_accepted():
    """Page.id is the page_id string 'page_type:target_path'."""
    now = _now()
    page = Page(
        id="file_page:src/main.py",
        repository_id="repo1",
        page_type="file_page",
        title="main.py",
        content="content",
        target_path="src/main.py",
        source_hash="abc",
        model_name="mock",
        provider_name="mock",
        created_at=now,
        updated_at=now,
    )
    assert page.id == "file_page:src/main.py"


def test_page_defaults():
    """Verify INSERT-time column defaults via table metadata."""
    col_defaults = {c.name: c.default for c in Page.__table__.columns if c.default is not None}
    assert col_defaults["version"].arg == 1
    assert col_defaults["confidence"].arg == 1.0
    assert col_defaults["freshness_status"].arg == "fresh"
    assert col_defaults["metadata_json"].arg == "{}"
    assert col_defaults["input_tokens"].arg == 0
    assert col_defaults["cached_tokens"].arg == 0


def test_page_metadata_json_is_not_named_metadata():
    """metadata is a SQLAlchemy reserved name on Base; we use metadata_json."""
    page = Page.__table__.c
    col_names = {c.name for c in page}
    assert "metadata_json" in col_names
    assert "metadata" not in col_names


# ---------------------------------------------------------------------------
# PageVersion
# ---------------------------------------------------------------------------


def test_page_version_has_expected_tablename():
    assert PageVersion.__tablename__ == "wiki_page_versions"


def test_page_version_fields():
    pv = PageVersion(
        id=_new_uuid(),
        page_id="file_page:src/a.py",
        repository_id="r1",
        version=1,
        page_type="file_page",
        title="a.py",
        content="old content",
        source_hash="old_hash",
        model_name="mock",
        provider_name="mock",
    )
    assert pv.version == 1
    # confidence has INSERT-time default 1.0; verify via column metadata
    col_defaults = {
        c.name: c.default for c in PageVersion.__table__.columns if c.default is not None
    }
    assert col_defaults["confidence"].arg == 1.0


# ---------------------------------------------------------------------------
# GraphNode
# ---------------------------------------------------------------------------


def test_graph_node_has_expected_tablename():
    assert GraphNode.__tablename__ == "graph_nodes"


def test_graph_node_defaults():
    """Verify INSERT-time column defaults via table metadata."""
    col_defaults = {c.name: c.default for c in GraphNode.__table__.columns if c.default is not None}
    assert col_defaults["node_type"].arg == "file"
    assert col_defaults["pagerank"].arg == 0.0
    assert col_defaults["betweenness"].arg == 0.0
    assert col_defaults["community_id"].arg == 0


def test_graph_node_unique_constraint_defined():
    """The UniqueConstraint on (repository_id, node_id) must exist."""
    constraint_names = {c.name for c in GraphNode.__table__.constraints if hasattr(c, "name")}
    assert "uq_graph_node" in constraint_names


async def test_graph_node_unique_constraint_enforced(async_session):
    """Inserting two nodes with the same (repository_id, node_id) raises IntegrityError."""
    from repowise.core.persistence.crud import upsert_repository

    repo = await upsert_repository(async_session, name="r", local_path="/tmp/uq-test")
    await async_session.commit()

    n1 = GraphNode(id=_new_uuid(), repository_id=repo.id, node_id="src/a.py")
    n2 = GraphNode(id=_new_uuid(), repository_id=repo.id, node_id="src/a.py")
    async_session.add(n1)
    await async_session.flush()

    async_session.add(n2)
    with pytest.raises(IntegrityError):
        await async_session.flush()


# ---------------------------------------------------------------------------
# GraphEdge
# ---------------------------------------------------------------------------


def test_graph_edge_has_expected_tablename():
    assert GraphEdge.__tablename__ == "graph_edges"


def test_graph_edge_defaults():
    col_defaults = {c.name: c.default for c in GraphEdge.__table__.columns if c.default is not None}
    assert col_defaults["imported_names_json"].arg == "[]"


# ---------------------------------------------------------------------------
# WebhookEvent
# ---------------------------------------------------------------------------


def test_webhook_event_has_expected_tablename():
    assert WebhookEvent.__tablename__ == "webhook_events"


def test_webhook_event_nullable_repository_id():
    """repository_id is nullable — webhook may arrive before repo is registered."""
    we = WebhookEvent(
        id=_new_uuid(),
        provider="github",
        event_type="push",
    )
    assert we.repository_id is None


def test_webhook_event_defaults():
    col_defaults = {
        c.name: c.default for c in WebhookEvent.__table__.columns if c.default is not None
    }
    # processed defaults to False (stored as 0 in SQLite)
    assert col_defaults["processed"].arg == False  # noqa: E712
    # job_id is nullable
    assert WebhookEvent.__table__.c.job_id.nullable is True


# ---------------------------------------------------------------------------
# WikiSymbol
# ---------------------------------------------------------------------------


def test_wiki_symbol_has_expected_tablename():
    assert WikiSymbol.__tablename__ == "wiki_symbols"


def test_wiki_symbol_name_does_not_shadow_ingestion_symbol():
    """The ORM class is named WikiSymbol to avoid shadowing ingestion.models.Symbol."""
    assert WikiSymbol.__name__ == "WikiSymbol"


def test_wiki_symbol_defaults():
    col_defaults = {
        c.name: c.default for c in WikiSymbol.__table__.columns if c.default is not None
    }
    assert col_defaults["visibility"].arg == "public"
    assert col_defaults["is_async"].arg == False  # noqa: E712
    assert col_defaults["complexity_estimate"].arg == 0
    # docstring and parent_name are nullable
    assert WikiSymbol.__table__.c.docstring.nullable is True
    assert WikiSymbol.__table__.c.parent_name.nullable is True


def test_wiki_symbol_unique_constraint_defined():
    constraint_names = {c.name for c in WikiSymbol.__table__.constraints if hasattr(c, "name")}
    assert "uq_wiki_symbol" in constraint_names


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


def test_base_includes_all_models():
    table_names = set(Base.metadata.tables.keys())
    expected = {
        "repositories",
        "generation_jobs",
        "wiki_pages",
        "wiki_page_versions",
        "graph_nodes",
        "graph_edges",
        "webhook_events",
        "wiki_symbols",
        "git_metadata",
        "dead_code_findings",
        "decision_records",
        "conversations",
        "chat_messages",
    }
    assert expected == table_names
