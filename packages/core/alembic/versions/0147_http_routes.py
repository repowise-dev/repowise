"""Route table: ``http_routes`` and ``repositories.http_routes_fingerprint``.

Every index stores the HTTP routes the repo registers (method, path template,
framework, registration site, handler symbol), not only with
``contracts.in_repo``. The fingerprint on the repository row names the read
that wrote them, so an update re-reads when it moves.

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
    columns = {c["name"] for c in inspector.get_columns("repositories")}
    if "http_routes_fingerprint" not in columns:
        op.add_column(
            "repositories", sa.Column("http_routes_fingerprint", sa.String(64), nullable=True)
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "http_routes_fingerprint" in {c["name"] for c in inspector.get_columns("repositories")}:
        with op.batch_alter_table("repositories") as batch:
            batch.drop_column("http_routes_fingerprint")
    if "http_routes" in set(inspector.get_table_names()):
        op.drop_table("http_routes")
