"""Which measured test files differ from the measured run."""

from __future__ import annotations

import subprocess

from repowise.core.analysis.test_impact import _stale_measured_tests


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("test_kept.py", "test_edited.py", "test_gone.py"):
        (tests / name).write_text("def test_it(): pass\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "measured")
    return _git(tmp_path, "rev-parse", "HEAD")


def _rows(*files):
    return {"src/a.py": [{"test_id": f"{f}::test_it", "test_file": f} for f in files]}


async def test_a_gone_and_an_edited_test_file_are_flagged(tmp_path):
    measured_at = _repo(tmp_path)
    (tmp_path / "tests" / "test_edited.py").write_text("def test_it(): assert 1\n", "utf-8")
    _git(tmp_path, "commit", "-qam", "edit")
    (tmp_path / "tests" / "test_gone.py").unlink()

    stale = await _stale_measured_tests(
        _rows("tests/test_kept.py", "tests/test_edited.py", "tests/test_gone.py"),
        measured_at,
        str(tmp_path),
    )

    assert stale == {
        "tests/test_edited.py": "test_file_changed",
        "tests/test_gone.py": "test_file_missing",
    }


async def test_an_uncommitted_edit_counts_and_a_file_absent_from_git_does_not(tmp_path):
    head = _repo(tmp_path)
    (tmp_path / "tests" / "test_edited.py").write_text("changed\n", "utf-8")

    stale = await _stale_measured_tests(
        _rows("tests/test_kept.py", "tests/test_edited.py", "other/test_never.py"),
        head,
        str(tmp_path),
    )

    # A path git never knew is no proof the run's file is gone (another checkout).
    assert stale == {"tests/test_edited.py": "test_file_changed"}


async def test_without_a_checkout_nothing_is_flagged():
    assert await _stale_measured_tests(_rows("tests/test_x.py"), "a", None) == {}

