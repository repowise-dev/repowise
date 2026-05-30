"""Add author_experience to git_commits.

The author's cumulative prior-commit count at the time of each commit — the one
change-risk feature not derivable from the diff alone (reconstructed in-memory
during the commit-index walk, no extra git pass). Persisted so the per-commit
risk breakdown can reproduce the stored change_risk_score exactly. Nullable;
back-populated on the next index.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "git_commits",
        sa.Column("author_experience", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("git_commits", "author_experience")
