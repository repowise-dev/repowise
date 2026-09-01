"""Separate decision candidates from accepted decisions.

Before this, a record reached ``active`` by being seen in two coding-agent
sessions, and an agent read it as a standing instruction. Recurrence is evidence
that a candidate is worth reviewing, not an acceptance event, so authority moves
out of the status column and into a row somebody has to write.

``decision_acceptances`` is that row, and it is append-only: reaffirming,
superseding and dismissing add to the log rather than editing it, so the history
of who granted authority survives every later action. Its CHECK constraints are
the acceptance contract — a reason, a scope, an evidence reference, and an
accepter or a tracked-artifact identity — enforced by the database rather than by
whichever caller is writing. A record with no acceptance row is a candidate, and
because a governance read is a join onto this table, a candidate cannot be
reached through one.

``decision_candidate_meta`` holds what review needs and a decision does not, so
the two entities do not share a column set. ``decision_aliases`` keeps ids that
merge and supersede retire from failing to resolve.

Legacy rows are classified by ``repowise decision migrate``, not here: a data
repair that lives in a migration and one that lives in the code eventually
disagree, and only one of them runs on an existing store.

Local SQLite stores get these tables from the models via ``init_db``'s schema
reconciler; this migration covers the managed Postgres ones.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_acceptances",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(length=32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("action", sa.String(length=24), nullable=False, server_default="accepted"),
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("accepter", sa.Text(), nullable=False, server_default=""),
        sa.Column("artifact", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("decision_id", "seq", name="uq_decision_acceptance_seq"),
        sa.CheckConstraint("reason <> ''", name="ck_acceptance_reason"),
        sa.CheckConstraint("scope_json NOT IN ('', '[]')", name="ck_acceptance_scope"),
        sa.CheckConstraint("evidence_json NOT IN ('', '[]')", name="ck_acceptance_evidence"),
        sa.CheckConstraint("accepter <> '' OR artifact <> ''", name="ck_acceptance_identity"),
        sa.CheckConstraint(
            "currency IN ('active', 'needs_review', 'uncheckable', 'superseded', 'dismissed')",
            name="ck_acceptance_currency",
        ),
    )
    op.create_index(
        "ix_decision_acceptances_repository_id", "decision_acceptances", ["repository_id"]
    )
    op.create_index(
        "ix_decision_acceptances_decision_id", "decision_acceptances", ["decision_id"]
    )

    op.create_table(
        "decision_candidate_meta",
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "repository_id",
            sa.String(length=32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("review_state", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("review_priority", sa.Float(), nullable=False, server_default="0"),
        sa.Column("grounding_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("extractor_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("lane", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("needs_split", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope_unresolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("merged_into", sa.String(length=32), nullable=True),
        sa.Column("dismissed_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "review_state IN ('open', 'accepted', 'merged', 'needs_split', 'dismissed')",
            name="ck_candidate_review_state",
        ),
    )
    op.create_index(
        "ix_decision_candidate_meta_repository_id",
        "decision_candidate_meta",
        ["repository_id"],
    )
    op.create_index(
        "ix_candidate_meta_repo_state",
        "decision_candidate_meta",
        ["repository_id", "review_state"],
    )

    op.create_table(
        "decision_aliases",
        sa.Column("alias_id", sa.String(length=32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(length=32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("decision_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="merged"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_aliases_repository_id", "decision_aliases", ["repository_id"])
    op.create_index("ix_decision_aliases_decision_id", "decision_aliases", ["decision_id"])


def downgrade() -> None:
    op.drop_table("decision_aliases")
    op.drop_table("decision_candidate_meta")
    op.drop_table("decision_acceptances")
