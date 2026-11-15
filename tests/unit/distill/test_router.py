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
        ('cmd /c "dir /s /b packages"', "dir /s /b packages"),
        ("cmd.exe /c dir /s", "dir /s"),
        ("CMD /C tree src", "tree src"),
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
        ("cargo check", "build_output"),
        ("npm run type-check", "build_output"),
        ("npm run lint", "lint_output"),
        ("eslint src --format stylish", "lint_output"),
        ("npx eslint .", "lint_output"),
        ("ruff check packages", "lint_output"),
        ("uv run ruff check .", "lint_output"),
        ("flake8 src", "lint_output"),
        ("mypy packages/core", "lint_output"),
        ("cargo clippy --workspace", "lint_output"),
        ("golangci-lint run ./...", "lint_output"),
        ("biome check src", "lint_output"),
        ("find . -name *.py", "file_listing"),
        ("ls -la", "file_listing"),
        ("tree packages", "file_listing"),
        ('cmd /c "dir /s /b packages"', "file_listing"),
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
        "ruff format .",  # formatter, not a linter
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


@pytest.mark.parametrize(
    "command",
    [
        "git show origin/main:packages/core/src/engine.py",
        "git show HEAD~2:README.md | sed -n 165,215p",
        "git show v0.59.0:src/app.py",
        "git show :src/app.py",
    ],
)
def test_git_show_of_a_file_blob_is_not_routed_as_a_diff(command: str) -> None:
    blob = "\n".join(f"line {i}: def f{i}(): pass" for i in range(60))
    chosen = select_filter(command, blob)
    assert chosen is None or chosen.name != "git_diff"


@pytest.mark.parametrize(
    "command",
    [
        "git show HEAD",
        "git show HEAD -- src/app.py",
        "git show --format=%H:%s HEAD",
        "git show HEAD -- C:/repo/src/app.py",
    ],
)
def test_git_show_of_a_commit_still_routes_as_a_diff(command: str) -> None:
    assert select_filter(command, "").name == "git_diff"
