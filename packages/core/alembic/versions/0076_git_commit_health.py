"""``git_commit_health_deltas`` / ``git_commit_health_findings``: what each commit did to health.

The base-versus-head comparison needs both sides of every changed file and a
working tree, so a hosted read path could never run it. These tables hold the
answer computed at index time.

Two tables rather than one: without a per-commit status row, "no findings" and
"never scanned" are the same empty result, and the scan is deliberately bounded
so unscanned commits are normal rather than exceptional.

The three version columns pin each row to the analyzer that produced it. A
reader treats a mismatch as absent, so a bumped analyzer version invalidates
rows without a migration and the next index refills them.

New tables only — no existing table is touched, so a pre-0076 wiki.db upgrades
without rewriting anything and reads empty tables until the next index.

Revision ID: 0076
Revises: 0075
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repository_fk() -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "git_commit_health_deltas",
        sa.Column("id", sa.String(32), primary_key=True),
        _repository_fk(),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("analyzer_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rules_fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("performance_model_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("introduced_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("worsened_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("resolved_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_analyzed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("findings_stored", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository_id", "sha", name="uq_git_commit_health_delta"),
    )
    op.create_table(
        "git_commit_health_findings",
        sa.Column("id", sa.String(32), primary_key=True),
        _repository_fk(),
        sa.Column("sha", sa.String(40), nullable=False),
        sa.Column("change_finding_id", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("change_kind", sa.String(16), nullable=False),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("biomarker_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("severity_before", sa.String(16), nullable=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text, nullable=True),
        sa.Column("line_start", sa.Integer, nullable=True),
        sa.Column("line_end", sa.Integer, nullable=True),
        sa.Column("attribution_basis", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("health_impact", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "repository_id", "sha", "change_finding_id", name="uq_git_commit_health_finding"
        ),
    )
    op.create_index(
        "ix_git_commit_health_findings_repo_sha",
        "git_commit_health_findings",
        ["repository_id", "sha"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_git_commit_health_findings_repo_sha", table_name="git_commit_health_findings"
    )
    op.drop_table("git_commit_health_findings")
    op.drop_table("git_commit_health_deltas")
