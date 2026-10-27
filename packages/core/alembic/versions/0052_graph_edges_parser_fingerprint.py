"""Let a parser change reach edges on files git considers unchanged.

``persist_incremental_edges`` rewrites only the git-changed files' rows, so a
tree-sitter query or extractor change reached a file only when that file next
happened to change — every other file kept the old build's edges indefinitely.
The one existing escape hatch (``_edges_predate_cohesion``) probes store content
for one specific past fix, which does not generalise.

``repositories.graph_edges_parser_fingerprint`` records the
``parser_fingerprint()`` of the build that last wrote the repo's edges. When it
differs from the running build's, the next update widens its reconcile to every
parsed file once and re-stamps, so this class of staleness cannot recur.

NULL on every store written before this, which reads as a mismatch and heals on
the next update.

``init_db``'s additive schema reconciler picks the column up from the model for
local SQLite stores that never run Alembic; this migration covers the managed
Postgres ones.

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("graph_edges_parser_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("repositories", "graph_edges_parser_fingerprint")
