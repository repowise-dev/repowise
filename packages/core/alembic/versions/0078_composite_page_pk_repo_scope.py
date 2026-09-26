"""Scope wiki_pages primary key and wiki_page_versions foreign key by repository_id.

Changes wiki_pages primary key from single-column id to composite (repository_id, id)
so multiple repositories sharing a single database (e.g. PostgreSQL) do not collide
or overwrite pages sharing identical relative paths.

Updates wiki_page_versions to reference the composite key (repository_id, page_id).

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        # 1. Drop existing FK from wiki_page_versions to wiki_pages
        op.execute(
            "ALTER TABLE wiki_page_versions DROP CONSTRAINT IF EXISTS wiki_page_versions_page_id_fkey"
        )
        # 2. Drop single-column PK on wiki_pages
        op.execute("ALTER TABLE wiki_pages DROP CONSTRAINT IF EXISTS wiki_pages_pkey")
        # 3. Create composite primary key on wiki_pages(repository_id, id)
        op.create_primary_key("wiki_pages_pkey", "wiki_pages", ["repository_id", "id"])
        # 4. Create composite FK on wiki_page_versions
        op.create_foreign_key(
            "fk_wiki_page_versions_repo_page",
            "wiki_page_versions",
            "wiki_pages",
            ["repository_id", "page_id"],
            ["repository_id", "id"],
            ondelete="CASCADE",
        )
        # 5. Index for foreign key lookups
        op.create_index(
            "ix_wiki_page_versions_repo_page",
            "wiki_page_versions",
            ["repository_id", "page_id"],
            if_not_exists=True,
        )
    elif dialect == "sqlite":
        with op.batch_alter_table("wiki_pages") as batch_op:
            batch_op.alter_column("id", existing_type=sa.Text(), primary_key=False)
            batch_op.create_primary_key("pk_wiki_pages", ["repository_id", "id"])
        with op.batch_alter_table("wiki_page_versions") as batch_op:
            batch_op.create_foreign_key(
                "fk_wiki_page_versions_repo_page",
                "wiki_pages",
                ["repository_id", "page_id"],
                ["repository_id", "id"],
                ondelete="CASCADE",
            )
            batch_op.create_index(
                "ix_wiki_page_versions_repo_page",
                ["repository_id", "page_id"],
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        op.drop_index("ix_wiki_page_versions_repo_page", table_name="wiki_page_versions")
        op.drop_constraint(
            "fk_wiki_page_versions_repo_page", "wiki_page_versions", type_="foreignkey"
        )
        op.drop_constraint("wiki_pages_pkey", "wiki_pages", type_="primary")
        op.create_primary_key("wiki_pages_pkey", "wiki_pages", ["id"])
        op.create_foreign_key(
            "wiki_page_versions_page_id_fkey",
            "wiki_page_versions",
            "wiki_pages",
            ["page_id"],
            ["id"],
        )
    elif dialect == "sqlite":
        with op.batch_alter_table("wiki_page_versions") as batch_op:
            batch_op.drop_index("ix_wiki_page_versions_repo_page")
            batch_op.drop_constraint("fk_wiki_page_versions_repo_page", type_="foreignkey")
            batch_op.create_foreign_key(
                "wiki_page_versions_page_id_fkey",
                "wiki_pages",
                ["page_id"],
                ["id"],
            )
        with op.batch_alter_table("wiki_pages") as batch_op:
            batch_op.alter_column("repository_id", primary_key=False)
            batch_op.create_primary_key("pk_wiki_pages", ["id"])
