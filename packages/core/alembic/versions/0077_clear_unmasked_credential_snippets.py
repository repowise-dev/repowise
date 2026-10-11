"""Clear credential snippets stored before full-line masking.

Rows written earlier can hold a raw value: a long secret cut before it was
masked, a ``public_env_secret`` value that was never masked, or a vendor-shape
secret sharing a line with another finding. Updates do not
rescan and history re-runs keep existing rows, so nothing else would replace
them. No downgrade: it would restore the secrets.

Revision ID: 0077
Revises: 0076
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE security_findings SET snippet = '' "
            "WHERE kind IN ('hardcoded_password', 'hardcoded_secret', 'public_env_secret', "
            "'aws_access_key', 'github_token', 'slack_token', 'google_api_key', "
            "'stripe_key', 'private_key_pem')"
        )
    )


def downgrade() -> None:
    """No-op: restoring the snippets would re-expose the secrets."""
