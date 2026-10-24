"""Migration 0117: Scopes wiki_pages PK and wiki_page_versions FK by repository_id.

Verifies that upgrading a database holding two repositories with the same page id
preserves both rows in wiki_pages and their wiki_page_versions, scoping them by repository_id.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


def _alembic_config(db_path: Path):
    root = Path(__file__).resolve().parents[3] / "packages" / "core"
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return config


def test_migration_0117_duplicate_page_ids_across_repos(tmp_path: Path) -> None:
    db_path = tmp_path / "test_migration.db"
    config = _alembic_config(db_path)

    # Upgrade to the revision right before 0117
    command.upgrade(config, "0116")

    conn = sqlite3.connect(db_path)
    try:
        # Repositories table
        conn.execute(
            "INSERT INTO repositories (id, name, local_path, created_at, updated_at) "
            "VALUES ('repo-1', 'Repo 1', '/tmp/repo1', datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO repositories (id, name, local_path, created_at, updated_at) "
            "VALUES ('repo-2', 'Repo 2', '/tmp/repo2', datetime('now'), datetime('now'))"
        )

        # Before 0117, wiki_pages PK was single-column id.
        # Insert a page in repo-1
        conn.execute(
            "INSERT INTO wiki_pages ("
            "  id, repository_id, page_type, title, content, target_path, "
            "  source_hash, model_name, provider_name, created_at, updated_at"
            ") VALUES ("
            "  'file_page:src/common.py', 'repo-1', 'file_page', 'Repo 1 Common', 'content 1', 'src/common.py', "
            "  'h1', 'm1', 'p1', datetime('now'), datetime('now')"
            ")"
        )
        conn.execute(
            "INSERT INTO wiki_page_versions ("
            "  id, page_id, repository_id, version, page_type, title, content, "
            "  source_hash, model_name, provider_name, archived_at"
            ") VALUES ("
            "  'v1', 'file_page:src/common.py', 'repo-1', 1, 'file_page', 'Repo 1 Common v1', 'content 1 v1', "
            "  'h1', 'm1', 'p1', datetime('now')"
            ")"
        )
        conn.commit()

        # Before 0117, inserting the same page id for repo-2 would fail PK constraint
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wiki_pages ("
                "  id, repository_id, page_type, title, content, target_path, "
                "  source_hash, model_name, provider_name, created_at, updated_at"
                ") VALUES ("
                "  'file_page:src/common.py', 'repo-2', 'file_page', 'Repo 2 Common', 'content 2', 'src/common.py', "
                "  'h2', 'm2', 'p2', datetime('now'), datetime('now')"
                ")"
            )
    finally:
        conn.close()

    # Apply migration 0117
    command.upgrade(config, "0117")

    conn = sqlite3.connect(db_path)
    try:
        # Verify repo-1's existing row survived
        p1 = conn.execute(
            "SELECT repository_id, id, title FROM wiki_pages WHERE repository_id = 'repo-1'"
        ).fetchall()
        assert len(p1) == 1
        assert p1[0] == ("repo-1", "file_page:src/common.py", "Repo 1 Common")

        v1 = conn.execute(
            "SELECT repository_id, page_id, title FROM wiki_page_versions WHERE repository_id = 'repo-1'"
        ).fetchall()
        assert len(v1) == 1
        assert v1[0] == ("repo-1", "file_page:src/common.py", "Repo 1 Common v1")

        # Now, with composite PK (repository_id, id), repo-2 can hold the exact same page_id
        conn.execute(
            "INSERT INTO wiki_pages ("
            "  id, repository_id, page_type, title, content, target_path, "
            "  source_hash, model_name, provider_name, created_at, updated_at"
            ") VALUES ("
            "  'file_page:src/common.py', 'repo-2', 'file_page', 'Repo 2 Common', 'content 2', 'src/common.py', "
            "  'h2', 'm2', 'p2', datetime('now'), datetime('now')"
            ")"
        )
        conn.execute(
            "INSERT INTO wiki_page_versions ("
            "  id, page_id, repository_id, version, page_type, title, content, "
            "  source_hash, model_name, provider_name, archived_at"
            ") VALUES ("
            "  'v2', 'file_page:src/common.py', 'repo-2', 1, 'file_page', 'Repo 2 Common v1', 'content 2 v1', "
            "  'h2', 'm2', 'p2', datetime('now')"
            ")"
        )
        conn.commit()

        # Both rows survive in wiki_pages and wiki_page_versions
        all_pages = conn.execute(
            "SELECT repository_id, id, title FROM wiki_pages ORDER BY repository_id"
        ).fetchall()
        assert len(all_pages) == 2
        assert all_pages[0] == ("repo-1", "file_page:src/common.py", "Repo 1 Common")
        assert all_pages[1] == ("repo-2", "file_page:src/common.py", "Repo 2 Common")

        all_versions = conn.execute(
            "SELECT repository_id, page_id, title FROM wiki_page_versions ORDER BY repository_id"
        ).fetchall()
        assert len(all_versions) == 2
        assert all_versions[0] == ("repo-1", "file_page:src/common.py", "Repo 1 Common v1")
        assert all_versions[1] == ("repo-2", "file_page:src/common.py", "Repo 2 Common v1")

        # Composite PK in the same repo still rejects duplicates
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO wiki_pages ("
                "  id, repository_id, page_type, title, content, target_path, "
                "  source_hash, model_name, provider_name, created_at, updated_at"
                ") VALUES ("
                "  'file_page:src/common.py', 'repo-1', 'file_page', 'Repo 1 Duplicate', 'content dup', 'src/common.py', "
                "  'h1', 'm1', 'p1', datetime('now'), datetime('now')"
                ")"
            )
    finally:
        conn.close()
