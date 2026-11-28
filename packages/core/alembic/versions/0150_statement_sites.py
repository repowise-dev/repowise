"""Statement-site index: ``statement_sites``, ``statement_sites_stamp`` and ``evidence_statement_stats.issuer``.

Every index stores the places that issue database statements (table or
database function, verb, columns, filter columns, the function holding
them) so a production statement joins to its code
(``evidence.statement_join``). ``statement_sites_stamp`` names the read that
wrote them. ``evidence_statement_stats.issuer`` keeps what the join found
(``function``, ``ambiguous``, ``database_schedule``).

Local SQLite stores get all of it from the models through ``init_db`` and
the reconciler; this migration exists for managed Postgres.

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
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    # init_db may already have created them through create_all and the reconciler.
    if "statement_sites" not in tables:
        op.create_table(
            "statement_sites",
            _repository_id(),
            sa.Column("ordinal", sa.Integer, primary_key=True),
            sa.Column("file_path", sa.Text, nullable=False),
            sa.Column("line", sa.Integer, nullable=False),
            sa.Column("channel", sa.String(16), nullable=False),
            sa.Column("target", sa.String(16), nullable=False),
            sa.Column("name", sa.Text, nullable=True),
            sa.Column("schema_name", sa.Text, nullable=True),
            sa.Column("verb", sa.String(16), nullable=True),
            sa.Column("via", sa.String(16), nullable=False),
            sa.Column("symbol_id", sa.Text, nullable=True),
            sa.Column("columns_json", sa.Text, nullable=True),
            sa.Column("filters_json", sa.Text, nullable=True),
            sqlite_with_rowid=False,
        )
    if "statement_sites_stamp" not in tables:
        op.create_table(
            "statement_sites_stamp",
            _repository_id(),
            sa.Column("fingerprint", sa.String(64), nullable=True),
            sa.Column("inputs_json", sa.Text, nullable=True),
        )
    have = {c["name"] for c in inspector.get_columns("evidence_statement_stats")}
    if "issuer" not in have:
        op.add_column("evidence_statement_stats", sa.Column("issuer", sa.String(32), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for name in ("statement_sites_stamp", "statement_sites"):
        if name in tables:
            op.drop_table(name)
    have = {c["name"] for c in inspector.get_columns("evidence_statement_stats")}
    if "issuer" in have:
        with op.batch_alter_table("evidence_statement_stats") as batch:
            batch.drop_column("issuer")
