"""``core.fsutils.atomic_write_text`` in a directory that refuses new files."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repowise.core import fsutils
from repowise.core.fsutils import atomic_write_text, make_temp_file


def test_a_directory_that_refuses_new_files_raises_at_once(monkeypatch, tmp_path: Path) -> None:
    # A sandbox ACL that denies creating files: Windows answers PermissionError while
    # os.access still calls the directory writable, and tempfile.mkstemp then retried
    # about 2**31 names, so `init` hung writing ~/.claude.json.
    calls: list[str] = []

    def _refuse(path: str, *_args: object) -> int:
        calls.append(path)
        raise PermissionError(13, "Access is denied", path)

    monkeypatch.setattr(fsutils.os, "open", _refuse)
    monkeypatch.setattr(fsutils.os, "access", lambda *_a, **_k: True)

    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "config.json", "{}")
    assert len(calls) == 1


def test_a_taken_name_is_retried(monkeypatch, tmp_path: Path) -> None:
    real_open = os.open
    calls: list[str] = []

    def _first_taken(path: str, *args: object) -> int:
        calls.append(path)
        if len(calls) == 1:
            raise FileExistsError(17, "File exists", path)
        return real_open(path, *args)

    monkeypatch.setattr(fsutils.os, "open", _first_taken)

    fd, path = make_temp_file(tmp_path, prefix=".x-", suffix=".json")
    os.close(fd)
    assert len(calls) == 2
    assert Path(path).parent == tmp_path
    assert Path(path).name.startswith(".x-") and path.endswith(".json")


def test_atomic_write_replaces_the_file_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_temp_file_is_private_like_mkstemp(tmp_path: Path) -> None:
    fd, path = make_temp_file(tmp_path)
    os.close(fd)

    assert os.stat(path).st_mode & 0o777 == 0o600


def test_a_failed_write_leaves_no_temp_and_keeps_the_old_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")

    def _refuse_replace(self: Path, _dst: Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "replace", _refuse_replace)

    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]
