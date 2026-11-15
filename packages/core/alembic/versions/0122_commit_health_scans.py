"""``git_commit_health_scans``: commits the health scan reached that store no row.

New table only, so an older wiki.db upgrades without rewriting anything; an
empty table means every such commit is scanned once more and then marked.

Revision ID: 0122
Revises: 0121
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0122"
down_revision: str | None = "0121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "git_commit_health_scans"


def upgrade() -> None:
    # init_db may already have created it through its additive reconciler.
    if _TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("analyzer_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("performance_model_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "sha", name="uq_git_commit_health_scan"),
    )


def downgrade() -> None:
    if _TABLE in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table(_TABLE)
