"""Unit tests for the unified cat_file_batch reader."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.git_cat_file import cat_file_batch


def _run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        check=True,
        text=True,
    )
    return proc.stdout.strip()


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a test repository containing files, directories, and commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")

    # Create root files and a subdirectory with a file
    (repo / "file1.txt").write_text("hello from file1\n")
    (repo / "file with spaces.txt").write_text("hello spaces\n")
    sub = repo / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("hello nested\n")

    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "Initial commit")
    return repo


def test_cat_file_batch_empty_specs(sample_repo: Path) -> None:
    res = cat_file_batch(str(sample_repo), [])
    assert res == []


def test_cat_file_batch_reads_blobs_and_preserves_order(sample_repo: Path) -> None:
    specs = [
        "HEAD:file1.txt",
        "HEAD:subdir/nested.txt",
        "HEAD:file with spaces.txt",
    ]
    res = cat_file_batch(str(sample_repo), specs)
    assert res is not None
    assert len(res) == 3

    assert res[0] == ("HEAD:file1.txt", b"hello from file1\n")
    assert res[1] == ("HEAD:subdir/nested.txt", b"hello nested\n")
    assert res[2] == ("HEAD:file with spaces.txt", b"hello spaces\n")


def test_cat_file_batch_handles_directory_tree_without_stream_desync(sample_repo: Path) -> None:
    """When a spec resolves to a tree object (e.g. HEAD:subdir), its body is consumed

    and subsequent items in the stream must remain perfectly aligned.
    """
    specs = [
        "HEAD:file1.txt",
        "HEAD:subdir",  # directory -> tree object
        "HEAD:file with spaces.txt",
    ]
    res = cat_file_batch(str(sample_repo), specs)
    assert res is not None
    assert len(res) == 3

    assert res[0] == ("HEAD:file1.txt", b"hello from file1\n")
    assert res[1] == ("HEAD:subdir", None)
    assert res[2] == ("HEAD:file with spaces.txt", b"hello spaces\n")


def test_cat_file_batch_on_missing_omit(sample_repo: Path) -> None:
    specs = [
        "HEAD:file1.txt",
        "HEAD:non_existent.txt",
        "HEAD:subdir/nested.txt",
    ]
    res = cat_file_batch(str(sample_repo), specs, on_missing="omit")
    assert res is not None
    assert len(res) == 3
    assert res[0] == ("HEAD:file1.txt", b"hello from file1\n")
    assert res[1] == ("HEAD:non_existent.txt", None)
    assert res[2] == ("HEAD:subdir/nested.txt", b"hello nested\n")


def test_cat_file_batch_on_missing_raise(sample_repo: Path) -> None:
    specs = [
        "HEAD:file1.txt",
        "HEAD:non_existent.txt",
    ]
    with pytest.raises(ValueError, match=r"git has no object for 'HEAD:non_existent\.txt'"):
        cat_file_batch(str(sample_repo), specs, on_missing="raise")


def test_cat_file_batch_submodule_gitlink(sample_repo: Path, tmp_path: Path) -> None:
    """Submodule whose commit is missing or present in repo is parsed as None."""
    # Create another repo to be added as submodule
    sub_repo = tmp_path / "sub_repo"
    sub_repo.mkdir()
    _run_git(sub_repo, "init", "-b", "main")
    _run_git(sub_repo, "config", "user.name", "Test User")
    _run_git(sub_repo, "config", "user.email", "test@example.com")
    (sub_repo / "subfile.txt").write_text("sub\n")
    _run_git(sub_repo, "add", ".")
    _run_git(sub_repo, "commit", "-m", "Sub init")

    # Add submodule to sample_repo
    _run_git(
        sample_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(sub_repo),
        "sub_mod",
    )
    _run_git(sample_repo, "commit", "-m", "Add submodule")

    specs = [
        "HEAD:file1.txt",
        "HEAD:sub_mod",
        "HEAD:subdir/nested.txt",
    ]
    res = cat_file_batch(str(sample_repo), specs)
    assert res is not None
    assert len(res) == 3
    assert res[0] == ("HEAD:file1.txt", b"hello from file1\n")
    assert res[1] == ("HEAD:sub_mod", None)
    assert res[2] == ("HEAD:subdir/nested.txt", b"hello nested\n")


def test_cat_file_batch_check_flag(sample_repo: Path, tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()

    # check=False returns None on git failure
    res = cat_file_batch(str(not_a_repo), ["HEAD:file1.txt"], check=False)
    assert res is None

    # check=True raises CalledProcessError
    with pytest.raises(subprocess.CalledProcessError):
        cat_file_batch(str(not_a_repo), ["HEAD:file1.txt"], check=True)


def test_cat_file_batch_timeout_handling(sample_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git", "cat-file", "--batch"], timeout=1.0)

    monkeypatch.setattr(subprocess, "run", _mock_run)

    # When check=False, timeout returns None
    assert cat_file_batch(str(sample_repo), ["HEAD:file1.txt"], check=False) is None

    # When check=True, timeout raises TimeoutExpired
    with pytest.raises(subprocess.TimeoutExpired):
        cat_file_batch(str(sample_repo), ["HEAD:file1.txt"], check=True)


def test_cat_file_batch_sets_env_vars(sample_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_env: dict | None = None
    real_run = subprocess.run

    def _mock_run(*args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _mock_run)
    res = cat_file_batch(str(sample_repo), ["HEAD:file1.txt"])
    assert res is not None
    assert captured_env is not None
    assert captured_env.get("GIT_NO_LAZY_FETCH") == "1"
    assert captured_env.get("GIT_LITERAL_PATHSPECS") == "1"


def test_cat_file_batch_ambiguous_header(sample_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=b"deadbeef ambiguous\n",
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _mock_run)
    assert cat_file_batch(str(sample_repo), ["deadbeef"], on_missing="omit") == []
    with pytest.raises(ValueError, match=r"unparseable header"):
        cat_file_batch(str(sample_repo), ["deadbeef"], on_missing="raise")


