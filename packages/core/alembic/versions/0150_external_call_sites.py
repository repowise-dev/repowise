"""Package calls: ``external_call_sites`` and ``external_call_sites_stamp``.

Every call whose callee an import binds to a third-party package (``from x
import y; y()``, ``import x; x.y()``, ``import {y} from 'x'``, ``require``) is
kept, with its caller, line and the imported binding, beside ``graph_edges``
rather than in it, so no graph metric moves. The stamp names the version the
rows hold. New tables only.

Local SQLite stores get both from the models through ``init_db``; this
migration exists for managed Postgres.

Revision ID: 0150
Revises: 0149
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0150"
down_revision: str | None = "0149"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repository_id() -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    # init_db may already have created them through create_all and the reconciler.
    if "external_call_sites" not in tables:
        op.create_table(
            "external_call_sites",
            _repository_id(),
            sa.Column("file_path", sa.Text, primary_key=True),
            sa.Column("line", sa.Integer, primary_key=True),
            sa.Column("caller_symbol_id", sa.Text, primary_key=True),
            sa.Column("local_name", sa.Text, primary_key=True),
            sa.Column("symbol_path", sa.Text, primary_key=True),
            sa.Column("ecosystem", sa.String(16), nullable=False),
            sa.Column("module", sa.Text, nullable=False),
            sa.Column("package", sa.Text, nullable=False),
            sa.Column("external_node", sa.Text, nullable=False),
            sa.Column("binding", sa.String(16), nullable=False),
            sqlite_with_rowid=False,
        )
        op.create_index(
            "ix_external_call_sites_repo_package",
            "external_call_sites",
            ["repository_id", "package"],
        )
    if "external_call_sites_stamp" not in tables:
        op.create_table(
            "external_call_sites_stamp",
            _repository_id(),
            sa.Column("version", sa.Integer, nullable=True),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for name in ("external_call_sites_stamp", "external_call_sites"):
        if name in tables:
            op.drop_table(name)
