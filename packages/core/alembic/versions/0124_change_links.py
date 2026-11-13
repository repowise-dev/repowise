"""``change_commits`` and ``change_files``: a change's commits and paths as rows.

The delta and hotspot filters join a change to its commits' stored deltas and
to the hotspot flags in SQL, rather than binding every matching change id.
Existing changes are linked here from their stored JSON.

Revision ID: 0124
Revises: 0123
Create Date: 2026-11-12
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0124"
down_revision: str | None = "0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500


def _create(name: str, value: sa.Column, unique: str, index: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "repository_id",
            sa.String(32),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_id", sa.String(96), nullable=False),
        value,
        sa.UniqueConstraint("repository_id", "change_id", value.name, name=unique),
    )
    op.create_index(index, name, ["repository_id", value.name])


def _loads_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _backfill(bind: sa.engine.Connection) -> None:
    meta = sa.MetaData()
    changes = sa.Table("changes", meta, autoload_with=bind)
    commits = sa.Table("change_commits", meta, autoload_with=bind)
    files = sa.Table("change_files", meta, autoload_with=bind)
    rows = bind.execute(
        sa.select(
            changes.c.repository_id,
            changes.c.change_id,
            changes.c.commit_shas_json,
            changes.c.files_json,
        )
    ).all()
    for start in range(0, len(rows), _BATCH):
        commit_rows, file_rows = [], []
        for repository_id, change_id, shas_json, files_json in rows[start : start + _BATCH]:
            base = {"repository_id": repository_id, "change_id": change_id}
            for sha in dict.fromkeys(str(s) for s in _loads_list(shas_json)):
                commit_rows.append({"id": uuid.uuid4().hex, **base, "sha": sha})
            paths = (f.get("path") for f in _loads_list(files_json) if isinstance(f, dict))
            for path in dict.fromkeys(p for p in paths if p):
                file_rows.append({"id": uuid.uuid4().hex, **base, "file_path": path})
        if commit_rows:
            bind.execute(sa.insert(commits), commit_rows)
        if file_rows:
            bind.execute(sa.insert(files), file_rows)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    # init_db may already have created them through its additive reconciler.
    if "change_commits" not in existing:
        _create(
            "change_commits",
            sa.Column("sha", sa.String(40), nullable=False),
            "uq_change_commit",
            "ix_change_commits_repo_sha",
        )
    if "change_files" not in existing:
        _create(
            "change_files",
            sa.Column("file_path", sa.Text, nullable=False),
            "uq_change_file",
            "ix_change_files_repo_path",
        )
    if "changes" in existing:
        count = bind.execute(sa.text("SELECT count(*) FROM change_commits")).scalar_one()
        if not count:
            _backfill(bind)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in ("change_files", "change_commits"):
        if name in existing:
            op.drop_table(name)
