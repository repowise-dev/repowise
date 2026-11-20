"""A stored report document per kind and commit (the repo report), and the
reason a person gave for an answer on the stores the report marks.

``reports`` holds one document per kind and commit. A nullable note column on
``action_states`` (``note``), ``health_findings`` and ``refactoring_suggestions``
(``status_note``) keeps the reason behind an acknowledged item or a false
positive; ``dead_code_findings.note`` already does. Local SQLite stores get the
columns from the models through the reconciler.

Revision ID: 0139
Revises: 0138
Create Date: 2026-11-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0139"
down_revision: str | None = "0138"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "reports"
#: ``(table, column)`` for each answer note this revision adds.
_NOTES = (
    ("action_states", "note"),
    ("health_findings", "status_note"),
    ("refactoring_suggestions", "status_note"),
)


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, column in _NOTES:
        if column not in _columns(table):
            op.add_column(table, sa.Column(column, sa.Text, nullable=True))
    # init_db may already have created it through create_all.
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("commit_sha", sa.String(64), primary_key=True),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    if _TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(_TABLE)
    for table, column in reversed(_NOTES):
        if column in _columns(table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
