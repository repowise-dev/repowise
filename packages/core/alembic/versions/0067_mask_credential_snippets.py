"""Clear plaintext secret values from existing credential findings.

Working-tree scans (``SecurityScanner.scan_file``) now mask the captured secret
value in the snippet before persisting.  Existing rows pre-dating this change
may contain the raw plaintext value.  This migration clears ``snippet`` to an
empty string for every row whose ``kind`` is a credential kind
(``hardcoded_password`` or ``hardcoded_secret``).  The next working-tree scan
will repopulate those rows with a masked snippet via ``replace_findings``.
History rows (``commit_sha`` non-empty) are also cleared: a future
``repowise security scan --history`` re-run will repopulate them masked too.

No data is truly lost: the snippet is a 120-character excerpt of the matched
line, and the line is still present in the repository.  The clearing is
irreversible in this migration intentionally — the downgrade path would
re-expose plaintext secrets, which is exactly the problem being fixed.

Revision ID: 0067
Revises: 0066
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE security_findings SET snippet = '' "
            "WHERE kind IN ('hardcoded_password', 'hardcoded_secret')"
        )
    )


def downgrade() -> None:
    """Intentionally a no-op.

    Re-exposing plaintext secret values in the snippet column in order to
    reverse this migration would defeat its purpose.  Downgrade is therefore
    not supported.
    """
