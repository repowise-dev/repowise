"""Route table: ``http_routes`` and ``http_routes_stamp``.

Every index stores the HTTP routes the repo registers (method, path template,
framework, registration site, handler symbol), not only with
``contracts.in_repo``. ``http_routes_stamp`` names the read that wrote them,
so an update re-reads when it moves. New tables only: a column on
``repositories`` would break every ORM insert into a store at an older revision.

Local SQLite stores get both from the models through ``init_db``; this
migration exists for managed Postgres.

Revision ID: 0147
Revises: 0144
Create Date: 2026-11-26

Intended final chain: 0143 -> 0144 (inside and drivers) -> 0145 (function blame
history) -> 0146 (evidence kinds) -> 0147. Down revision is 0144 while this
branch stacks on 0144 alone; set it to 0146 when the chain is merged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0147"
down_revision: str | None = "0144"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # init_db may already have created them through create_all and the reconciler.
    if "http_routes" not in set(inspector.get_table_names()):
        op.create_table(
            "http_routes",
            sa.Column(
                "repository_id",
                sa.String(32),
                sa.ForeignKey("repositories.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("method", sa.String(16), primary_key=True),
            sa.Column("path", sa.Text, primary_key=True),
            sa.Column("file_path", sa.Text, primary_key=True),
            sa.Column("line", sa.Integer, primary_key=True),
            sa.Column("framework", sa.String(32), nullable=False),
            sa.Column("handler_symbol_id", sa.Text, nullable=True),
            sa.Column("handler", sa.Text, nullable=True),
            sa.Column("service", sa.Text, nullable=True),
            sqlite_with_rowid=False,
        )
    if "http_routes_stamp" not in set(inspector.get_table_names()):
        op.create_table(
            "http_routes_stamp",
            sa.Column(
                "repository_id",
                sa.String(32),
                sa.ForeignKey("repositories.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("fingerprint", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for name in ("http_routes_stamp", "http_routes"):
        if name in tables:
            op.drop_table(name)
