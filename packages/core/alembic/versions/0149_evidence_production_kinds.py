"""Production evidence: route latency and statement statistics, and a run's window.

Two row tables for the production kinds (``evidence.kinds``):
``evidence_route_latency`` (one row per method and route template) and
``evidence_statement_stats`` (one row per normalized statement), keyed by
run like every evidence row table. Three nullable columns on
``evidence_runs``: ``window_start`` and ``window_end`` (what a production
run's counters cover) and ``base_commit_sha`` (the oldest commit deployed in
that window). Local SQLite stores get all of it from the models through the
reconciler.

Revision ID: 0149
Revises: 0148
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0149"
down_revision: str | None = "0148"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_COLUMNS = (
    ("window_start", sa.DateTime(timezone=True)),
    ("window_end", sa.DateTime(timezone=True)),
    ("base_commit_sha", sa.String(64)),
)
_NO_ROWID = {"sqlite_with_rowid": False}


def _run_id() -> sa.Column:
    return sa.Column(
        "run_id",
        sa.String(32),
        sa.ForeignKey("evidence_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )


def _repository_id() -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    have = {c["name"] for c in inspector.get_columns("evidence_runs")}
    for name, type_ in _RUN_COLUMNS:
        if name not in have:
            op.add_column("evidence_runs", sa.Column(name, type_, nullable=True))
    # init_db may have created the tables from the models before this ran.
    if "evidence_route_latency" not in tables:
        op.create_table(
            "evidence_route_latency",
            _run_id(),
            sa.Column("method", sa.String(16), primary_key=True),
            sa.Column("route", sa.Text, primary_key=True),
            _repository_id(),
            sa.Column("handler_symbol_id", sa.Text, nullable=True),
            sa.Column("lineage_id", sa.String(16), nullable=True),
            sa.Column("request_count", sa.BigInteger, nullable=False),
            sa.Column("p50_ms", sa.Float, nullable=True),
            sa.Column("p95_ms", sa.Float, nullable=True),
            sa.Column("total_ms", sa.Float, nullable=True),
            sa.Column("error_count", sa.BigInteger, nullable=True),
            **_NO_ROWID,
        )
    if "evidence_statement_stats" not in tables:
        op.create_table(
            "evidence_statement_stats",
            _run_id(),
            sa.Column("statement_fp", sa.String(32), primary_key=True),
            _repository_id(),
            sa.Column("query_text", sa.Text, nullable=True),
            sa.Column("symbol_id", sa.Text, nullable=True),
            sa.Column("lineage_id", sa.String(16), nullable=True),
            sa.Column("calls", sa.BigInteger, nullable=False),
            sa.Column("total_ms", sa.Float, nullable=True),
            sa.Column("mean_ms", sa.Float, nullable=True),
            sa.Column("row_count", sa.BigInteger, nullable=True),
            **_NO_ROWID,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for name in ("evidence_statement_stats", "evidence_route_latency"):
        if name in tables:
            op.drop_table(name)
    have = {c["name"] for c in inspector.get_columns("evidence_runs")}
    with op.batch_alter_table("evidence_runs") as batch:
        for name, _ in reversed(_RUN_COLUMNS):
            if name in have:
                batch.drop_column(name)
