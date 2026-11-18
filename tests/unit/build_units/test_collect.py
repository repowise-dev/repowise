"""collect_build_units over small on-disk repos, one ecosystem at a time."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.build_units.adapters import cargo as cargo_adapter
from repowise.core.build_units.adapters import dotnet as dotnet_adapter
from repowise.core.build_units.adapters import go as go_adapter
from repowise.core.build_units.adapters import maven as maven_adapter
from repowise.core.build_units.adapters import python as python_adapter
from repowise.core.build_units.collect import collect_build_units
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
    index = _collect(tmp_path, {
        "pkg/Gemfile": "",
        "pkg/m.rb": "",
        # Manifests alone do not wake an adapter: there is no source for it to own.
        "pyproject.toml": "[project]\nname = 'root'\n",
        "pom.xml": "<project><groupId>g</groupId><artifactId>a</artifactId></project>",
    })
    assert list(index.units) == ["manifest:.", "manifest:pkg"]
    assert _owners(index, "pkg/m.rb") == ["manifest:pkg"]


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
