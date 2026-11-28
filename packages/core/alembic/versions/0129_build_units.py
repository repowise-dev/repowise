"""Build units: ``build_units``, ``build_unit_deps`` and ``build_unit_files``.

New tables only. Local SQLite stores get the same tables from the models
through ``init_db``; neither path can change a key later, so the keys here
are final.

Revision ID: 0129
Revises: 0127
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0129"
down_revision: str | None = "0127"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NO_ROWID = {"sqlite_with_rowid": False}


def _repository_id() -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    # init_db may already have created them through create_all.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "build_units" not in existing:
        op.create_table(
            "build_units",
            _repository_id(),
            sa.Column("unit_id", sa.Text, primary_key=True),
            sa.Column("ecosystem", sa.String(16), nullable=False),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("root", sa.Text, nullable=False),
            sa.Column("manifest", sa.Text, nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            **_NO_ROWID,
        )
    if "build_unit_deps" not in existing:
        op.create_table(
            "build_unit_deps",
            _repository_id(),
            sa.Column("unit_id", sa.Text, primary_key=True),
            sa.Column("dep_unit_id", sa.Text, primary_key=True),
            **_NO_ROWID,
        )
    if "build_unit_files" not in existing:
        op.create_table(
            "build_unit_files",
            _repository_id(),
            sa.Column("path", sa.Text, primary_key=True),
            sa.Column("unit_id", sa.Text, primary_key=True),
            **_NO_ROWID,
        )
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("build_unit_files")}
    if "ix_build_unit_files_repo_unit" not in indexes:
        op.create_index(
            "ix_build_unit_files_repo_unit", "build_unit_files", ["repository_id", "unit_id"]
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in ("build_unit_files", "build_unit_deps", "build_units"):
        if name in existing:
            op.drop_table(name)
