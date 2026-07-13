"""security_findings: full-history provenance + idempotent dedup.

Adds ``commit_sha`` / ``commit_at`` columns so a finding can be tied to the
commit that introduced it (full-history scans via ``repowise security scan
--history``), and a unique constraint over
``(repository_id, file_path, kind, line_number, commit_sha)`` so re-runs never
double-insert the same signal within the same commit. Working-tree findings
leave both columns NULL (the constraint keys on the empty-string default).

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "security_findings",
        sa.Column("commit_sha", sa.String(40), nullable=True, server_default=""),
    )
    op.add_column(
        "security_findings",
        sa.Column("commit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        "uq_security_finding_provenance",
        "security_findings",
        ["repository_id", "file_path", "kind", "line_number", "commit_sha"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_security_finding_provenance", "security_findings", type_="unique"
    )
    op.drop_column("security_findings", "commit_at")
    op.drop_column("security_findings", "commit_sha")
