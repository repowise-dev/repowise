"""Doctor's store-format capability probe.

An un-upgraded store learns from ``doctor`` that a re-index is available, since
the once-per-store update nag may already have been shown and suppressed. The
row is always OK (a pre-upgrade store works degraded, it is not broken) and
carries the exact command.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import doctor_command
from repowise.core.upgrade import STORE_FORMAT_VERSION


def _repo_with_store(tmp_path: Path, store_format_version: int) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    repowise_dir = tmp_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    (repowise_dir / "state.json").write_text(
        json.dumps(
            {
                "store_format_version": store_format_version,
                "written_by_version": "0.21.0",
                "last_sync_commit": "abc1234",
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def _store_format_row(tmp_path: Path) -> dict:
    result = CliRunner().invoke(
        doctor_command, [str(tmp_path), "--no-workspace", "--format", "json"]
    )
    payload = json.loads(result.output[result.output.index("{") :])
    rows = [c for c in payload["checks"] if c["name"] == "Store format"]
    assert rows, "doctor did not emit a Store format row"
    return rows[0]


def test_doctor_flags_reindex_for_old_store(tmp_path: Path) -> None:
    row = _store_format_row(_repo_with_store(tmp_path, 1))
    # Reported OK (degraded, not broken) with an actionable command.
    assert row["ok"] is True
    assert "repowise init --force" in row["detail"]
    # Plain text only: no Rich markup leaking through the JSON surface.
    assert "[" not in row["detail"] or "]" not in row["detail"]


def test_doctor_reports_current_store_as_current(tmp_path: Path) -> None:
    row = _store_format_row(_repo_with_store(tmp_path, STORE_FORMAT_VERSION))
    assert row["ok"] is True
    assert row["detail"] == "Current"
