"""decision_acceptances: record what signed, not only who.

``accepter`` is a free string resolved from ``git config user.name``, so an
agent accepting through the CLI stamped the maintainer's name and nothing
downstream could tell the two apart. ``accepter_kind`` is the distinction;
``accepter_session`` is the transcript behind an agent that signed.

Added nullable-false with an empty server default, which is the
"written before provenance existed" value and stays legible as one. Nothing
is backfilled to ``person``: that is the claim this column exists to stop a
machine making. Local SQLite stores never run Alembic -- ``init_db``'s
reconciler issues additive DDL -- so this is for managed Postgres, and the
CHECK the model declares is a backstop under
``crud.authority.record_acceptance``, which is where the rule is enforced.

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
    # SQLite cannot ALTER a constraint onto an existing table, and does not
    # need to: a local store is built by ``init_db`` from the model, which
    # declares this CHECK at CREATE TABLE. The rule itself lives in
    # ``record_acceptance`` either way.
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
