"""collect_build_units over small on-disk repos, one ecosystem at a time."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.build_units.adapters import cargo as cargo_adapter
from repowise.core.build_units.adapters import dotnet as dotnet_adapter
from repowise.core.build_units.adapters import go as go_adapter
from repowise.core.build_units.collect import collect_build_units
from repowise.core.ingestion.resolvers.context import ResolverContext


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ResolverContext:
    paths = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    return ResolverContext(path_set=paths, stem_map={}, graph=nx.DiGraph(), repo_path=root)


def _collect(root: Path, files: dict[str, str]):
    _write(root, files)
    return collect_build_units(_ctx(root))


def _deps(index) -> dict[str, tuple[str, ...]]:
    return {uid: unit.deps for uid, unit in index.units.items()}


def _owners(index, path: str) -> list[str]:
    return [u.id for u in index.units_owning(path)]


_CSPROJ = '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{refs}</ItemGroup></Project>'


def test_dotnet_projects_references_and_listed_files(tmp_path):
    index = _collect(tmp_path, {
        "src/App/App.csproj": _CSPROJ.format(refs='<ProjectReference Include="..\\Lib\\Lib.csproj" />'),
        "src/App/Program.cs": "namespace App; class Program {}",
        "src/Lib/Lib.csproj": _CSPROJ.format(refs=""),
        "src/Lib/Util.cs": "namespace Lib; class Util {}",
    })
    assert _deps(index) == {
        "dotnet:src/App/App.csproj": ("dotnet:src/Lib/Lib.csproj",),
        "dotnet:src/Lib/Lib.csproj": (),
    }
    app = index.unit("dotnet:src/App/App.csproj")
    assert (app.root, app.manifest, app.name, app.source) == ("src/App", "src/App/App.csproj", "App", "msbuild")
    assert _owners(index, "src/App/Program.cs") == ["dotnet:src/App/App.csproj"]
    assert [u.id for u in index.dependents("dotnet:src/Lib/Lib.csproj")] == ["dotnet:src/App/App.csproj"]


def test_cargo_path_and_workspace_inherited_deps(tmp_path):
    index = _collect(tmp_path, {
        "Cargo.toml": (
            '[workspace]\nmembers = ["crates/*"]\n'
            '[workspace.dependencies]\ncore = { path = "crates/core" }\nserde = "1"\n'
        ),
        "crates/core/Cargo.toml": '[package]\nname = "core"\n',
        "crates/core/src/lib.rs": "",
        "crates/util/Cargo.toml": '[package]\nname = "util"\n',
        "crates/util/src/lib.rs": "",
        "crates/app/Cargo.toml": (
            '[package]\nname = "app"\n[dependencies]\n'
            'core = { workspace = true }\nutil = { path = "../util" }\nserde = { workspace = true }\n'
        ),
        "crates/app/src/main.rs": "fn main() {}",
    })
    assert _deps(index) == {
        "cargo:app": ("cargo:core", "cargo:util"),
        "cargo:core": (),
        "cargo:util": (),
    }
    assert index.unit("cargo:app").manifest == "crates/app/Cargo.toml"
    assert _owners(index, "crates/app/src/main.rs") == ["cargo:app"]


def test_gradle_projects_project_deps_and_source_set_files(tmp_path):
    index = _collect(tmp_path, {
        "settings.gradle.kts": 'include("core", "app")\n',
        "core/build.gradle.kts": "plugins { `java-library` }\n",
        "core/src/main/java/c/Core.java": "package c; public class Core {}",
        "app/build.gradle.kts": (
            "dependencies {\n    implementation(project(\":core\"))\n"
            "    // implementation(project(\":gone\"))\n}\n"
        ),
        "app/src/main/java/a/App.java": "package a; public class App {}",
        "app/src/test/java/a/AppTest.java": "package a; public class AppTest {}",
    })
    assert _deps(index) == {"gradle::app": ("gradle::core",), "gradle::core": ()}
    app = index.unit("gradle::app")
    assert (app.root, app.manifest, app.name) == ("app", "app/build.gradle.kts", "app")
    assert _owners(index, "app/src/test/java/a/AppTest.java") == ["gradle::app"]
    assert _owners(index, "app/build.gradle.kts") == ["gradle::app"]


def test_npm_workspace_member_deps(tmp_path):
    index = _collect(tmp_path, {
        "package.json": json.dumps({"name": "root", "private": True, "workspaces": ["packages/*"]}),
        "packages/a/package.json": json.dumps(
            {"name": "@x/a", "dependencies": {"@x/b": "*", "react": "18"}, "devDependencies": {"@x/c": "*"}}
        ),
        "packages/a/index.ts": "",
        "packages/b/package.json": json.dumps({"name": "@x/b"}),
        "packages/c/package.json": json.dumps({"name": "@x/c", "peerDependencies": {"@x/b": "*"}}),
        "fixtures/demo/package.json": json.dumps({"name": "demo"}),
        "tools/pyproject.toml": "[project]\nname = 'tools'\n",
    })
    assert _deps(index) == {
        "manifest:tools": (),
        "npm:@x/a": ("npm:@x/b", "npm:@x/c"),
        "npm:@x/b": (),
        "npm:@x/c": ("npm:@x/b",),
    }
    assert _owners(index, "packages/a/index.ts") == ["npm:@x/a"]
    # The workspace declaration rules on every package.json: the root and a
    # fixture are no members, and the fallback does not bring them back.
    assert _owners(index, "package.json") == []
    assert _owners(index, "fixtures/demo/package.json") == []


def test_go_modules_and_in_repo_requires(tmp_path):
    index = _collect(tmp_path, {
        "go.mod": (
            "module example.com/root\n\ngo 1.22\n\nrequire (\n\texample.com/lib v0.0.0\n"
            "\tgithub.com/pkg/errors v0.9.1\n)\n\nreplace example.com/lib => ./lib\n"
        ),
        "main.go": "package main\nfunc main() {}\n",
        "lib/go.mod": "module example.com/lib\n\ngo 1.22\n",
        "lib/lib.go": "package lib\n",
    })
    assert _deps(index) == {"go:example.com/lib": (), "go:example.com/root": ("go:example.com/lib",)}
    assert index.unit("go:example.com/lib").root == "lib"
    assert _owners(index, "lib/lib.go") == ["go:example.com/lib"]
    assert _owners(index, "main.go") == ["go:example.com/root"]


def test_two_units_at_one_root_both_own_unlisted_files(tmp_path):
    index = _collect(tmp_path, {
        "package.json": json.dumps({"name": "root", "workspaces": ["web"]}),
        "web/package.json": json.dumps({"name": "web"}),
        "web/Web.csproj": _CSPROJ.format(refs=""),
        "web/Program.cs": "class Program {}",
        "web/index.ts": "",
    })
    assert _owners(index, "web/index.ts") == ["dotnet:web/Web.csproj", "npm:web"]
    # The .NET index lists the C# file, so only its project owns it.
    assert _owners(index, "web/Program.cs") == ["dotnet:web/Web.csproj"]


def test_adapter_beats_fallback_and_nested_roots_deepest_wins(tmp_path):
    index = _collect(tmp_path, {
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        "packages/a/package.json": json.dumps({"name": "a"}),
        # Not all of this root's manifests are npm's, but npm's unit holds the root.
        "packages/a/pyproject.toml": "[project]\nname = 'a'\n",
        "py/pyproject.toml": "[project]\nname = 'py'\n",
        "py/y.py": "",
        "py/sub/pyproject.toml": "[project]\nname = 'sub'\n",
        "py/sub/x.py": "",
    })
    assert set(index.units) == {"manifest:py", "manifest:py/sub", "npm:a"}
    assert index.unit("manifest:py/sub").manifest == "py/sub/pyproject.toml"
    assert _owners(index, "py/sub/x.py") == ["manifest:py/sub"]
    assert _owners(index, "py/y.py") == ["manifest:py"]


def test_absent_ecosystems_never_build_their_index(tmp_path, monkeypatch):
    def boom(_ctx):
        raise AssertionError("index built for an absent ecosystem")

    monkeypatch.setattr(dotnet_adapter, "get_or_build_index", boom)
    monkeypatch.setattr(go_adapter, "get_or_build_go_index", boom)
    index = _collect(tmp_path, {"pkg/pyproject.toml": "[project]\nname = 'pkg'\n", "pkg/m.py": ""})
    assert list(index.units) == ["manifest:pkg"]
    assert _owners(index, "pkg/m.py") == ["manifest:pkg"]


def test_failing_index_is_skipped_and_its_roots_fall_back(tmp_path, monkeypatch):
    def broken(_ctx):
        raise ValueError("unreadable")

    monkeypatch.setattr(cargo_adapter, "get_or_build_cargo_workspace_index", broken)
    index = _collect(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        "crates/app/Cargo.toml": '[package]\nname = "app"\n',
        "crates/app/src/main.rs": "",
        "go.mod": "module example.com/m\n",
        "m.go": "package m\n",
    })
    assert set(index.units) == {"go:example.com/m", "manifest:crates/app"}
    assert _owners(index, "crates/app/src/main.rs") == ["manifest:crates/app"]


def test_two_runs_give_the_same_index(tmp_path):
    _write(tmp_path, {
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        "packages/b/package.json": json.dumps({"name": "b", "dependencies": {"a": "*"}}),
        "packages/a/package.json": json.dumps({"name": "a"}),
        "packages/a/i.ts": "",
    })
    first, second = collect_build_units(_ctx(tmp_path)), collect_build_units(_ctx(tmp_path))
    assert first == second
    assert list(first.units) == sorted(first.units)
    assert list(first.file_owners) == sorted(first.file_owners)


@pytest.mark.parametrize("root_manifest", ["pyproject.toml", "setup.py"])
def test_root_manifest_is_a_unit(tmp_path, root_manifest):
    index = _collect(tmp_path, {root_manifest: "", "src/x.py": ""})
    assert index.unit("manifest:.").manifest == root_manifest
    assert _owners(index, "src/x.py") == ["manifest:."]


def test_duplicate_id_keeps_the_shallowest_root(tmp_path):
    index = _collect(tmp_path, {
        "a/go.mod": "module example.com/same\n",
        "a/a.go": "package a\n",
        "b/copy/go.mod": "module example.com/same\n",
        "b/copy/b.go": "package b\n",
    })
    assert list(index.units) == ["go:example.com/same"]
    assert index.unit("go:example.com/same").root == "a"
    assert _owners(index, "b/copy/b.go") == []


def test_cargo_crates_outside_the_workspace_get_units(tmp_path):
    index = _collect(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/app"]\nexclude = ["fuzz"]\n',
        "crates/app/Cargo.toml": '[package]\nname = "app"\n',
        "crates/app/src/main.rs": "",
        # Excluded from the workspace, no explicit targets.
        "fuzz/Cargo.toml": '[package]\nname = "fuzz"\n',
        "fuzz/src/lib.rs": "",
        # Standalone, never listed by the workspace.
        "tools/gen/Cargo.toml": '[package]\nname = "gen"\n',
        "tools/gen/src/main.rs": "",
        # A nested virtual workspace is no unit either.
        "tools/ws/Cargo.toml": '[workspace]\nmembers = []\n',
        "tools/ws/build.rs": "",
    })
    # The virtual workspace root is no unit.
    assert set(index.units) == {"cargo:app", "cargo:fuzz", "cargo:gen"}
    assert _owners(index, "fuzz/src/lib.rs") == ["cargo:fuzz"]
    assert _owners(index, "tools/gen/src/main.rs") == ["cargo:gen"]
    assert _owners(index, "Cargo.toml") == []
    assert _owners(index, "tools/ws/build.rs") == []


def test_cargo_crate_with_no_root_manifest(tmp_path):
    index = _collect(tmp_path, {
        "native/Cargo.toml": '[package]\nname = "native"\n',
        "native/src/lib.rs": "",
        "pyproject.toml": "[project]\nname = 'host'\n",
    })
    assert set(index.units) == {"cargo:native", "manifest:."}
    assert _owners(index, "native/src/lib.rs") == ["cargo:native"]


def test_cargo_path_dep_outside_the_repo_links_no_same_named_crate(tmp_path):
    repo = tmp_path / "repo"
    index = _collect(repo, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
        "crates/core/Cargo.toml": '[package]\nname = "core"\n',
        "crates/core/src/lib.rs": "",
        "crates/app/Cargo.toml": (
            '[package]\nname = "app"\n[dependencies]\ncore = { path = "../../../elsewhere/core" }\n'
        ),
        "crates/app/src/main.rs": "",
    })
    assert index.unit("cargo:app").deps == ()
