"""`doctor` names the source files ingestion dropped on size (#5415).

An excluded file leaves no index row, so nothing else in doctor can see it: the
repo reported healthy while its largest classes were absent from every search
and every answer. This row is the only place the omission is visible.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd._checks.core import _oversize_source_rows
from repowise.core.ingestion.traverser import _SOURCE_MAX_FILE_SIZE_BYTES


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _commit(repo: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


def test_a_source_over_the_ceiling_is_named(repo: Path):
    # Just over the source ceiling, in a file the language registry parses.
    pad = "# padding\n"
    (repo / "huge.py").write_text("x = 1\n" + pad * (_SOURCE_MAX_FILE_SIZE_BYTES // len(pad) + 10))
    (repo / "small.py").write_text("y = 2\n")
    _commit(repo)

    rows = _oversize_source_rows(repo)

    assert len(rows) == 1
    row = rows[0]
    assert row.id == "oversize-sources"
    # A warning, not a failure: dropping a huge generated file is legitimate.
    assert row.level == "warn"
    assert "huge.py" in row.detail
    assert "small.py" not in row.detail


def test_a_repo_within_the_ceiling_gets_no_row(repo: Path):
    (repo / "fine.py").write_text("y = 2\n")
    _commit(repo)
    assert _oversize_source_rows(repo) == []


def test_listing_stops_at_five_and_counts_the_rest(repo: Path):
    pad = "# padding\n"
    body = "x = 1\n" + pad * (_SOURCE_MAX_FILE_SIZE_BYTES // len(pad) + 10)
    for i in range(7):
        (repo / f"huge{i}.py").write_text(body)
    _commit(repo)

    rows = _oversize_source_rows(repo)

    assert len(rows) == 1
    assert "7 source file(s)" in rows[0].detail
    assert "and 2 more" in rows[0].detail
