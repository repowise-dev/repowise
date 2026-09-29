"""Package detection: any depth, workspace-declared members, no fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.traverser import FileTraverser


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _packages(root: Path) -> dict[str, tuple[str, bool]]:
    packages, _ = FileTraverser(root)._detect_monorepo()
    return {p.path: (p.name, p.declared) for p in packages}


def _npm(root: Path, rel: str, name: str) -> None:
    _write(root, f"{rel}/package.json", json.dumps({"name": name}))
    _write(root, f"{rel}/src/index.ts", "export {}")


def test_detect_monorepo_depth3_pnpm(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", json.dumps({"name": "root", "private": True}))
    _write(
        tmp_path,
        "pnpm-workspace.yaml",
        "packages:\n  - 'js/libs/*'\n  - 'js/libs/plugins/*'\n  - '!js/libs/skip'\n",
    )
    _npm(tmp_path, "js/libs/core", "@acme/core")
    _npm(tmp_path, "js/libs/plugins/alpha", "@acme/alpha")
    _npm(tmp_path, "js/libs/skip", "@acme/skip")
    # A package.json the pnpm workspace does not list is not one of its packages.
    _npm(tmp_path, "ts/tools/stray", "stray")

    structure = FileTraverser(tmp_path).get_repo_structure()
    found = {p.path: (p.name, p.declared) for p in structure.packages}
    assert found == {
        "js/libs/core": ("@acme/core", True),
        "js/libs/plugins/alpha": ("@acme/alpha", True),
    }
    assert structure.is_monorepo is True


def test_fixture_manifests_not_packages(tmp_path: Path) -> None:
    _write(tmp_path, "package.json", json.dumps({"workspaces": ["packages/*"]}))
    _npm(tmp_path, "packages/app", "app")
    # Fixtures and samples carry manifests without being packages.
    _npm(tmp_path, "packages/app/test/fixtures/proj", "fixture-proj")
    _npm(tmp_path, "packages/app/__fixtures__/other", "fixture-other")
    _npm(tmp_path, "examples/demo-app", "demo-app")
    # A template shipped inside a declared member is not a member either.
    _npm(tmp_path, "packages/app/templates/starter", "starter")
    # Other ecosystems with no workspace declaration keep their packages.
    _write(tmp_path, "tools/lint/pyproject.toml", "[project]\nname = 'acme-lint'\n")
    _write(tmp_path, "tools/lint/lint.py", "pass")

    assert _packages(tmp_path) == {
        "packages/app": ("app", True),
        "tools/lint": ("acme-lint", False),
    }


def test_uv_workspace_members(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        "[project]\nname = 'root'\n\n[tool.uv.workspace]\n"
        "members = ['libs/*', 'apps/api']\nexclude = ['libs/legacy']\n",
    )
    for rel, name in (("libs/core", "acme-core"), ("libs/legacy", "legacy"), ("apps/api", "api")):
        _write(tmp_path, f"{rel}/pyproject.toml", f"[project]\nname = '{name}'\n")
        _write(tmp_path, f"{rel}/main.py", "pass")
    _write(tmp_path, "scratch/pyproject.toml", "[tool.poetry]\nname = 'scratch'\n")

    assert _packages(tmp_path) == {
        "apps/api": ("api", True),
        "libs/core": ("acme-core", True),
    }


def test_cargo_workspace_members(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "Cargo.toml",
        "[workspace]\nmembers = ['crates/*']\nexclude = ['crates/old']\n",
    )
    for rel, name in (("crates/cli", "acme-cli"), ("crates/old", "old")):
        _write(tmp_path, f"{rel}/Cargo.toml", f"[package]\nname = '{name}'\n")
        _write(tmp_path, f"{rel}/src/lib.rs", "")
    # A Python eval fixture inside a crate is not a package of the repo.
    _write(tmp_path, "crates/cli/truth/case-one/pyproject.toml", "[project]\nname = 'case'\n")

    assert _packages(tmp_path) == {"crates/cli": ("acme-cli", True)}


def test_go_work_members(tmp_path: Path) -> None:
    _write(
        tmp_path, "go.work", "go 1.22\n\nuse (\n\t./svc/api // the API\n\t./lib\n)\nuse ./tool\n"
    )
    for rel in ("svc/api", "lib", "tool", "unused"):
        _write(tmp_path, f"{rel}/go.mod", f"module example.com/{rel}\n\ngo 1.22\n")
        _write(tmp_path, f"{rel}/main.go", "package main\n")

    assert _packages(tmp_path) == {
        "lib": ("example.com/lib", True),
        "svc/api": ("example.com/svc/api", True),
        "tool": ("example.com/tool", True),
    }


def test_no_declaration_keeps_every_root_at_any_depth(tmp_path: Path) -> None:
    _write(tmp_path, "a/b/c/pyproject.toml", "[project]\nname = 'deep'\n")
    _write(tmp_path, "svc/package.json", "{}")

    # Unnamed manifests fall back to the directory name.
    assert _packages(tmp_path) == {"a/b/c": ("deep", False), "svc": ("svc", False)}
