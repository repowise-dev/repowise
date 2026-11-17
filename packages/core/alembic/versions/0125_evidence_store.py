"""Runtime evidence store: ``evidence_*`` tables and ``symbol_hotness``.

New tables only, so an older wiki.db upgrades without rewriting anything. An
empty store means no runtime evidence, which every reader shows as unknown.
Local SQLite stores get the same tables from the models through ``init_db``;
neither path can change a key later, so the keys here are final.

Revision ID: 0125
Revises: 0124
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0125"
down_revision: str | None = "0124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NO_ROWID = {"sqlite_with_rowid": False}


def _repository_id(*, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        "repository_id",
        sa.String(32),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
    )


def _run_id() -> sa.Column:
    return sa.Column(
        "run_id",
        sa.String(32),
        sa.ForeignKey("evidence_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )


def _create(existing: set[str], name: str, *items: object, **kwargs: object) -> None:
    # init_db may already have created it through create_all.
    if name not in existing:
        op.create_table(name, *items, **kwargs)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    _create(
        existing,
        "evidence_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        _repository_id(),
        sa.Column("run_key", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("workload_name", sa.Text, nullable=False),
        sa.Column("workload_class", sa.String(16), nullable=False),
        sa.Column("value_kind", sa.String(16), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("environment", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("tool_version", sa.String(64), nullable=True),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("dirty", sa.Boolean, nullable=True),
        sa.Column("checkout_root", sa.Text, nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("units_total", sa.Integer, nullable=True),
        sa.Column("units_matched", sa.Integer, nullable=True),
        sa.Column("units_skipped", sa.Integer, nullable=True),
        sa.Column("units_outside_repo", sa.Integer, nullable=True),
        sa.Column("units_truncated", sa.Integer, nullable=True),
        sa.Column("skipped_json", sa.Text, nullable=True),
        sa.Column("unmatched_sample_json", sa.Text, nullable=True),
        sa.Column("total_value", sa.Float, nullable=True),
        sa.Column("explained_value", sa.Float, nullable=True),
        sa.Column("mapping_partial", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    _create_index("ix_evidence_runs_repo_ingested", "evidence_runs", ["repository_id", "ingested_at"])
    _create_index(
        "ix_evidence_runs_repo_key", "evidence_runs", ["repository_id", "run_key", "commit_sha"]
    )
    _create(
        existing,
        "evidence_symbol_costs",
        _run_id(),
        sa.Column("symbol_id", sa.Text, primary_key=True),
        _repository_id(),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("line", sa.Integer, nullable=True),
        sa.Column("self_value", sa.Float, nullable=True),
        sa.Column("total_value", sa.Float, nullable=True),
        sa.Column("self_samples", sa.Integer, nullable=True),
        sa.Column("total_samples", sa.Integer, nullable=True),
        sa.Column("call_count", sa.Integer, nullable=True),
        sa.Column("resolution", sa.String(16), nullable=True),
        **_NO_ROWID,
    )
    _create(
        existing,
        "evidence_benchmark_results",
        _run_id(),
        sa.Column("benchmark_key", sa.Text, primary_key=True),
        _repository_id(),
        sa.Column("symbol_id", sa.Text, nullable=True),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("lower_is_better", sa.Boolean, nullable=True),
        sa.Column("median", sa.Float, nullable=True),
        sa.Column("mean", sa.Float, nullable=True),
        sa.Column("min", sa.Float, nullable=True),
        sa.Column("max", sa.Float, nullable=True),
        sa.Column("stddev", sa.Float, nullable=True),
        sa.Column("rounds", sa.Integer, nullable=True),
        sa.Column("iterations", sa.Integer, nullable=True),
        sa.Column("allocs_per_op", sa.Float, nullable=True),
        sa.Column("bytes_per_op", sa.Float, nullable=True),
        **_NO_ROWID,
    )
    _create_index(
        "ix_evidence_benchmark_results_repo_key",
        "evidence_benchmark_results",
        ["repository_id", "benchmark_key"],
    )
    _create(
        existing,
        "evidence_test_timings",
        _run_id(),
        sa.Column("test_id", sa.Text, primary_key=True),
        _repository_id(),
        sa.Column("test_file", sa.Text, nullable=True),
        sa.Column("symbol_id", sa.Text, nullable=True),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        **_NO_ROWID,
    )
    _create(
        existing,
        "evidence_test_stats",
        _repository_id(primary_key=True),
        sa.Column("environment", sa.String(32), primary_key=True),
        sa.Column("test_id", sa.Text, primary_key=True),
        sa.Column("symbol_id", sa.Text, nullable=True),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("durations_json", sa.Text, nullable=False),
        sa.Column("median_s", sa.Float, nullable=True),
        sa.Column("last_duration_s", sa.Float, nullable=True),
        sa.Column("last_commit_sha", sa.String(40), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        **_NO_ROWID,
    )
    _create(
        existing,
        "evidence_query_counts",
        _run_id(),
        sa.Column("scope_kind", sa.String(16), primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("site_symbol_id", sa.Text, primary_key=True),
        sa.Column("statement_fp", sa.String(32), primary_key=True),
        _repository_id(),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("site_line", sa.Integer, nullable=True),
        sa.Column("query_count", sa.Integer, nullable=False),
        sa.Column("identical_max", sa.Integer, nullable=True),
        sa.Column("total_ms", sa.Float, nullable=True),
        **_NO_ROWID,
    )
    _create(
        existing,
        "symbol_hotness",
        _repository_id(primary_key=True),
        sa.Column("symbol_id", sa.Text, primary_key=True),
        sa.Column("lineage_id", sa.String(16), nullable=True),
        sa.Column("tier", sa.String(16), nullable=True),
        sa.Column("total_share", sa.Float, nullable=True),
        sa.Column("self_share", sa.Float, nullable=True),
        sa.Column("workload_name", sa.Text, nullable=True),
        sa.Column("workload_class", sa.String(16), nullable=True),
        sa.Column("run_count", sa.Integer, nullable=True),
        sa.Column("evidence_commit_sha", sa.String(40), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        **_NO_ROWID,
    )


def _create_index(name: str, table: str, columns: list[str]) -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


#: Children before the runs they reference.
_TABLES = (
    "symbol_hotness",
    "evidence_query_counts",
    "evidence_test_stats",
    "evidence_test_timings",
    "evidence_benchmark_results",
    "evidence_symbol_costs",
    "evidence_runs",
)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in _TABLES:
        if name in existing:
            op.drop_table(name)
