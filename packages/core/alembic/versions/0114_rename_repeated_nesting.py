"""Rename the stored biomarker id ``bumpy_road`` to ``repeated_nesting``.

Rewrites every column that stores a biomarker id. Public ids hash the marker's
first id, so ``public_id`` columns stay as they are. Local SQLite stores never
run Alembic; their rows are rewritten by the health re-score that follows an
analyzer version change.

Revision ID: 0114
Revises: 0113
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0114"
down_revision: str | None = "0113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "bumpy_road"
_NEW = "repeated_nesting"

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("health_findings", "biomarker_type"),
    ("git_commit_health_findings", "biomarker_type"),
    ("performance_opportunities", "biomarker_type"),
    ("refactoring_suggestions", "source_biomarker"),
    ("refactoring_opportunities", "lead_biomarker"),
)


def _rename(old: str, new: str) -> None:
    for table, column in _COLUMNS:
        op.execute(
            sa.text(f'UPDATE "{table}" SET "{column}" = :new WHERE "{column}" = :old').bindparams(
                old=old, new=new
            )
        )


def upgrade() -> None:
    _rename(_OLD, _NEW)


def downgrade() -> None:
    _rename(_NEW, _OLD)
