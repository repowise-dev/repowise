"""collect_build_units over small on-disk repos, one ecosystem at a time."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.build_units.adapters import cargo as cargo_adapter
from repowise.core.build_units.adapters import dart as dart_adapter
from repowise.core.build_units.adapters import dotnet as dotnet_adapter
from repowise.core.build_units.adapters import go as go_adapter
from repowise.core.build_units.adapters import maven as maven_adapter
from repowise.core.build_units.adapters import pascal as pascal_adapter
from repowise.core.build_units.adapters import php as php_adapter
from repowise.core.build_units.adapters import python as python_adapter
from repowise.core.build_units.adapters import swift as swift_adapter
from repowise.core.build_units.collect import collect_build_units
from repowise.core.ingestion.graph import build_stem_map
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust_workspace import get_or_build_cargo_workspace_index


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


def test_go_modules_replace_only_dependency(tmp_path):
    index = _collect(tmp_path, {
        "a/go.mod": "module example.com/a\n\ngo 1.22\n\nreplace example.com/b => ../b\n",
        "a/main.go": "package main\nfunc main() {}\n",
        "b/go.mod": "module example.com/b\n\ngo 1.22\n",
        "b/b.go": "package b\n",
    })
    assert _deps(index) == {"go:example.com/a": ("go:example.com/b",), "go:example.com/b": ()}


def test_go_modules_replace_alias_dependency(tmp_path):
    index = _collect(tmp_path, {
        "a/go.mod": (
            "module example.com/a\n\ngo 1.22\n\n"
            "require example.com/old v0.0.0\n"
            "replace example.com/old => ../b\n"
        ),
        "a/main.go": "package main\nfunc main() {}\n",
        "b/go.mod": "module example.com/b\n\ngo 1.22\n",
        "b/b.go": "package b\n",
    })
    assert _deps(index) == {"go:example.com/a": ("go:example.com/b",), "go:example.com/b": ()}


def test_go_modules_replace_does_not_leak_across_modules(tmp_path):
    index = _collect(tmp_path, {
        "a/go.mod": "module example.com/a\n\ngo 1.22\n\nrequire example.com/x v1.0.0\n",
        "a/main.go": "package main\nfunc main() {}\n",
        "b/go.mod": "module example.com/b\n\ngo 1.22\n",
        "b/b.go": "package b\n",
        "c/go.mod": "module example.com/c\n\ngo 1.22\n\nreplace example.com/x => ../b\n",
        "c/c.go": "package c\n",
    })
    assert _deps(index) == {
        "go:example.com/a": (),
        "go:example.com/b": (),
        "go:example.com/c": ("go:example.com/b",),
    }


def test_go_modules_outside_repo_replace_does_not_fall_back_to_same_named_module(tmp_path):
    index = _collect(tmp_path, {
        "a/go.mod": "module example.com/a\n\ngo 1.22\n\nreplace example.com/b => ../../outside/b\n",
        "a/main.go": "package main\nfunc main() {}\n",
        "b/go.mod": "module example.com/b\n\ngo 1.22\n",
        "b/b.go": "package b\n",
    })
    assert _deps(index) == {"go:example.com/a": (), "go:example.com/b": ()}


def test_go_modules_replace_target_without_go_mod_is_not_attributed_to_root(tmp_path):
    index = _collect(tmp_path, {
        "go.mod": "module example.com/root\n\ngo 1.22\n",
        "main.go": "package main\nfunc main() {}\n",
        "a/go.mod": "module example.com/a\n\ngo 1.22\n\nreplace example.com/missing => ../missing_dir\n",
        "a/a.go": "package a\n",
        "missing_dir/data.txt": "no go.mod here\n",
    })
    assert _deps(index) == {"go:example.com/root": (), "go:example.com/a": ()}


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
        "packages/a/Gemfile": "",
        "rb/Gemfile": "",
        "rb/y.rb": "",
        "rb/sub/Gemfile": "",
        "rb/sub/x.rb": "",
    })
    assert set(index.units) == {"manifest:rb", "manifest:rb/sub", "npm:a"}
    assert index.unit("manifest:rb/sub").manifest == "rb/sub/Gemfile"
    assert _owners(index, "rb/sub/x.rb") == ["manifest:rb/sub"]
    assert _owners(index, "rb/y.rb") == ["manifest:rb"]


def test_absent_ecosystems_never_build_their_index(tmp_path, monkeypatch):
    def boom(_ctx):
        raise AssertionError("index built for an absent ecosystem")

    monkeypatch.setattr(dotnet_adapter, "get_or_build_index", boom)
    monkeypatch.setattr(go_adapter, "get_or_build_go_index", boom)
    monkeypatch.setattr(python_adapter, "package_manifests", boom)
    monkeypatch.setattr(maven_adapter, "load_maven_reactor", boom)
    monkeypatch.setattr(dart_adapter, "package_manifests", boom)
    monkeypatch.setattr(swift_adapter, "package_manifests", boom)
    monkeypatch.setattr(php_adapter, "repo_composer_manifests", boom)
    index = _collect(tmp_path, {
        "pkg/Gemfile": "",
        "pkg/m.rb": "",
        # Manifests alone do not wake an adapter: there is no source for it to own.
        "pyproject.toml": "[project]\nname = 'root'\n",
        "pom.xml": "<project><groupId>g</groupId><artifactId>a</artifactId></project>",
        "pkg/pubspec.yaml": "name: pkg\n",
        "spm/Package.swift": _PACKAGE_SWIFT_LIB,
        "composer.json": '{"name": "acme/test"}',
    })
    assert list(index.units) == ["manifest:.", "manifest:pkg", "manifest:spm"]
    assert _owners(index, "pkg/m.rb") == ["manifest:pkg"]
    assert index.failed_ecosystems == frozenset()


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
    assert index.failed_ecosystems == frozenset({"cargo"})
    # A run that failed compares equal to the same units read back without the flag.
    assert index == type(index)(units=index.units, file_owners=index.file_owners)


def test_clean_run_reports_no_failed_ecosystem(tmp_path):
    index = _collect(tmp_path, {"go.mod": "module example.com/m\n", "m.go": "package m\n"})
    assert index.failed_ecosystems == frozenset()


def test_unparseable_cargo_manifest_keeps_its_files(tmp_path):
    index = _collect(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/app"]\n',
        "crates/app/Cargo.toml": '[package]\nname = "app"\n',
        "crates/app/src/main.rs": "",
        "broken/Cargo.toml": "[package\nname = ",
        "broken/src/lib.rs": "",
    })
    assert set(index.units) == {"cargo:app", "manifest:broken"}
    assert _owners(index, "broken/src/lib.rs") == ["manifest:broken"]


def test_malformed_csproj_keeps_its_files_beside_a_good_one(tmp_path):
    index = _collect(tmp_path, {
        "src/Good/Good.csproj": _CSPROJ.format(refs=""),
        "src/Good/A.cs": "class A {}",
        "src/Bad/Bad.csproj": "<Project><ItemGroup>",
        "src/Bad/B.cs": "class B {}",
    })
    assert set(index.units) == {"dotnet:src/Good/Good.csproj", "manifest:src/Bad"}
    assert _owners(index, "src/Bad/B.cs") == ["manifest:src/Bad"]


def test_unreadable_package_json_keeps_its_root(tmp_path):
    index = _collect(tmp_path, {
        "package.json": json.dumps({"name": "root", "workspaces": ["packages/*"]}),
        "packages/a/package.json": json.dumps({"name": "a"}),
        "tools/broken/package.json": "{ not json",
        "tools/broken/x.js": "",
    })
    assert set(index.units) == {"npm:a", "manifest:tools/broken"}
    assert _owners(index, "tools/broken/x.js") == ["manifest:tools/broken"]


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


def test_gradle_root_project_and_quoted_configurations(tmp_path):
    index = _collect(tmp_path, {
        "settings.gradle.kts": 'include("lib", "gen", "app")\n',
        "build.gradle.kts": 'dependencies {\n    dokka(project(":lib"))\n}\n',
        "lib/build.gradle.kts": 'dependencies {\n    "kspTest"(project(":gen"))\n}\n',
        "lib/src/main/kotlin/l/Lib.kt": "package l\n",
        "gen/build.gradle.kts": "",
        "gen/src/main/kotlin/g/Gen.kt": "package g\n",
        "app/build.gradle": (
            "dependencies {\n    testImplementation project(':gen')\n"
            "    'kapt' project(path: ':lib')\n}\n"
        ),
        "app/src/main/java/a/App.java": "package a;\n",
    })
    assert _deps(index) == {
        "gradle::": ("gradle::lib",),
        "gradle::app": ("gradle::gen", "gradle::lib"),
        "gradle::gen": (),
        "gradle::lib": ("gradle::gen",),
    }
    root = index.unit("gradle::")
    assert (root.root, root.manifest, root.name) == ("", "build.gradle.kts", ":")
    assert _owners(index, "lib/src/main/kotlin/l/Lib.kt") == ["gradle::lib"]


def test_cargo_crate_outside_the_workspace_reads_its_path_deps(tmp_path):
    index = _collect(tmp_path, {
        "Cargo.toml": '[workspace]\nmembers = ["crates/globset"]\nexclude = ["fuzz"]\n',
        "crates/globset/Cargo.toml": '[package]\nname = "globset"\n',
        "crates/globset/src/lib.rs": "",
        "fuzz/Cargo.toml": (
            '[package]\nname = "fuzz"\n[workspace]\nmembers = ["."]\n'
            '[dependencies]\nglobset = { path = "../crates/globset" }\n'
            'other = { path = "../../outside/other" }\n'
        ),
        "fuzz/src/lib.rs": "",
    })
    assert index.unit("cargo:fuzz").deps == ("cargo:globset",)
    assert [u.id for u in index.dependents("cargo:globset")] == ["cargo:fuzz"]


def test_excluded_crate_with_explicit_targets_keeps_its_deps(tmp_path):
    index = _collect(tmp_path, {
        "Cargo.toml": (
            '[package]\nname = "app"\nversion = "0.1.0"\n'
            '[workspace]\nmembers = ["a"]\nexclude = ["fuzz"]\n'
        ),
        "src/main.rs": "fn main() {}",
        "a/Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
        "a/src/lib.rs": "",
        "fuzz/Cargo.toml": (
            '[package]\nname = "fuzz"\nversion = "0.0.1"\n'
            '[dependencies]\na = { path = "../a" }\n'
            '[workspace]\nmembers = ["."]\n'
            '[[bin]]\nname = "x"\npath = "fuzz_targets/x.rs"\n'
        ),
        "fuzz/fuzz_targets/x.rs": "fn main() {}",
    })
    assert _deps(index) == {"cargo:a": (), "cargo:app": (), "cargo:fuzz": ("cargo:a",)}
    assert _owners(index, "fuzz/fuzz_targets/x.rs") == ["cargo:fuzz"]


def test_root_binary_outside_src_is_one_crate(tmp_path):
    files = {
        "Cargo.toml": (
            '[package]\nname = "rg"\n[[bin]]\nname = "rg"\npath = "crates/core/main.rs"\n'
            '[workspace]\nmembers = ["crates/globset"]\n'
            '[dependencies]\nglobset = { path = "crates/globset" }\n'
        ),
        "crates/core/main.rs": "fn main() {}",
        "crates/globset/Cargo.toml": '[package]\nname = "globset"\n',
        "crates/globset/src/lib.rs": "",
    }
    _write(tmp_path, files)
    crates = get_or_build_cargo_workspace_index(_ctx(tmp_path)).crates
    assert sorted(crate.name for crate in crates) == ["globset", "rg"]
    index = collect_build_units(_ctx(tmp_path))
    assert index.unit("cargo:rg").deps == ("cargo:globset",)
    assert _owners(index, "crates/core/main.rs") == ["cargo:rg"]


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


def test_python_uv_workspace_and_path_sources(tmp_path):
    index = _collect(tmp_path, {
        # A virtual workspace root names no distribution: the fallback keeps it.
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
        "scripts/run.py": "",
        "packages/core/pyproject.toml": '[project]\nname = "Acme_Core"\n',
        "packages/core/src/acme/core.py": "",
        "packages/cli/pyproject.toml": (
            '[project]\nname = "acme-cli"\ndependencies = ["acme-core>=1", "acme-tools", "requests"]\n'
            # uv's list form: one source per marker.
            '[tool.uv.sources]\nacme-core = [{ workspace = true, marker = "python_version >= \'3.11\'" }]\n'
            'acme-tools = { git = "https://example.com/tools.git" }\n'
        ),
        "packages/cli/main.py": "",
        "packages/server/pyproject.toml": (
            '[project]\nname = "acme-server"\n'
            '[tool.uv.sources]\nlib = { path = "../../libs/renamed", editable = true }\n'
        ),
        "libs/renamed/pyproject.toml": '[project]\nname = "acme-lib"\n',
        "libs/tools/pyproject.toml": '[project]\nname = "acme-tools"\n',
    })
    assert _deps(index) == {
        "manifest:.": (),
        "python:acme-cli": ("python:acme-core",),
        "python:acme-core": (),
        "python:acme-lib": (),
        # A git source names an outside distribution, whatever the repo holds.
        "python:acme-tools": (),
        "python:acme-server": ("python:acme-lib",),
    }
    core = index.unit("python:acme-core")
    assert (core.name, core.root, core.manifest, core.source) == (
        "Acme_Core", "packages/core", "packages/core/pyproject.toml", "pyproject",
    )
    assert _owners(index, "packages/core/src/acme/core.py") == ["python:acme-core"]
    assert _owners(index, "scripts/run.py") == ["manifest:."]
    assert [u.id for u in index.dependents("python:acme-core")] == ["python:acme-cli"]


def test_python_poetry_path_deps_and_name_matched_deps(tmp_path):
    index = _collect(tmp_path, {
        "app/pyproject.toml": (
            '[tool.poetry]\nname = "app"\n'
            '[tool.poetry.dependencies]\npython = "^3.11"\nlib = { path = "../lib", develop = true }\n'
            '[tool.poetry.group.dev.dependencies]\ntestkit = { path = "../testkit" }\n'
            'tooling = { path = ".." }\n'
            # Outside the repo: links nothing, even though a unit has this name.
            'lib-utils = { path = "../../elsewhere/utils" }\n'
        ),
        "pyproject.toml": '[project]\nname = "root-tools"\n',
        "app/app.py": "",
        "lib/pyproject.toml": '[tool.poetry]\nname = "lib"\n',
        "testkit/pyproject.toml": '[tool.poetry]\nname = "testkit"\n',
        "web/pyproject.toml": (
            '[project]\nname = "web"\n'
            'dependencies = ["Lib.Utils @ file:///${PROJECT_ROOT}/../utils"]\n'
            '[project.optional-dependencies]\ntest = ["testkit[extra]>=1"]\n'
        ),
        "utils/pyproject.toml": '[project]\nname = "lib-utils"\n',
    })
    assert _deps(index) == {
        "python:app": ("python:lib", "python:root-tools", "python:testkit"),
        "python:lib": (),
        "python:lib-utils": (),
        "python:root-tools": (),
        "python:testkit": (),
        "python:web": ("python:lib-utils", "python:testkit"),
    }


def test_python_unit_beats_fallback_at_its_root(tmp_path):
    index = _collect(tmp_path, {
        "pkg/pyproject.toml": '[project]\nname = "pkg"\n',
        "pkg/setup.py": "",
        "pkg/m.py": "",
        "legacy/setup.py": "",
        "legacy/l.py": "",
        "bare/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
    })
    assert set(index.units) == {"manifest:bare", "manifest:legacy", "python:pkg"}
    assert _owners(index, "pkg/m.py") == ["python:pkg"]


_POM = (
    '<project xmlns="http://maven.apache.org/POM/4.0.0">'
    "<modelVersion>4.0.0</modelVersion>{body}</project>"
)


def _parent(artifact: str, group: str = "com.acme") -> str:
    return f"<parent><groupId>{group}</groupId><artifactId>{artifact}</artifactId><version>1</version></parent>"


def _dep(coordinate: str, scope: str = "") -> str:
    group, artifact = coordinate.split(":")
    scope_tag = f"<scope>{scope}</scope>" if scope else ""
    return f"<dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>{scope_tag}</dependency>"


def test_maven_reactor_modules_inherited_group_and_test_scope_deps(tmp_path):
    index = _collect(tmp_path, {
        "pom.xml": _POM.format(body=(
            "<groupId>com.acme</groupId><artifactId>parent</artifactId><version>1</version>"
            "<packaging>pom</packaging>"
            "<modules><module>core</module><module>app</module>"
            "<module>testkit</module><module>services</module></modules>"
        )),
        "core/pom.xml": _POM.format(body=_parent("parent") + "<artifactId>core</artifactId>"),
        "core/src/main/java/a/Core.java": "package a; class Core {}",
        "testkit/pom.xml": _POM.format(body=_parent("parent") + "<artifactId>testkit</artifactId>"),
        "app/pom.xml": _POM.format(body=(
            _parent("parent") + "<artifactId>app</artifactId><dependencies>"
            + _dep("com.acme:core") + _dep("com.acme:testkit", "test") + _dep("junit:junit", "test")
            + "</dependencies>"
        )),
        "services/pom.xml": _POM.format(body=(
            _parent("parent") + "<groupId>com.acme.svc</groupId><artifactId>services</artifactId>"
            "<packaging>pom</packaging><modules><module>api</module></modules>"
        )),
        "services/api/pom.xml": _POM.format(body=(
            _parent("services", "com.acme.svc") + "<artifactId>api</artifactId>"
            "<dependencies>" + _dep("com.acme:core") + "</dependencies>"
        )),
        "services/api/src/main/java/s/Api.java": "package s; class Api {}",
        # Outside the reactor: no unit, and the fallback leaves it too.
        "examples/demo/pom.xml": _POM.format(body="<groupId>x</groupId><artifactId>demo</artifactId>"),
        "examples/demo/Demo.java": "class Demo {}",
    })
    assert _deps(index) == {
        "maven:com.acme.svc:api": ("maven:com.acme:core",),
        "maven:com.acme.svc:services": (),
        "maven:com.acme:app": ("maven:com.acme:core", "maven:com.acme:testkit"),
        "maven:com.acme:core": (),
        "maven:com.acme:parent": (),
        "maven:com.acme:testkit": (),
    }
    api = index.unit("maven:com.acme.svc:api")
    assert (api.name, api.root, api.manifest, api.source) == (
        "api", "services/api", "services/api/pom.xml", "maven",
    )
    assert index.unit("maven:com.acme:parent").root == ""
    assert _owners(index, "services/api/src/main/java/s/Api.java") == ["maven:com.acme.svc:api"]
    assert _owners(index, "examples/demo/Demo.java") == ["maven:com.acme:parent"]


def test_maven_without_root_pom_falls_back(tmp_path):
    index = _collect(tmp_path, {
        "lib/pom.xml": _POM.format(body="<groupId>g</groupId><artifactId>lib</artifactId>"),
        "lib/src/main/java/L.java": "class L {}",
    })
    assert list(index.units) == ["manifest:lib"]


def test_python_unreadable_pyproject_falls_back_alone(tmp_path):
    _write(tmp_path, {
        "good/pyproject.toml": '[project]\nname = "good"\n',
        "good/g.py": "",
    })
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "pyproject.toml").write_bytes(b'[project]\nname = "caf\xe9"\n')
    (tmp_path / "bad" / "b.py").write_text("", encoding="utf-8")
    index = collect_build_units(_ctx(tmp_path))
    assert set(index.units) == {"manifest:bad", "python:good"}


def test_python_path_source_to_a_dropped_duplicate_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "a/dup/pyproject.toml": '[project]\nname = "dup"\n',
        "b/dup/pyproject.toml": '[project]\nname = "Dup"\n',
        "b/dup/d.py": "",
        "app/pyproject.toml": (
            '[project]\nname = "app"\n[tool.uv.sources]\nother = { path = "../b/dup" }\n'
        ),
        "web/pyproject.toml": (
            '[project]\nname = "web"\n[tool.uv.sources]\nkept = { path = "../a/dup" }\n'
        ),
    })
    assert index.unit("python:dup").root == "a/dup"
    assert index.unit("python:app").deps == ()
    assert index.unit("python:web").deps == ("python:dup",)
    assert _owners(index, "b/dup/d.py") == ["manifest:b/dup"]


def test_python_poetry_legacy_dev_dependencies(tmp_path):
    index = _collect(tmp_path, {
        "app/pyproject.toml": (
            '[tool.poetry]\nname = "app"\n'
            '[tool.poetry.dev-dependencies]\ntestkit = { path = "../testkit", develop = true }\n'
            'helpers = "*"\n'
        ),
        "app/app.py": "",
        "testkit/pyproject.toml": '[tool.poetry]\nname = "testkit"\n',
        "helpers/pyproject.toml": '[tool.poetry]\nname = "helpers"\n',
    })
    assert index.unit("python:app").deps == ("python:helpers", "python:testkit")


def test_python_marker_split_uv_source_keeps_the_workspace_edge(tmp_path):
    index = _collect(tmp_path, {
        "core/pyproject.toml": '[project]\nname = "core"\n',
        "core/c.py": "",
        "cli/pyproject.toml": (
            '[project]\nname = "cli"\ndependencies = ["core", "ext"]\n[tool.uv.sources]\n'
            'core = [\n  { workspace = true, marker = "sys_platform == \'linux\'" },\n'
            '  { index = "mirror", marker = "sys_platform != \'linux\'" },\n]\n'
            'ext = [{ index = "mirror" }, { url = "https://example.com/ext.whl" }]\n'
        ),
        "ext/pyproject.toml": '[project]\nname = "ext"\n',
    })
    assert index.unit("python:cli").deps == ("python:core",)


def test_maven_unparsed_or_unresolved_module_falls_back(tmp_path):
    _write(tmp_path, {
        "pom.xml": _POM.format(body=(
            "<groupId>g</groupId><artifactId>root</artifactId><packaging>pom</packaging>"
            "<modules><module>a</module><module>bad</module><module>prop</module></modules>"
        )),
        "a/pom.xml": _POM.format(body=_parent("root", "g") + "<artifactId>a</artifactId>"),
        "a/src/main/java/A.java": "class A {}",
        "prop/pom.xml": _POM.format(body=_parent("root", "g") + "<artifactId>${missing}</artifactId>"),
        "prop/src/main/java/P.java": "class P {}",
    })
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "pom.xml").write_bytes(b"<project><artifactId>caf\xe9</artifactId><oops></project>")
    index = collect_build_units(_ctx(tmp_path))
    assert set(index.units) == {"manifest:bad", "manifest:prop", "maven:g:a", "maven:g:root"}
    assert _owners(index, "prop/src/main/java/P.java") == ["manifest:prop"]


def test_dart_path_deps_and_dev_deps_linked(tmp_path):
    index = _collect(tmp_path, {
        "app/pubspec.yaml": (
            "name: app\n"
            "dependencies:\n"
            "  lib:\n"
            "    path: ../lib\n"
            "dev_dependencies:\n"
            "  testkit:\n"
            "    path: ../testkit\n"
        ),
        "app/lib/main.dart": "void main() {}",
        "lib/pubspec.yaml": "name: lib\n",
        "lib/lib/lib.dart": "class Lib {}\n",
        "testkit/pubspec.yaml": "name: testkit\n",
        "testkit/test_test.dart": "",
    })
    assert _deps(index) == {
        "dart:app": ("dart:lib", "dart:testkit"),
        "dart:lib": (),
        "dart:testkit": (),
    }
    app = index.unit("dart:app")
    assert (app.root, app.manifest, app.name, app.source) == (
        "app", "app/pubspec.yaml", "app", "pubspec"
    )
    assert _owners(index, "app/lib/main.dart") == ["dart:app"]
    assert _owners(index, "lib/lib/lib.dart") == ["dart:lib"]


def test_dart_path_dep_outside_repo_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "app/pubspec.yaml": (
            "name: app\n"
            "dependencies:\n"
            "  other: {path: ../../elsewhere}\n"
        ),
        "elsewhere/pubspec.yaml": "name: other\n",
        "elsewhere/other.dart": "",
        "app/main.dart": "void main() {}",
    })
    # The path dep points outside the repo (../../elsewhere from app/),
    # so it links nothing even though there's a package with that name
    assert _deps(index) == {"dart:app": (), "dart:other": ()}


def test_dart_hosted_dep_named_like_in_repo_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "app/pubspec.yaml": (
            "name: app\n"
            "dependencies:\n"
            "  http: ^1.0.0\n"
        ),
        "http/pubspec.yaml": "name: http\n",
        "http/http.dart": "",
        "app/main.dart": "void main() {}",
    })
    assert _deps(index) == {"dart:app": (), "dart:http": ()}


def test_dart_unparseable_pubspec_falls_back(tmp_path):
    index = _collect(tmp_path, {
        "good/pubspec.yaml": "name: good\n",
        "good/g.dart": "class Good {}\n",
        "bad/pubspec.yaml": "name: [broken\n",
        "bad/b.dart": "class Bad {}\n",
    })
    assert set(index.units) == {"dart:good", "manifest:bad"}
    assert _owners(index, "bad/b.dart") == ["manifest:bad"]


def test_dart_non_utf8_pubspec_falls_back_alone(tmp_path):
    _write(tmp_path, {"good/pubspec.yaml": "name: good\n", "good/g.dart": "class Good {}\n",
                      "bad/b.dart": "class Bad {}\n"})
    (tmp_path / "bad").mkdir(exist_ok=True)
    (tmp_path / "bad/pubspec.yaml").write_bytes(b"name: bad\n# \xff\xfe\n")
    index = collect_build_units(_ctx(tmp_path))
    assert set(index.units) == {"dart:good", "manifest:bad"}
    assert "dart" not in index.failed_ecosystems


def test_dart_pubspec_with_an_impossible_date_falls_back_alone(tmp_path):
    index = _collect(tmp_path, {
        "good/pubspec.yaml": "name: good\n", "good/g.dart": "class Good {}\n",
        "bad/pubspec.yaml": "name: bad\ncreated: 2023-02-30\n", "bad/b.dart": "class Bad {}\n",
    })
    assert set(index.units) == {"dart:good", "manifest:bad"}
    assert "dart" not in index.failed_ecosystems


def test_dart_pub_workspace_root_ruled_out(tmp_path):
    index = _collect(tmp_path, {
        "pubspec.yaml": (
            "environment:\n"
            "  sdk: '>=3.0.0'\n"
            "workspace:\n"
            "  - pkgs/a\n"
            "  - pkgs/b\n"
        ),
        "pkgs/a/pubspec.yaml": (
            "name: a\n"
            "dependencies:\n"
            "  b: {path: ../b}\n"
        ),
        "pkgs/a/a.dart": "class A {}\n",
        "pkgs/b/pubspec.yaml": "name: b\n",
        "pkgs/b/b.dart": "class B {}\n",
    })
    assert set(index.units) == {"dart:a", "dart:b"}
    assert _owners(index, "pubspec.yaml") == []
    assert index.unit("dart:a").deps == ("dart:b",)


def test_dart_duplicate_name_keeps_first_root(tmp_path):
    index = _collect(tmp_path, {
        "a/pubspec.yaml": "name: same\n",
        "a/a.dart": "class A {}\n",
        "b/pubspec.yaml": "name: same\n",
        "b/b.dart": "class B {}\n",
    })
    assert set(index.units) == {"dart:same", "manifest:b"}
    assert index.unit("dart:same").root == "a"
    assert _owners(index, "b/b.dart") == ["manifest:b"]


def test_dart_adapter_beats_fallback(tmp_path):
    index = _collect(tmp_path, {
        "pkg/pubspec.yaml": "name: pkg\n",
        "pkg/lib.dart": "class Pkg {}\n",
    })
    assert set(index.units) == {"dart:pkg"}
    assert _owners(index, "pkg/lib.dart") == ["dart:pkg"]


def test_dart_absolute_path_dep_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "app/pubspec.yaml": (
            "name: app\n"
            "dependencies:\n"
            "  lib: {path: /opt/lib}\n"
        ),
        "app/main.dart": "void main() {}",
    })
    assert _deps(index) == {"dart:app": ()}


def test_dart_root_pubspec_with_path_dep(tmp_path):
    index = _collect(tmp_path, {
        "pubspec.yaml": (
            "name: root\n"
            "dependencies:\n"
            "  core: {path: packages/core}\n"
        ),
        "packages/core/pubspec.yaml": "name: core\n",
        "packages/core/core.dart": "",
        "main.dart": "void main() {}",
    })
    assert _deps(index) == {"dart:root": ("dart:core",), "dart:core": ()}
    root_unit = index.unit("dart:root")
    assert (root_unit.root, root_unit.manifest) == ("", "pubspec.yaml")


_PACKAGE_SWIFT_APP = (
    '// swift-tools-version:5.9\n'
    'import PackageDescription\n\n'
    'let package = Package(\n'
    '    name: "App",\n'
    '    dependencies: [\n'
    '        .package(path: "../Lib"),\n'
    '        .package(url: "https://github.com/apple/swift-log.git", from: "1.0.0"),\n'
    '    ],\n'
    '    targets: [\n'
    '        .executableTarget(name: "App", dependencies: [.product(name: "Lib", package: "Lib")]),\n'
    '    ]\n'
    ')'
)

_PACKAGE_SWIFT_LIB = (
    '// swift-tools-version:5.9\n'
    'import PackageDescription\n\n'
    'let package = Package(\n'
    '    name: "Lib",\n'
    '    targets: [\n'
    '        .target(name: "Lib"),\n'
    '    ]\n'
    ')'
)

_PACKAGE_SWIFT_OTHER = (
    '// swift-tools-version:5.9\n'
    'import PackageDescription\n\n'
    'let package = Package(\n'
    '    name: "Other",\n'
    '    targets: [\n'
    '        .target(name: "Other"),\n'
    '    ]\n'
    ')'
)


def test_swift_packages_and_path_deps(tmp_path):
    index = _collect(tmp_path, {
        "App/Package.swift": _PACKAGE_SWIFT_APP,
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
    })
    assert _deps(index) == {"swift:App": ("swift:Lib",), "swift:Lib": ()}
    app = index.unit("swift:App")
    assert (app.root, app.manifest, app.name, app.source) == ("App", "App/Package.swift", "App", "swiftpm")
    assert _owners(index, "App/Sources/App/main.swift") == ["swift:App"]
    # No manifest:App fallback at the same root.
    assert "manifest:App" not in index.units


def test_swift_path_dep_outside_repo_links_nothing(tmp_path):
    repo = tmp_path / "repo"
    index = _collect(repo, {
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: ['
            '.package(path: "../../../elsewhere/Lib")], targets: [])'
        ),
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
    })
    assert index.unit("swift:App").deps == ()


def test_swift_absolute_path_dep_outside_repo_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: [\n'
            '    .package(path: "/opt/Lib"),\n'
            '    .package(path: "C:/Lib"),\n'
            '], targets: [])'
        ),
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
    })
    assert index.unit("swift:App").deps == ()


def test_swift_older_name_path_form_and_commented_out(tmp_path):
    index = _collect(tmp_path, {
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: [\n'
            '    .package(name: "Lib", path: "../Lib"),\n'
            '    // .package(path: "../Other"),\n'
            '], targets: [])'
        ),
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
        "Other/Package.swift": _PACKAGE_SWIFT_OTHER,
        "Other/Sources/Other/Other.swift": "",
    })
    assert _deps(index) == {"swift:App": ("swift:Lib",), "swift:Lib": (), "swift:Other": ()}
    assert index.unit("swift:Other").root == "Other"


def test_swift_trailing_and_block_comments_hide_deps(tmp_path):
    index = _collect(tmp_path, {
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: [\n'
            '    .package(path: "../Lib"), // .package(path: "../Legacy")\n'
            '    /* .package(path: "../Old") */\n'
            '], targets: [])'
        ),
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
        "Legacy/Package.swift": 'let package = Package(name: "Legacy", targets: [])',
        "Legacy/Sources/Legacy/Legacy.swift": "",
        "Old/Package.swift": _PACKAGE_SWIFT_OTHER,
        "Old/Sources/Other/Other.swift": "",
    })
    assert index.unit("swift:App").deps == ("swift:Lib",)


def test_swift_url_slashes_in_a_string_are_not_a_comment(tmp_path):
    index = _collect(tmp_path, {
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: [\n'
            '    .package(url: "https://example.com/x.git", from: "1.0.0"), .package(path: "../Lib"),\n'
            '], targets: [])'
        ),
        "App/Sources/App/main.swift": "",
        "Lib/Package.swift": _PACKAGE_SWIFT_LIB,
        "Lib/Sources/Lib/Lib.swift": "",
    })
    assert index.unit("swift:App").deps == ("swift:Lib",)


def test_swift_unreadable_package_swift_falls_back(tmp_path):
    _write(tmp_path, {
        "Good/Package.swift": _PACKAGE_SWIFT_LIB,
        "Good/Sources/Good/Good.swift": "",
    })
    (tmp_path / "Bad").mkdir()
    (tmp_path / "Bad" / "Package.swift").write_bytes(b'let package = Package(name: "caf\xe9")\n')
    (tmp_path / "Bad" / "Sources" / "Bad").mkdir(parents=True)
    (tmp_path / "Bad" / "Sources" / "Bad" / "Bad.swift").write_text("")
    (tmp_path / "NoName").mkdir()
    (tmp_path / "NoName" / "Package.swift").write_text('// No Package() call\n')
    (tmp_path / "NoName" / "Sources" / "NoName").mkdir(parents=True)
    (tmp_path / "NoName" / "Sources" / "NoName" / "NoName.swift").write_text("")
    index = collect_build_units(_ctx(tmp_path))
    assert set(index.units) == {"manifest:Bad", "manifest:NoName", "swift:Lib"}
    assert _owners(index, "Bad/Sources/Bad/Bad.swift") == ["manifest:Bad"]
    assert _owners(index, "NoName/Sources/NoName/NoName.swift") == ["manifest:NoName"]


def test_swift_root_package_with_subpackage_dep(tmp_path):
    index = _collect(tmp_path, {
        "Package.swift": (
            'let package = Package(name: "Root", dependencies: ['
            '.package(path: "Packages/Core")], targets: [])'
        ),
        "Sources/Root/main.swift": "",
        "Packages/Core/Package.swift": (
            'let package = Package(name: "Core", targets: ['
            '.target(name: "Core")])'
        ),
        "Packages/Core/Sources/Core/Core.swift": "",
    })
    assert _deps(index) == {"swift:Core": (), "swift:Root": ("swift:Core",)}
    root = index.unit("swift:Root")
    assert (root.root, root.manifest) == ("", "Package.swift")


def test_swift_duplicate_name_keeps_first_root(tmp_path):
    index = _collect(tmp_path, {
        "a/Dup/Package.swift": (
            'let package = Package(name: "Dup", targets: ['
            '.target(name: "Dup")])'
        ),
        "a/Dup/Sources/Dup/Dup.swift": "",
        "b/Dup/Package.swift": (
            'let package = Package(name: "Dup", targets: ['
            '.target(name: "Dup")])'
        ),
        "b/Dup/Sources/Dup/Dup.swift": "",
        "App/Package.swift": (
            'let package = Package(name: "App", dependencies: ['
            '.package(path: "../b/Dup")], targets: [.target(name: "App")])'
        ),
        "App/Sources/App/main.swift": "",
    })
    assert index.unit("swift:Dup").root == "a/Dup"
    assert index.unit("swift:App").deps == ()
    assert _owners(index, "b/Dup/Sources/Dup/Dup.swift") == ["manifest:b/Dup"]


def test_php_composer_path_repository_deps(tmp_path):
    index = _collect(tmp_path, {
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"acme/lib": "*"},
            "repositories": [{"type": "path", "url": "../lib"}],
        }),
        "app/src/App.php": "<?php namespace Acme\\App; class App {}",
        "lib/composer.json": json.dumps({"name": "acme/lib"}),
        "lib/src/Lib.php": "<?php namespace Acme\\Lib; class Lib {}",
    })
    assert _deps(index) == {
        "php:acme/app": ("php:acme/lib",),
        "php:acme/lib": (),
    }
    app = index.unit("php:acme/app")
    assert (app.root, app.manifest, app.name, app.source) == ("app", "app/composer.json", "acme/app", "composer")
    assert _owners(index, "app/src/App.php") == ["php:acme/app"]
    assert _owners(index, "lib/src/Lib.php") == ["php:acme/lib"]


def test_php_require_matches_package_name_in_any_case(tmp_path):
    index = _collect(tmp_path, {
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"Acme/Lib": "*"},
            "repositories": [{"type": "path", "url": "../lib"}],
        }),
        "app/src/App.php": "<?php namespace Acme\App; class App {}",
        "lib/composer.json": json.dumps({"name": "acme/lib"}),
        "lib/src/Lib.php": "<?php namespace Acme\Lib; class Lib {}",
    })
    assert _deps(index)["php:acme/app"] == ("php:acme/lib",)


def test_php_glob_never_names_the_repo_root(tmp_path):
    index = _collect(tmp_path, {
        "composer.json": json.dumps({"name": "acme/root"}),
        "src/Root.php": "<?php namespace Acme\Root; class Root {}",
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"acme/root": "*"},
            "repositories": [{"type": "path", "url": "../*"}],
        }),
        "app/src/App.php": "<?php namespace Acme\App; class App {}",
    })
    assert _deps(index)["php:acme/app"] == ()


def test_php_case_variant_name_is_a_duplicate(tmp_path):
    index = _collect(tmp_path, {
        "a/composer.json": json.dumps({"name": "acme/lib"}),
        "a/src/A.php": "<?php namespace Acme\A; class A {}",
        "b/composer.json": json.dumps({"name": "Acme/Lib"}),
        "b/src/B.php": "<?php namespace Acme\B; class B {}",
    })
    assert [u for u in index.units if u.startswith("php:")] == ["php:acme/lib"]


def test_php_unnamed_dirs_differing_in_case_stay_apart(tmp_path):
    (tmp_path / "Probe").mkdir()
    if (tmp_path / "probe").exists():
        pytest.skip("case-insensitive filesystem")
    (tmp_path / "Probe").rmdir()
    index = _collect(tmp_path, {
        "Lib/composer.json": json.dumps({}),
        "Lib/src/A.php": "<?php namespace A; class A {}",
        "lib/composer.json": json.dumps({}),
        "lib/src/B.php": "<?php namespace B; class B {}",
    })
    assert sorted(u for u in index.units if u.startswith("php:")) == ["php:Lib", "php:lib"]


def test_php_path_repository_outside_repo_links_nothing(tmp_path):
    repo = tmp_path / "repo"
    index = _collect(repo, {
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"acme/lib": "*"},
            "repositories": [{"type": "path", "url": "../../elsewhere/lib"}],
        }),
        "app/src/App.php": "<?php namespace Acme\\App; class App {}",
        "lib/composer.json": json.dumps({"name": "acme/lib"}),
        "lib/src/Lib.php": "<?php namespace Acme\\Lib; class Lib {}",
    })
    assert _deps(index) == {
        "php:acme/app": (),
        "php:acme/lib": (),
    }
    assert set(index.units) == {"php:acme/app", "php:acme/lib"}


def test_php_required_without_path_repo_links_nothing(tmp_path):
    index = _collect(tmp_path, {
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"acme/lib": "*"},
        }),
        "app/src/App.php": "<?php namespace Acme\\App; class App {}",
        "lib/composer.json": json.dumps({"name": "acme/lib"}),
        "lib/src/Lib.php": "<?php namespace Acme\\Lib; class Lib {}",
    })
    assert _deps(index) == {
        "php:acme/app": (),
        "php:acme/lib": (),
    }


def test_php_glob_path_repository(tmp_path):
    index = _collect(tmp_path, {
        "composer.json": json.dumps({
            "name": "acme/root",
            "require": {"acme/a": "*", "acme/b": "*"},
            "repositories": [{"type": "path", "url": "packages/*"}],
        }),
        "src/Root.php": "<?php namespace Acme\\Root; class Root {}",
        "packages/a/composer.json": json.dumps({"name": "acme/a"}),
        "packages/a/src/A.php": "<?php namespace Acme\\A; class A {}",
        "packages/b/composer.json": json.dumps({"name": "acme/b"}),
        "packages/b/src/B.php": "<?php namespace Acme\\B; class B {}",
        "packages/c/composer.json": json.dumps({"name": "acme/c"}),
        "packages/c/src/C.php": "<?php namespace Acme\\C; class C {}",
    })
    assert _deps(index) == {
        "php:acme/root": ("php:acme/a", "php:acme/b"),
        "php:acme/a": (),
        "php:acme/b": (),
        "php:acme/c": (),
    }
    assert _owners(index, "packages/a/src/A.php") == ["php:acme/a"]
    assert _owners(index, "packages/c/src/C.php") == ["php:acme/c"]


def test_php_glob_character_class(tmp_path):
    index = _collect(tmp_path, {
        "composer.json": json.dumps({
            "name": "acme/root",
            "require": {"acme/a": "*", "acme/b": "*", "acme/c": "*"},
            "repositories": [{"type": "path", "url": "packages/[ab]"}],
        }),
        "src/Root.php": "<?php namespace Acme\\Root; class Root {}",
        "packages/a/composer.json": json.dumps({"name": "acme/a"}),
        "packages/a/src/A.php": "<?php namespace Acme\\A; class A {}",
        "packages/b/composer.json": json.dumps({"name": "acme/b"}),
        "packages/b/src/B.php": "<?php namespace Acme\\B; class B {}",
        "packages/c/composer.json": json.dumps({"name": "acme/c"}),
        "packages/c/src/C.php": "<?php namespace Acme\\C; class C {}",
    })
    assert index.unit("php:acme/root").deps == ("php:acme/a", "php:acme/b")


def test_php_glob_star_stays_in_one_segment(tmp_path):
    index = _collect(tmp_path, {
        "composer.json": json.dumps({
            "name": "acme/root",
            "require": {"acme/nested": "*", "acme/deeper": "*"},
            "repositories": [{"type": "path", "url": "packages/*"}],
        }),
        "src/Root.php": "<?php namespace Acme\\Root; class Root {}",
        "packages/nested/composer.json": json.dumps({"name": "acme/nested"}),
        "packages/nested/src/Nested.php": "<?php namespace Acme\\Nested; class Nested {}",
        "packages/nested/deeper/composer.json": json.dumps({"name": "acme/deeper"}),
        "packages/nested/deeper/src/Deep.php": "<?php namespace Acme\\Deep; class Deep {}",
    })
    assert index.unit("php:acme/root").deps == ("php:acme/nested",)
    assert "php:acme/deeper" in index.units


def test_php_unparseable_composer_falls_back(tmp_path):
    index = _collect(tmp_path, {
        "good/composer.json": json.dumps({"name": "acme/good"}),
        "good/Good.php": "<?php namespace Acme\\Good; class Good {}",
        "bad/composer.json": "{not json",
        "bad/Bad.php": "<?php namespace Acme\\Bad; class Bad {}",
    })
    assert set(index.units) == {"php:acme/good", "manifest:bad"}
    assert _owners(index, "good/Good.php") == ["php:acme/good"]
    assert _owners(index, "bad/Bad.php") == ["manifest:bad"]


def test_php_unnamed_composer_gets_dir_id(tmp_path):
    index = _collect(tmp_path, {
        "composer.json": json.dumps({"require": {}}),
        "src/Main.php": "<?php class Main {}",
        "packages/pkg/composer.json": json.dumps({"require": {}}),
        "packages/pkg/Pkg.php": "<?php class Pkg {}",
    })
    assert set(index.units) == {"php:.", "php:packages/pkg"}
    assert index.unit("php:.").name == "."
    assert index.unit("php:packages/pkg").name == "pkg"
    assert _owners(index, "src/Main.php") == ["php:."]
    assert _owners(index, "packages/pkg/Pkg.php") == ["php:packages/pkg"]


def test_php_unnamed_package_never_becomes_dep(tmp_path):
    index = _collect(tmp_path, {
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"pkg": "*"},
            "repositories": [{"type": "path", "url": "../packages/pkg"}],
        }),
        "app/App.php": "<?php class App {}",
        "packages/pkg/composer.json": json.dumps({"require": {}}),
        "packages/pkg/Pkg.php": "<?php class Pkg {}",
    })
    # An unnamed package has no name a require can use.
    assert index.unit("php:acme/app").deps == ()
    assert set(index.units) == {"php:acme/app", "php:packages/pkg"}


def test_php_duplicate_name_keeps_the_first_root(tmp_path):
    index = _collect(tmp_path, {
        "a/composer.json": json.dumps({"name": "acme/dup"}),
        "a/A.php": "<?php class A {}",
        "b/dup/composer.json": json.dumps({"name": "acme/dup"}),
        "b/dup/B.php": "<?php class B {}",
        "app/composer.json": json.dumps({
            "name": "acme/app",
            "require": {"acme/dup": "*"},
            "repositories": [{"type": "path", "url": "../b/dup"}],
        }),
        "app/App.php": "<?php class App {}",
    })
    # The first (in sorted order by rel_dir) root "a" wins the id "php:acme/dup"
    assert index.unit("php:acme/dup").root == "a"
    # Sorted by root, "a" comes first; "b/dup" falls back.
    assert "manifest:b/dup" in index.units
    assert index.unit("php:acme/app").deps == ()
    assert _owners(index, "b/dup/B.php") == ["manifest:b/dup"]


_PAS = "unit U;\ninterface\nimplementation\nend.\n"
_DPR = "program P;\nbegin\nend.\n"


def _dpk(requires: str = "rtl", contains: str = "U") -> str:
    return f"package P;\nrequires\n  {requires};\ncontains\n  {contains};\nend.\n"


def _lpk(name: str, files: str = "", required: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><CONFIG><Package Version="5">'
        f'<PathDelim Value="\\"/><Name Value="{name}"/>'
        f"<Files>{files}</Files><RequiredPkgs>{required}</RequiredPkgs>"
        "</Package></CONFIG>"
    )


def _pascal_ctx(root: Path, files: dict[str, str]) -> ResolverContext:
    """A context indexing what the traverser indexes: no project option files, no ``.lpk``."""
    _write(root, files)
    indexed = {rel for rel in files if not rel.lower().endswith((".dproj", ".lpi", ".lpk"))}
    return ResolverContext(
        path_set=indexed, stem_map=build_stem_map(indexed), graph=nx.DiGraph(), repo_path=root
    )


def _collect_pascal(root: Path, files: dict[str, str]):
    return collect_build_units(_pascal_ctx(root, files))


def test_pascal_projects_packages_requires_and_listed_files(tmp_path):
    index = _collect_pascal(tmp_path, {
        "core/CoreLib.dpk": _dpk(contains="Core.Utils"),
        "core/Core.Utils.pas": _PAS,
        "ui/UiKit.dpk": _dpk(requires="rtl, vcl, CoreLib", contains="Ui.Forms"),
        "ui/Ui.Forms.pas": _PAS,
        "app/App.dpr": "program App;\nuses\n  Main,\n  Core.Utils in '..\\core\\Core.Utils.pas';\nbegin\nend.\n",
        "app/App.dproj": "<Project/>",
        "app/Main.pas": _PAS,
    })
    assert _deps(index) == {
        "pascal:App": (),
        "pascal:CoreLib": (),
        "pascal:UiKit": ("pascal:CoreLib",),
    }
    app = index.unit("pascal:App")
    assert (app.ecosystem, app.name, app.root, app.manifest, app.source) == (
        "pascal", "App", "app", "app/App.dpr", "pascal",
    )
    assert _owners(index, "core/Core.Utils.pas") == ["pascal:App", "pascal:CoreLib"]
    assert _owners(index, "app/Main.pas") == ["pascal:App"]
    assert _owners(index, "app/App.dpr") == ["pascal:App"]
    assert [u.id for u in index.dependents("pascal:CoreLib")] == ["pascal:UiKit"]


def test_pascal_requires_ignores_case_comments_and_conditional_markers(tmp_path):
    index = _collect_pascal(tmp_path, {
        "a/Base.dpk": _dpk(),
        "a/U.pas": _PAS,
        "b/Off.dpk": _dpk(),
        "b/U.pas": _PAS,
        "c/Top.dpk": _dpk(requires="rtl, {$IFDEF X} BASE, {$ENDIF} // Off,\n  { Off, } (* Off *) vcl"),
        "c/U.pas": _PAS,
    })
    assert index.unit("pascal:Top").deps == ("pascal:Base",)


def test_lazarus_package_is_named_by_its_manifest_and_lists_its_files(tmp_path):
    index = _collect_pascal(tmp_path, {
        "base/base.lpk": _lpk(
            "BaseLib",
            '<Item><Filename Value="src\\Base.pas"/><UnitName Value="Base"/></Item>',
            '<Item><PackageName Value="LCL"/></Item>',
        ),
        "base/src/Base.pas": _PAS,
        "base/src/Loose.pas": _PAS,
        "top/Top.lpk": _lpk(
            "Top",
            '<Item1><Filename Value="..\\shared\\Top.pas"/></Item1>',
            '<Item1><PackageName Value="baselib"/></Item1><Item2><PackageName Value="FCL"/></Item2>',
        ),
        "shared/Top.pas": _PAS,
    })
    assert _deps(index) == {"pascal:BaseLib": (), "pascal:Top": ("pascal:BaseLib",)}
    assert index.unit("pascal:BaseLib").manifest == "base/base.lpk"
    assert _owners(index, "shared/Top.pas") == ["pascal:Top"]
    # Not in the package's file list, so owned through the package root.
    assert _owners(index, "base/src/Loose.pas") == ["pascal:BaseLib"]


def test_lazarus_package_without_indexed_files_is_no_unit(tmp_path):
    index = _collect_pascal(tmp_path, {
        "ignored/Old.lpk": _lpk("Old", '<Item><Filename Value="Gone.pas"/></Item>'),
        "app/App.lpr": _DPR,
    })
    assert list(index.units) == ["pascal:App"]


def test_lpk_paths_that_hold_no_package_are_no_units(tmp_path):
    (tmp_path / "a/Folder.lpk").mkdir(parents=True)
    index = _collect_pascal(tmp_path, {
        "a/App.lpr": _DPR,
        "a/NoPackage.lpk": "<CONFIG/>",
    })
    assert list(index.units) == ["pascal:App"]


def test_lazarus_package_lists_its_non_pascal_files(tmp_path):
    index = _collect_pascal(tmp_path, {
        "laz/Laz.lpk": _lpk(
            "Laz",
            '<Item><Filename Value="forms\\Main.lfm"/></Item>'
            '<Item><Filename Value="Main.pas"/></Item>',
        ),
        "laz/forms/Main.lfm": "object Main: TForm\nend\n",
        "laz/Main.pas": _unit(),
        "other/Main.lfm": "object Main: TForm\nend\n",
    })
    assert _owners(index, "laz/forms/Main.lfm") == ["pascal:Laz"]
    assert _owners(index, "other/Main.lfm") == []


def test_pascal_projects_sharing_a_name_all_stay_units(tmp_path):
    index = _collect_pascal(tmp_path, {
        "demo1/Project1.dpr": _DPR,
        "demo1/One.pas": _PAS,
        "demo2/project1.dpr": _DPR,
        "demo2/Two.pas": _PAS,
        "tool/Tool.dpr": "program Tool;\nuses\n  A in 'delphi\\A.pas';\nbegin\nend.\n",
        "tool/delphi/A.pas": _PAS,
        "tool/Tool.lpr": "program Tool;\nuses\n  B in 'fpc\\B.pas';\nbegin\nend.\n",
        "tool/fpc/B.pas": _PAS,
    })
    assert set(index.units) == {
        "pascal:demo1/Project1.dpr",
        "pascal:demo2/project1.dpr",
        "pascal:tool/Tool.dpr",
        "pascal:tool/Tool.lpr",
    }
    assert index.unit("pascal:demo2/project1.dpr").name == "project1"
    assert _owners(index, "demo2/Two.pas") == ["pascal:demo2/project1.dpr"]
    assert _owners(index, "tool/delphi/A.pas") == ["pascal:tool/Tool.dpr"]
    assert _owners(index, "tool/fpc/B.pas") == ["pascal:tool/Tool.lpr"]


def test_pascal_requires_prefers_the_same_kind_then_the_same_directory(tmp_path):
    index = _collect_pascal(tmp_path, {
        "d10/Base.dpk": _dpk(),
        "d10/Top.dpk": _dpk(requires="Base"),
        "d10/U.pas": _PAS,
        "d11/Base.dpk": _dpk(),
        "d11/Top.dpk": _dpk(requires="Base"),
        "d11/U.pas": _PAS,
        "fpc/Base.lpk": _lpk("Base", '<Item><Filename Value="U.pas"/></Item>'),
        "fpc/Top.lpk": _lpk(
            "Top", '<Item><Filename Value="U.pas"/></Item>', '<Item><PackageName Value="Base"/></Item>'
        ),
        "fpc/U.pas": _PAS,
        "other/Use.dpk": _dpk(requires="Base"),
        "other/U.pas": _PAS,
    })
    assert index.unit("pascal:d10/Top.dpk").deps == ("pascal:d10/Base.dpk",)
    assert index.unit("pascal:d11/Top.dpk").deps == ("pascal:d11/Base.dpk",)
    assert index.unit("pascal:fpc/Top.lpk").deps == ("pascal:fpc/Base.lpk",)
    # No package of that name beside it: every same-kind candidate may be the one built.
    assert index.unit("pascal:Use").deps == ("pascal:d10/Base.dpk", "pascal:d11/Base.dpk")


def test_unreadable_pascal_project_leaves_its_files_to_the_deepest_other_unit(tmp_path):
    ctx = _pascal_ctx(tmp_path, {
        "a/Good.dpr": _DPR,
        "a/bad/U.pas": _PAS,
        "a/broken/U.pas": _PAS,
        "a/broken/Broken.lpk": "<CONFIG><Package>",
    })
    ctx.path_set.add("a/bad/Gone.dpr")
    index = collect_build_units(ctx)
    assert list(index.units) == ["pascal:Good"]
    assert index.failed_ecosystems == frozenset()
    assert _owners(index, "a/bad/U.pas") == ["pascal:Good"]
    assert _owners(index, "a/broken/U.pas") == ["pascal:Good"]


def test_pascal_project_files_are_read_in_their_own_encoding(tmp_path):
    ctx = _pascal_ctx(tmp_path, {
        "base/Base.dpk": _dpk(),
        "base/U.pas": _PAS,
        "wide/Wide.dpk": "",
        "wide/U.pas": _PAS,
        "be/Be.dpk": "",
        "be/U.pas": _PAS,
        "bom/Bom.dpk": "",
        "bom/U.pas": _PAS,
    })
    text = _dpk(requires="rtl, Base")
    (tmp_path / "wide/Wide.dpk").write_bytes(text.encode("utf-16"))
    (tmp_path / "be/Be.dpk").write_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
    (tmp_path / "bom/Bom.dpk").write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    index = collect_build_units(ctx)
    for unit_id in ("pascal:Wide", "pascal:Be", "pascal:Bom"):
        assert index.unit(unit_id).deps == ("pascal:Base",)


_DPROJ_PACKAGES = (
    "<Project><PropertyGroup>{flag}<DCC_UsePackage>rtl;CoreLib;$(DCC_UsePackage)</DCC_UsePackage>"
    "</PropertyGroup></Project>"
)


def test_delphi_program_links_packages_only_when_built_with_them(tmp_path):
    index = _collect_pascal(tmp_path, {
        "core/CoreLib.dpk": _dpk(),
        "core/U.pas": _PAS,
        "dyn/Dyn.dpr": _DPR,
        "dyn/Dyn.dproj": _DPROJ_PACKAGES.format(flag="<UsePackages>true</UsePackages>"),
        "off/Off.dpr": _DPR,
        "off/Off.dproj": _DPROJ_PACKAGES.format(flag="<UsePackages>false</UsePackages>"),
        "plain/Plain.dpr": _DPR,
        "plain/Plain.dproj": _DPROJ_PACKAGES.format(flag=""),
    })
    assert index.unit("pascal:Dyn").deps == ("pascal:CoreLib",)
    assert index.unit("pascal:Off").deps == ()
    assert index.unit("pascal:Plain").deps == ()


def test_lazarus_program_links_the_packages_its_project_requires(tmp_path):
    index = _collect_pascal(tmp_path, {
        "lib/base.lpk": _lpk("BaseLib", '<Item><Filename Value="U.pas"/></Item>'),
        "lib/U.pas": _PAS,
        "delphi/BaseLib.dpk": _dpk(),
        "delphi/U.pas": _PAS,
        "app/App.lpr": _DPR,
        "app/App.lpi": (
            '<CONFIG><ProjectOptions><RequiredPackages Count="2">'
            '<Item1><PackageName Value="LCL"/></Item1>'
            '<Item2><PackageName Value="baselib"/></Item2>'
            "</RequiredPackages></ProjectOptions></CONFIG>"
        ),
    })
    assert index.unit("pascal:App").deps == ("pascal:lib/base.lpk",)


def _program(uses: str) -> str:
    return f"program P;\nuses\n  {uses};\nbegin\nend.\n"


def _unit(uses: str = "", implementation_uses: str = "") -> str:
    interface = f"uses {uses};\n" if uses else ""
    implementation = f"uses {implementation_uses};\n" if implementation_uses else ""
    return f"unit U;\ninterface\n{interface}implementation\n{implementation}end.\n"


def test_pascal_projects_list_only_the_units_they_reach_through_uses(tmp_path):
    index = _collect_pascal(tmp_path, {
        "src/One.dpr": _program("A"),
        "src/Two.dpr": _program("B"),
        "src/A.pas": _unit("Shared"),
        "src/B.pas": _unit(implementation_uses="Shared"),
        "src/Shared.pas": _unit(),
        "src/Unused.pas": _unit(),
    })
    assert _owners(index, "src/A.pas") == ["pascal:One"]
    assert _owners(index, "src/B.pas") == ["pascal:Two"]
    assert _owners(index, "src/Shared.pas") == ["pascal:One", "pascal:Two"]
    # Nothing reaches it, so it is owned through the shared root.
    assert _owners(index, "src/Unused.pas") == ["pascal:One", "pascal:Two"]


def test_pascal_project_reaches_units_outside_its_search_paths(tmp_path):
    index = _collect_pascal(tmp_path, {
        "src/App.dpr": _program("Core"),
        "src/core/Core.pas": _unit(implementation_uses="Deep"),
        "src/core/deep/Deep.pas": _unit(),
        "src/tests/Tests.dpr": _program("Core"),
    })
    assert _owners(index, "src/core/Core.pas") == ["pascal:App", "pascal:Tests"]
    assert _owners(index, "src/core/deep/Deep.pas") == ["pascal:App", "pascal:Tests"]


def test_pascal_search_path_picks_a_variant_and_an_unplaced_name_keeps_all(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/Win.dpr": _program("uPty"),
        "app/Win.dproj": (
            "<Project><PropertyGroup><DCC_UnitSearchPath>..\\win</DCC_UnitSearchPath>"
            "</PropertyGroup></Project>"
        ),
        "tool/Any.dpr": _program("uPty"),
        "win/uPty.pas": _unit(),
        "posix/uPty.pas": _unit(),
    })
    assert _owners(index, "win/uPty.pas") == ["pascal:Any", "pascal:Win"]
    assert _owners(index, "posix/uPty.pas") == ["pascal:Any"]


def test_pascal_unique_unit_settles_which_same_named_variant_a_project_builds(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("uPty, OnlyPosix"),
        "platform/posix/OnlyPosix.pas": _unit(),
        "platform/posix/uPty.pas": _unit("uChained"),
        "platform/posix/uChained.pas": _unit(),
        "platform/win/uPty.pas": _unit("OnlyWin, uChained"),
        "platform/win/uChained.pas": _unit(),
        "platform/win/OnlyWin.pas": _unit(),
    })
    assert _owners(index, "platform/posix/uPty.pas") == ["pascal:App"]
    assert _owners(index, "platform/posix/uChained.pas") == ["pascal:App"]
    for name in ("uPty", "uChained", "OnlyWin"):
        assert _owners(index, f"platform/win/{name}.pas") == []


def test_pascal_project_reaching_both_variant_directories_keeps_both(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("uPty, OnlyPosix, OnlyWin"),
        "posix/OnlyPosix.pas": _unit(),
        "posix/uPty.pas": _unit(),
        "win/OnlyWin.pas": _unit(),
        "win/uPty.pas": _unit(),
    })
    assert _owners(index, "posix/uPty.pas") == ["pascal:App"]
    assert _owners(index, "win/uPty.pas") == ["pascal:App"]


def test_pascal_uses_walk_survives_cycles_and_ignores_text_that_only_looks_like_uses(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("A"),
        "app/A.pas": (
            "unit A;\ninterface\nuses B;\nimplementation\n"
            "// uses InLine;\n{ uses InBrace; }\n"
            "const S = 'uses InString;';\nvar Uses: Integer;\nbegin\n  Uses := 1;\nend.\n"
        ),
        "app/B.pas": _unit(implementation_uses="A"),
        "other/InLine.pas": _unit(),
        "other/InBrace.pas": _unit(),
        "other/InString.pas": _unit(),
    })
    assert _owners(index, "app/B.pas") == ["pascal:App"]
    for name in ("InLine", "InBrace", "InString"):
        assert _owners(index, f"other/{name}.pas") == []


def test_pascal_uses_keeps_every_unit_of_a_clause_with_conditional_branches(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("{$IFDEF WIN} WinSide {$ELSE} PosixSide {$ENDIF}, Common"),
        "lib/WinSide.pas": _unit(),
        "lib/PosixSide.pas": _unit(),
        "lib/Common.pas": _unit(),
    })
    for name in ("WinSide", "PosixSide", "Common"):
        assert _owners(index, f"lib/{name}.pas") == ["pascal:App"]


def test_pascal_uses_keeps_the_clause_around_an_in_path_with_an_apostrophe(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("Quoted in 'O''Brien.pas', Plain"),
        "app/O'Brien.pas": _unit(),
        "lib/Plain.pas": _unit(),
    })
    assert _owners(index, "app/O'Brien.pas") == ["pascal:App"]
    assert _owners(index, "lib/Plain.pas") == ["pascal:App"]


def test_pascal_in_path_needs_no_space_after_the_keyword(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": _program("Named in'sub\\Named.pas', Plain"),
        "app/sub/Named.pas": _unit(),
        "other/Named.pas": _unit(),
        "lib/Plain.pas": _unit(),
    })
    assert _owners(index, "app/sub/Named.pas") == ["pascal:App"]
    assert _owners(index, "other/Named.pas") == []
    assert _owners(index, "lib/Plain.pas") == ["pascal:App"]


def test_pascal_expressions_on_identifiers_named_uses_link_nothing(tmp_path):
    index = _collect_pascal(tmp_path, {
        "app/App.dpr": (
            "program P;\nvar Uses, Contains: Integer;\nbegin\n"
            "  if Contains then InThen;\n  Run(Uses - InMinus);\n  Uses := Contains + InPlus;\nend.\n"
        ),
        "lib/InThen.pas": _unit(),
        "lib/InMinus.pas": _unit(),
        "lib/InPlus.pas": _unit(),
    })
    for name in ("InThen", "InMinus", "InPlus"):
        assert _owners(index, f"lib/{name}.pas") == []


def test_pascal_package_walks_its_contains_and_lazarus_its_file_list(tmp_path):
    index = _collect_pascal(tmp_path, {
        "pkg/Lib.dpk": _dpk(contains="Api"),
        "pkg/Api.pas": _unit("Inner"),
        "src/Inner.pas": _unit(),
        "laz/Laz.lpk": _lpk("Laz", '<Item><Filename Value="Front.pas"/></Item>'),
        "laz/Front.pas": _unit("Back"),
        "shared/Back.pas": _unit(),
    })
    assert _owners(index, "src/Inner.pas") == ["pascal:Lib"]
    assert _owners(index, "shared/Back.pas") == ["pascal:Laz"]


def test_repo_without_pascal_never_builds_the_pascal_index(tmp_path, monkeypatch):
    def boom(_ctx):
        raise AssertionError("index built for an absent ecosystem")

    monkeypatch.setattr(pascal_adapter, "get_pascal_index", boom)
    index = _collect_pascal(tmp_path, {"pkg/Gemfile": "", "pkg/Stray.lpk": _lpk("Stray")})
    assert list(index.units) == ["manifest:pkg"]
    assert index.failed_ecosystems == frozenset()


def test_a_pip_application_in_a_subdirectory_is_a_unit(tmp_path):
    """A requirements file and no pyproject still makes its directory a unit."""
    index = _collect(tmp_path, {
        "backend/requirements.txt": "fastapi\n",
        "backend/requirements-dev.txt": "pytest\n",
        "backend/app.py": "",
        "frontend/package.json": json.dumps({"name": "web"}),
    })
    assert index.unit("manifest:backend").manifest == "backend/requirements-dev.txt"
    assert _owners(index, "backend/app.py") == ["manifest:backend"]
