"""Where a first-look report lives: a file in the checkout and a row in the index.

The file (``.repowise/reports/first-look.json``) is what the CLI opens and an
agent reads. The ``reports`` row is what the server and a served snapshot
read, so both read one place. A report written at a new commit keeps the old
file as ``first-look.<commit>.json`` and adds a row beside the old one.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from repowise.core.git_sha import is_sha_prefix

from .model import FirstLookReport

KIND = "first_look"
REPORTS_DIR = Path(".repowise") / "reports"
FILE_NAME = "first-look.json"


def report_path(repo_root: Path) -> Path:
    return Path(repo_root) / REPORTS_DIR / FILE_NAME


def _dump(report: FirstLookReport) -> str:
    return json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n"


def read_report_file(repo_root: Path) -> FirstLookReport | None:
    """The stored report, or ``None`` when there is none or it cannot be read."""
    path = report_path(repo_root)
    try:
        return FirstLookReport.from_json(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_report_file(repo_root: Path, report: FirstLookReport) -> Path:
    """Write *report*; one stamped at another commit is kept as ``first-look.<commit>.json``."""
    path = report_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The old report is archived as a copy and *path* is only ever replaced in
    # one step, so a failure at any point leaves a readable report there.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(_dump(report), encoding="utf-8", newline="\n")
    old = read_report_file(repo_root)
    if (
        old is not None
        and is_sha_prefix(old.stamp.commit)
        and old.stamp.commit != report.stamp.commit
    ):
        shutil.copy2(path, path.with_name(f"first-look.{old.stamp.commit}.json"))
    elif old is None and path.exists():
        # Unreadable here (damaged, or a newer Repowise's schema): the latest
        # unreadable copy is kept beside it; an earlier one there is replaced.
        shutil.copy2(path, path.with_name("first-look.previous.json"))
    os.replace(tmp, path)
    return path


async def store_report(session: Any, repository_id: str, report: FirstLookReport) -> None:
    """Upsert *report*'s row in ``reports`` (one per commit). The caller commits."""
    from datetime import UTC, datetime

    from repowise.core.persistence.models import StoredReport

    row = StoredReport(
        repository_id=repository_id,
        kind=KIND,
        commit_sha=report.stamp.commit or "",
        payload_json=_dump(report),
        created_at=datetime.fromisoformat(report.stamp.created_at)
        if report.stamp.created_at
        else datetime.now(UTC),
    )
    await session.merge(row)
    await session.flush()


async def load_report(
    session: Any, repository_id: str, *, commit: str | None = None
) -> FirstLookReport | None:
    """The newest stored report, or the one written at *commit*."""
    from sqlalchemy import select

    from repowise.core.persistence.models import StoredReport

    q = select(StoredReport.payload_json).where(
        StoredReport.repository_id == repository_id, StoredReport.kind == KIND
    )
    if commit is not None:
        q = q.where(StoredReport.commit_sha == commit)
    raw = (await session.execute(q.order_by(StoredReport.created_at.desc()).limit(1))).scalar()
    if raw is None:
        return None
    return FirstLookReport.from_json(json.loads(raw))


__all__ = [
    "FILE_NAME",
    "KIND",
    "REPORTS_DIR",
    "load_report",
    "read_report_file",
    "report_path",
    "store_report",
    "write_report_file",
]
