"""Add security_findings table.

Stores lightweight security signals detected during file ingestion,
including eval/exec calls, hardcoded secrets, raw SQL, weak hashes, etc.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "security_findings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("snippet", sa.Text, nullable=True),
        sa.Column("line_number", sa.Integer, nullable=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_security_findings_repo_file",
        "security_findings",
        ["repository_id", "file_path"],
    )


def downgrade() -> None:
    op.drop_index("ix_security_findings_repo_file", table_name="security_findings")
    op.drop_table("security_findings")
