"""A corrupt ``state.json`` must not crash doctor, init or update.

``load_state`` has dozens of callers and none of them guards it, so a
truncated file used to escape as ``JSONDecodeError`` before any command could
report anything. The fix lives in ``load_state`` itself: an unreadable or
non-dict file reads as absent, which every caller already handles. The doctor
row still tells the cases apart, and both carry the fix command.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.cli._helpers.state import load_state, state_file_error
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.core import _state_row


def _repo(tmp_path: Path, content: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    if content is not None:
        (repo / ".repowise" / "state.json").write_text(content, encoding="utf-8")
    return repo


def _row(repo: Path):
    return _state_row(CheckContext(repo_path=repo, repowise_dir=repo / ".repowise"))


def test_load_state_treats_corrupt_file_as_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "{not json")
    assert load_state(repo) == {}
    assert state_file_error(repo) is not None


def test_load_state_treats_non_dict_file_as_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "[1, 2, 3]")
    assert load_state(repo) == {}
    assert "JSON object" in (state_file_error(repo) or "")


def test_load_state_keeps_valid_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path, json.dumps({"last_sync_commit": "abcdef01"}))
    assert load_state(repo) == {"last_sync_commit": "abcdef01"}
    assert state_file_error(repo) is None


def test_doctor_row_names_unreadable_file_and_fix(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "{not json")
    row = _row(repo)
    assert row.ok is False
    assert row.detail.startswith("unreadable: ")
    assert "state.json" in row.detail
    assert row.fix == "repowise init"


def test_doctor_row_names_missing_file_and_fix(tmp_path: Path) -> None:
    repo = _repo(tmp_path, None)
    row = _row(repo)
    assert row.ok is False
    assert row.detail == "missing"
    assert row.fix == "repowise init"


def test_doctor_row_keeps_valid_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path, json.dumps({"last_sync_commit": "abcdef0123456789"}))
    row = _row(repo)
    assert row.ok is True
    assert row.detail == "last_sync: abcdef01"
    assert row.fix is None
