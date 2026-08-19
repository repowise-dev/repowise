"""get_health attaches per-file coverage decay in targeted mode."""

from __future__ import annotations

import json
import subprocess
import types
from datetime import UTC, datetime

import pytest

from repowise.server.mcp_server.tool_health import (
    _attach_coverage_decay,
    _serialize_coverage_row,
)


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mod.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _row(path: str, covered: list[int], *, sha: str | None, pct: float = 100.0):
    return types.SimpleNamespace(
        file_path=path,
        source_format="lcov",
        line_coverage_pct=pct,
        branch_coverage_pct=None,
        covered_lines_json=json.dumps(covered),
        total_coverable_lines=len(covered),
        ingested_at=datetime.now(UTC),
        ingested_commit_sha=sha,
    )


def test_decay_marks_the_lines_that_moved_since_the_report(repo) -> None:
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text("a = 1\nb = 22\nc = 3\nd = 4\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit")

    rows = [_row("mod.py", [1, 2, 3], sha=base)]
    payload = [_serialize_coverage_row(r) for r in rows]
    _attach_coverage_decay(payload, rows, str(repo))

    decay = payload[0]["decay"]
    assert decay["measured_lines"] == 3
    assert decay["invalidated_lines"] == 1
    assert decay["confirmed_lines"] == 2
    assert decay["measured_at_commit"] == base[:12]


def test_the_stored_percentage_is_never_rewritten(repo) -> None:
    """Decay annotates the measurement; it must not restate it.

    The report said what it said. Re-deriving a percentage from a partial
    invalidation would invent a figure nobody can check, which is the whole
    reason this is a separate block.
    """
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text("a = 1\nb = 22\nc = 33\nd = 4\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit")

    rows = [_row("mod.py", [1, 2, 3], sha=base, pct=75.0)]
    payload = [_serialize_coverage_row(r) for r in rows]
    _attach_coverage_decay(payload, rows, str(repo))

    assert payload[0]["line_coverage_pct"] == 75.0
    assert payload[0]["decay"]["invalidated_lines"] == 2


def test_no_decay_block_when_the_measurement_cannot_be_placed(repo) -> None:
    """Absent, not zero. A zero drift block would read as a freshness claim."""
    rows = [_row("mod.py", [1, 2, 3], sha=None)]
    rows[0].ingested_at = None
    payload = [_serialize_coverage_row(r) for r in rows]
    _attach_coverage_decay(payload, rows, str(repo))

    assert "decay" not in payload[0]


def test_a_row_with_no_covered_lines_gets_no_decay_block(repo) -> None:
    head = _git(repo, "rev-parse", "HEAD").strip()
    rows = [_row("mod.py", [], sha=head)]
    payload = [_serialize_coverage_row(r) for r in rows]
    _attach_coverage_decay(payload, rows, str(repo))

    assert "decay" not in payload[0]


def test_untouched_file_reports_a_confirmed_measurement(repo) -> None:
    head = _git(repo, "rev-parse", "HEAD").strip()
    rows = [_row("mod.py", [1, 2, 3], sha=head)]
    payload = [_serialize_coverage_row(r) for r in rows]
    _attach_coverage_decay(payload, rows, str(repo))

    assert payload[0]["decay"]["invalidated_lines"] == 0
    assert payload[0]["decay"]["stale"] is False


def test_empty_row_list_is_a_no_op(repo) -> None:
    payload: list[dict] = []
    _attach_coverage_decay(payload, [], str(repo))
    assert payload == []
