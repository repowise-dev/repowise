"""Give refactoring plans a versioned identity and a real lifecycle.

Plan rows were replaced wholesale on every analysis, so an id an agent quoted
stopped resolving on the next update and a dismissal had nowhere to live. This
adds the content-derived public id, the model that minted it, the triage reason
and timestamp, and the indexes that let a page be served without loading every
open plan.

Every column is nullable or defaulted and every index is additive, so an
existing store upgrades without a rewrite and repopulates on its next analysis.

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUGGESTION_INDEXES = (
    ("ix_refactoring_suggestions_repo_status", ["repository_id", "status"]),
    (
        "ix_refactoring_suggestions_repo_status_type",
        ["repository_id", "status", "refactoring_type"],
    ),
    (
        "ix_refactoring_suggestions_repo_status_path",
        ["repository_id", "status", "file_path"],
    ),
)

_UNIQUE_NAME = "uq_refactoring_suggestions_repo_model_public_id"


def upgrade() -> None:
    op.add_column("refactoring_suggestions", sa.Column("public_id", sa.String(64), nullable=True))
    op.add_column(
        "refactoring_suggestions",
        sa.Column("model_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "refactoring_suggestions", sa.Column("status_reason", sa.String(32), nullable=True)
    )
    op.add_column(
        "refactoring_suggestions",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, columns in _SUGGESTION_INDEXES:
        op.create_index(name, "refactoring_suggestions", columns, if_not_exists=True)
    # Uniqueness is (repository, model, public id), never the id alone: two
    # models may legitimately name the same plan differently, and a row from an
    # older model keeps answering until the writer resolves it. A unique index
    # rather than a table constraint, because SQLite cannot add a constraint to
    # an existing table without rebuilding it.
    op.create_index(
        _UNIQUE_NAME,
        "refactoring_suggestions",
        ["repository_id", "model_version", "public_id"],
        unique=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(_UNIQUE_NAME, table_name="refactoring_suggestions", if_exists=True)
    for name, _columns in _SUGGESTION_INDEXES:
        op.drop_index(name, table_name="refactoring_suggestions", if_exists=True)
    op.drop_column("refactoring_suggestions", "status_changed_at")
    op.drop_column("refactoring_suggestions", "status_reason")
    op.drop_column("refactoring_suggestions", "model_version")
    op.drop_column("refactoring_suggestions", "public_id")
