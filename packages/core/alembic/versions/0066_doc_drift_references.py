"""Add doc_drift_references table.

Stores the complement of ``doc_drift_findings``: the references that resolved.
A row says ``document_path`` names ``target_path`` and the repository still has
it, which is what answers "which documents mention this file" without
re-running the pass in a process that holds no ``source_map``.

Two indexes, one per direction, on the ``test_coverage`` pattern. The target
index is the hot one, because the question is asked about a code file --- the
direction ``ix_doc_drift_repo_path`` does not have. The document index serves
the scoped rewrite and the deletion prune.

The unique constraint over
``(repository_id, document_path, kind, line_number, target_path)`` is what
makes re-running the pass converge instead of accumulating duplicates, and
carries ``target_path`` for the same reason the findings key carries
``target``: one line can hold two references.

Local SQLite stores never run Alembic --- ``init_db``'s reconciler calls
``Base.metadata.create_all`` --- so this migration exists for managed
Postgres.

Revision ID: 0066
Revises: 0065
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "doc_drift_references",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_path", sa.String(1024), nullable=False),
        sa.Column("target_path", sa.String(1024), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("line_number", sa.Integer, nullable=False),
        sa.Column("section", sa.Text, nullable=False, server_default=""),
        sa.UniqueConstraint(
            "repository_id",
            "document_path",
            "kind",
            "line_number",
            "target_path",
            name="uq_doc_drift_reference_site",
        ),
    )
    op.create_index(
        "ix_doc_drift_ref_repo_target",
        "doc_drift_references",
        ["repository_id", "target_path"],
    )
    op.create_index(
        "ix_doc_drift_ref_repo_doc",
        "doc_drift_references",
        ["repository_id", "document_path"],
    )


def downgrade() -> None:
    op.drop_index("ix_doc_drift_ref_repo_doc", table_name="doc_drift_references")
    op.drop_index("ix_doc_drift_ref_repo_target", table_name="doc_drift_references")
    op.drop_table("doc_drift_references")
