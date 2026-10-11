"""Tests for the pre-scan repo walk (``repowise.cli.ui.repo_scanner``).

The pre-scan feeds the mode-selection UI (file counts, language mix, exclude
suggestions), so a walk that wanders into vendored checkouts or junk trees
shows the user stats for a different codebase.
"""

from __future__ import annotations

from pathlib import Path

from repowise.cli.ui.repo_scanner import quick_repo_scan


def _write(root: Path, rel: str, content: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_counts_and_language_histogram(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py")
    _write(tmp_path, "src/util.py")
    _write(tmp_path, "web/index.ts")
    info = quick_repo_scan(tmp_path)
    assert info.total_files == 3
    assert info.language_counts["Python"] == 2
    assert info.language_counts["TypeScript"] == 1


def test_junk_dirs_not_counted(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py")
    _write(tmp_path, "node_modules/dep/index.js")
    _write(tmp_path, ".venv/lib/site.py")
    _write(tmp_path, "dist/bundle.js")
    _write(tmp_path, "vendor/lib.go")
    info = quick_repo_scan(tmp_path)
    assert info.total_files == 1
    assert "JavaScript" not in info.language_counts
    assert "Go" not in info.language_counts


def test_nested_git_repo_not_counted(tmp_path: Path) -> None:
    """A sibling/vendored checkout must not inflate the pre-scan stats or
    the large-dir exclude suggestions."""
    _write(tmp_path, "src/app.py")
    (tmp_path / "sibling-repo" / ".git").mkdir(parents=True)
    for i in range(60):
        _write(tmp_path, f"sibling-repo/src/file{i}.go")
    info = quick_repo_scan(tmp_path)
    assert info.total_files == 1
    assert "Go" not in info.language_counts
    assert all(d != "sibling-repo" for d, _ in info.large_dirs)


def test_own_git_dir_not_counted_but_root_exempt(tmp_path: Path) -> None:
    """The repo's own .git is skipped as a junk dir, not as a nested repo."""
    (tmp_path / ".git").mkdir()
    _write(tmp_path, ".git/config")
    _write(tmp_path, "main.py")
    info = quick_repo_scan(tmp_path)
    assert info.total_files == 1


def test_large_dirs_reported(tmp_path: Path) -> None:
    for i in range(60):
        _write(tmp_path, f"generated/file{i}.py")
    _write(tmp_path, "src/app.py")
    info = quick_repo_scan(tmp_path)
    assert ("generated", 60) in info.large_dirs
