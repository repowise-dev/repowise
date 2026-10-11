"""Index ``health_findings`` — the table every health read full-scanned.

``health_findings`` carried no index beyond its primary key, so both of the
shapes ``get_health`` issues degraded to a full table scan plus a temp B-tree
sort. On a 3.2k-file repo that is ~10k rows; the cost is linear in finding
count, so it grows with the repo it is meant to describe.

Two indexes for two distinct reads:

``ix_health_findings_repo_status_path`` serves the file-scoped lookup —
``get_health(targets=[...])``, the call an agent makes to self-check a file
before and after editing it. Measured on the repowise index, one file went
from 8.8ms (SCAN) to 0.1ms (SEARCH).

``ix_health_findings_repo_status_impact`` serves the repo-wide ranked read,
letting the top-N walk the index in impact order instead of sorting every row
into a temp B-tree first.

Both are declared in ``HealthFinding.__table_args__`` too. Most local stores
are created by ``init_db`` and never see Alembic, so the model declaration
covers new stores and this migration covers existing ones; the two converge on
the same shape.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers
revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_health_findings_repo_status_path", ["repository_id", "status", "file_path"]),
    ("ix_health_findings_repo_status_impact", ["repository_id", "status", "health_impact"]),
)


def upgrade() -> None:
    for name, columns in _INDEXES:
        op.create_index(name, "health_findings", columns, if_not_exists=True)


def downgrade() -> None:
    for name, _columns in _INDEXES:
        op.drop_index(name, table_name="health_findings", if_exists=True)
