"""Bind a repository's decision-vector undo file to its committed SQL update.

Revision ID: 0156
Revises: 0155
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0156"
down_revision: str | None = "0155"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = sa.inspect(op.get_bind()).get_columns("repositories")
    if not any(column["name"] == "decision_vector_revision" for column in columns):
        op.add_column("repositories", sa.Column("decision_vector_revision", sa.String(32)))


def downgrade() -> None:
    op.drop_column("repositories", "decision_vector_revision")
