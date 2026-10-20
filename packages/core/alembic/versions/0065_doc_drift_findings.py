"""Add doc_drift_findings table.

Stores assertions a document makes that the repository no longer satisfies: a
path that no longer resolves, a link whose target moved, an in-page anchor
whose heading was renamed, a build target the manifest does not declare.

``file_path`` is the document that is wrong, not the target it names, because
the document is what a reader has to edit.

The unique constraint over
``(repository_id, file_path, kind, line_number, target)`` is what makes
re-running the pass converge instead of accumulating duplicates. ``target``
belongs in the key because one line can carry two references: a markdown link
with a fragment yields both a ``link`` row and an ``anchor`` row at the same
line, and a key without ``target`` would collapse them into one.

Local SQLite stores never run Alembic --- ``init_db``'s reconciler calls
``Base.metadata.create_all``, which creates a brand-new table with its full
current schema --- so this migration exists for managed Postgres. No
reconciler change is needed for a new table; that gap is only for columns
added to tables that already exist.

Revision ID: 0065
Revises: 0064
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doc_drift_findings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("target", sa.String(1024), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("origin", sa.String(64), nullable=False, server_default=""),
        sa.Column("evidence_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("raw", sa.Text, nullable=False, server_default=""),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "repository_id",
            "file_path",
            "kind",
            "line_number",
            "target",
            name="uq_doc_drift_finding_site",
        ),
    )
    op.create_index(
        "ix_doc_drift_repo_path",
        "doc_drift_findings",
        ["repository_id", "file_path"],
    )


def downgrade() -> None:
    op.drop_index("ix_doc_drift_repo_path", table_name="doc_drift_findings")
    op.drop_table("doc_drift_findings")
