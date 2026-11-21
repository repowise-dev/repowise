"""Build units: ``stale`` marks units kept from a run whose reader failed.

``replace_build_units`` keeps a failed ecosystem's stored units; the flag lets
``load_build_units`` name those ecosystems in ``failed_ecosystems``, so test
selection never trusts their edges. Local SQLite stores get it from the models
through the reconciler.

Revision ID: 0138
Revises: 0137
Create Date: 2026-11-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0138"
down_revision: str | None = "0137"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("build_units")}


def upgrade() -> None:
    if "stale" not in _existing():
        op.add_column(
            "build_units",
            sa.Column("stale", sa.Boolean, nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if "stale" in _existing():
        with op.batch_alter_table("build_units") as batch:
            batch.drop_column("stale")
