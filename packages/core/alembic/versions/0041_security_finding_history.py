"""security_findings: full-history provenance + idempotent dedup.

Adds ``commit_sha`` / ``commit_at`` columns so a finding can be tied to the
commit that introduced it (full-history scans via ``repowise security scan
--history``), and a unique constraint over
``(repository_id, file_path, kind, line_number, commit_sha)`` so re-runs never
double-insert the same signal within the same commit. Working-tree findings
store ``""`` for ``commit_sha`` (not NULL) so the constraint keys identically
across runs.

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("security_findings") as batch_op:
        batch_op.add_column(
            sa.Column("commit_sha", sa.String(40), nullable=True, server_default=""),
        )
        batch_op.add_column(
            sa.Column("commit_at", sa.DateTime(timezone=True), nullable=True),
        )
        batch_op.create_unique_constraint(
            "uq_security_finding_provenance",
            ["repository_id", "file_path", "kind", "line_number", "commit_sha"],
        )


def downgrade() -> None:
    with op.batch_alter_table("security_findings") as batch_op:
        batch_op.drop_constraint(
            "uq_security_finding_provenance", type_="unique"
        )
        batch_op.drop_column("commit_at")
        batch_op.drop_column("commit_sha")
