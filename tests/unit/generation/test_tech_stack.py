"""Unit tests for tech stack and build command detection."""

from __future__ import annotations

import json

from repowise.core.generation.editor_files.tech_stack import (
    detect_build_commands,
    detect_tech_stack,
)

# ---------------------------------------------------------------------------
# detect_tech_stack
# ---------------------------------------------------------------------------


def test_empty_directory_returns_empty_list(tmp_path):
    result = detect_tech_stack(tmp_path)
    assert result == []


def test_detects_python_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myapp"\n', encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "Python" in names


def test_detects_fastapi_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi>=0.100"]\n', encoding="utf-8"
    )
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "FastAPI" in names


def test_detects_nodejs_from_package_json(tmp_path):
    pkg = {"name": "myapp", "engines": {"node": "20"}, "dependencies": {}}
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "Node.js" in names


def test_detects_react_from_package_json(tmp_path):
    pkg = {
        "name": "myapp",
        "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "React" in names


def test_detects_typescript_from_tsconfig(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "app", "dependencies": {}}), encoding="utf-8"
    )
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "TypeScript" in names


def test_detects_rust_from_cargo_toml(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "myapp"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "Rust" in names


def test_detects_go_from_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module myapp\n\ngo 1.22\n", encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "Go" in names
    go_item = next(i for i in items if i.name == "Go")
    assert go_item.version == "1.22"


def test_detects_docker(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    items = detect_tech_stack(tmp_path)
    names = [i.name for i in items]
    assert "Docker" in names


def test_returns_sorted_by_category_then_name(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi", "sqlalchemy"]\n', encoding="utf-8"
    )
    items = detect_tech_stack(tmp_path)
    # Verify sort order: categories should be grouped
    categories = [i.category for i in items]
    assert categories == sorted(categories) or len(set(categories)) == 1


# ---------------------------------------------------------------------------
# detect_build_commands
# ---------------------------------------------------------------------------


def test_empty_directory_returns_empty_dict(tmp_path):
    result = detect_build_commands(tmp_path)
    assert result == {}


def test_detects_pytest_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8"
    )
    cmds = detect_build_commands(tmp_path)
    assert "test" in cmds
    assert "pytest" in cmds["test"]


def test_detects_ruff_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")
    cmds = detect_build_commands(tmp_path)
    assert "lint" in cmds
    assert "ruff" in cmds["lint"]


def test_detects_npm_scripts(tmp_path):
    pkg = {
        "name": "myapp",
        "scripts": {"build": "tsc", "test": "jest", "lint": "eslint src/"},
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    cmds = detect_build_commands(tmp_path)
    assert "build" in cmds
    assert "test" in cmds
    assert "lint" in cmds


def test_detects_pnpm_when_lockfile_present(tmp_path):
    pkg = {"name": "app", "scripts": {"test": "vitest"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    cmds = detect_build_commands(tmp_path)
    assert cmds.get("test", "").startswith("pnpm")


def test_detects_makefile_targets(tmp_path):
    makefile = "build:\n\tgo build ./...\n\ntest:\n\tgo test ./...\n\nlint:\n\tgolangci-lint run\n"
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")
    cmds = detect_build_commands(tmp_path)
    assert "build" in cmds
    assert cmds["build"] == "make build"
    assert "test" in cmds
