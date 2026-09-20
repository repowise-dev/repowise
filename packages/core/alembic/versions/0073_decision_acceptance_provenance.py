"""decision_acceptances: record what signed, not only who.

``accepter`` is a free string resolved from ``git config user.name``, so a
machine signing read as a person. ``accepter_kind`` is the distinction;
``accepter_session`` is the transcript behind an agent that signed.

The empty server default is the "written before provenance" value and stays
legible as one; nothing is backfilled to ``person``. Local SQLite stores never
run Alembic -- ``init_db``'s reconciler issues additive DDL -- so this is for
managed Postgres, and the rule itself is enforced in
``crud.authority.record_acceptance``.

Revision ID: 0073
Revises: 0072
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_acceptances",
        sa.Column("accepter_kind", sa.String(length=16), nullable=False, server_default=""),
    )
    op.add_column(
        "decision_acceptances",
        sa.Column("accepter_session", sa.String(length=64), nullable=False, server_default=""),
    )
    # SQLite cannot ALTER a constraint on, and does not need to: a local store
    # is built from the model, which declares this CHECK at CREATE TABLE.
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "ck_acceptance_accepter_kind",
            "decision_acceptances",
            "accepter_kind IN ('', 'person', 'agent', 'import')",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("ck_acceptance_accepter_kind", "decision_acceptances", type_="check")
    op.drop_column("decision_acceptances", "accepter_session")
    op.drop_column("decision_acceptances", "accepter_kind")
