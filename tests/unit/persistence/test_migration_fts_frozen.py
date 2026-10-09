"""Migrations that touch full-text search carry their own SQL.

A migration that imports the live search constants replays today's column set
on a database migrated from an older revision, where those columns do not
exist yet. Rendered offline against PostgreSQL, each revision has to create
exactly the index its own schema supports.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_CORE = Path(__file__).resolve().parents[3] / "packages" / "core"
_VERSIONS = _CORE / "alembic" / "versions"


def _offline_sql(monkeypatch, revisions: str) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    buffer = io.StringIO()
    # No ini file: env.py would hand it to logging.fileConfig, which disables
    # every logger already created and breaks log assertions in later tests.
    config = Config(output_buffer=buffer)
    config.set_main_option("script_location", str(_CORE / "alembic"))
    command.upgrade(config, revisions, sql=True)
    return buffer.getvalue()


def test_no_migration_imports_the_live_search_constants():
    offenders = [
        p.name for p in _VERSIONS.glob("*.py") if "repowise.core.persistence.search" in p.read_text()
    ]
    assert offenders == []


@pytest.mark.parametrize(
    ("revisions", "has_digest"), [("0043:0044", False), ("0094:0095", True)]
)
def test_each_revision_builds_the_index_its_schema_supports(monkeypatch, revisions, has_digest):
    sql = _offline_sql(monkeypatch, revisions)
    index = next(line for line in sql.splitlines() if "CREATE INDEX idx_wiki_pages_fts" in line)

    assert "COALESCE(target_path,'')" in index
    assert ("COALESCE(digest,'')" in index) is has_digest


def test_the_sqlite_shapes_around_the_digest_keep_the_vocabulary():
    import importlib.util

    spec = importlib.util.spec_from_file_location("m0095", _VERSIONS / "0095_page_digest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "target_path, vocabulary, digest)" in module.PAGE_FTS_DDL
    assert "target_path, vocabulary)" in module._OLD_SQLITE_DDL
