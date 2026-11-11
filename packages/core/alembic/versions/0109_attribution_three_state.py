"""Three-state commit attribution and the per-file known-source count.

``git_commits.attribution`` is ``agent``, ``human_attested`` or ``unknown``; a
commit without a marker used to read as human. ``git_metadata`` gains
``attribution_known_count`` and ``agent_authored_pct`` becomes the agent share
of those known commits. Existing rows are rewritten to the same meaning: before
this revision no source could attest a human, so every agent commit was the
whole known set.

Local SQLite stores never run Alembic; ``init_db`` adds the columns there and
readers derive the label for NULL rows until the next index.

Revision ID: 0109
Revises: 0108
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("git_commits", sa.Column("attribution", sa.String(16), nullable=True))
    op.add_column(
        "git_metadata",
        sa.Column("attribution_known_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE git_commits SET attribution = "
        "CASE WHEN agent_name IS NOT NULL THEN 'agent' ELSE 'unknown' END"
    )
    op.execute(
        "UPDATE git_metadata SET attribution_known_count = agent_commit_count, "
        "agent_authored_pct = CASE WHEN agent_commit_count > 0 THEN 1.0 ELSE NULL END"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE git_metadata SET agent_authored_pct = "
        "CASE WHEN commit_count_total > 0 "
        "THEN CAST(agent_commit_count AS FLOAT) / commit_count_total ELSE NULL END"
    )
    op.drop_column("git_metadata", "attribution_known_count")
    op.drop_column("git_commits", "attribution")
