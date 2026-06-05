"""Unit tests for command normalization and filter selection."""

from __future__ import annotations

import pytest

from repowise.core.distill.router import normalize_command, select_filter


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("git status", "git status"),
        ("GIT STATUS", "git status"),
        ("uv run pytest -x", "pytest -x"),
        ("python -m pytest tests/unit", "pytest tests/unit"),
        ("uv run python -m pytest", "pytest"),
        (".venv\\Scripts\\pytest.exe -q", "pytest -q"),
        ("C:\\tools\\git.exe log", "git log"),
        ("/usr/bin/git diff", "git diff"),
        ("FOO=bar npm test", "npm test"),
        ("npx vitest run", "vitest run"),
    ],
)
def test_normalize_command(raw: str, expected: str) -> None:
    assert normalize_command(raw) == expected


@pytest.mark.parametrize(
    ("command", "filter_name"),
    [
        ("git status", "git_status"),
        ("git log --oneline -20", "git_log"),
        ("git diff HEAD~3", "git_diff"),
        ("git show HEAD", "git_diff"),
        ("pytest tests/unit -x", "test_output"),
        ("uv run pytest", "test_output"),
        ("cargo test --workspace", "test_output"),
        ("go test ./...", "test_output"),
        ("npm test", "test_output"),
        ("npm run build", "build_output"),
        ("tsc --noEmit", "build_output"),
        ("cargo build --release", "build_output"),
        ("npm run type-check", "build_output"),
        ("find . -name *.py", "file_listing"),
        ("ls -la", "file_listing"),
        ("tree packages", "file_listing"),
        ("tail -200 app.log", "logs"),
        ("docker logs api", "logs"),
        ("kubectl logs pod-1", "logs"),
    ],
)
def test_select_filter_by_command(command: str, filter_name: str) -> None:
    chosen = select_filter(command)
    assert chosen is not None, f"no filter matched {command!r}"
    assert chosen.name == filter_name


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        "cd packages",
        "rm -rf build",
        "git push origin main",
        "git commit -m msg",
        "python script.py",
        "lsof -i :8000",  # 'ls' must not greedily match
        "git statuses",  # word boundary
    ],
)
def test_unrelated_commands_do_not_match(command: str) -> None:
    assert select_filter(command) is None


def test_content_sniff_fallback_when_no_command(load_fixture) -> None:
    output = load_fixture("pytest_fail.txt")
    chosen = select_filter("", output)
    assert chosen is not None
    assert chosen.name == "test_output"


def test_disabled_filters_are_skipped() -> None:
    assert select_filter("git status").name == "git_status"
    assert select_filter("git status", disabled=("git_status",)) is None
