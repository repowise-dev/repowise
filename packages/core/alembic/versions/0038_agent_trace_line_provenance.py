"""Line-level agent-trace provenance: model id + per-file AI line share.

Line-level extension of the agent-trace channel. Per-commit: the ``model_id``
(models.dev ``provider/model`` form) resolved from the agent-trace record that
attributed the commit — populated only for the ``agent_trace`` channel, NULL otherwise.
Per-file: the count of distinct file lines an AI/mixed contributor wrote
(interval-union deduped across all trace records for the path) plus a
``{model_id: line_count}`` breakdown, so "N% of this file is AI-written" and an
opus-vs-sonnet split can be derived downstream against the file's current LOC.

All columns are nullable / defaulted and back-populated on the next index. Data
comes from ``.agent-trace/traces.jsonl`` only — repos without the standard pay
nothing and keep NULL/zero throughout.

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("git_commits", sa.Column("agent_model_id", sa.String(64), nullable=True))

    op.add_column(
        "git_metadata",
        sa.Column("agent_line_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "git_metadata",
        sa.Column("agent_line_model_json", sa.Text, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("git_metadata", "agent_line_model_json")
    op.drop_column("git_metadata", "agent_line_count")
    op.drop_column("git_commits", "agent_model_id")
