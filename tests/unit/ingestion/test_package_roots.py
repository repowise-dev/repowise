"""One answer to "is this directory a package", read off disk.

Three lists used to answer it and disagree: the traverser's hardcoded four, the
registry's every-manifest union, and health's own six. The registry is now the
single source, and package roots are scanned from the filesystem rather than
inferred from the indexed file list — because the traverser drops 18 manifest
names it cannot language-detect, which silently cost Go, Maven, Groovy-Gradle,
Ruby, Scala, Clojure and Erlang monorepos any package attribution at all.

.NET is not among them: its package file is the ``.csproj`` glob, which an
exact-filename list cannot express, so every name C# declares is build
configuration and is excluded as such.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.package_roots import (
    module_for,
    package_manifest_names,
    scan_package_roots,
)
from repowise.core.ingestion.traverser import FileTraverser

# Names the traverser drops as files but which unambiguously declare a package.
# Measured by running FileTraverser over a directory holding one of each.
DROPPED_BUT_REAL = ("go.mod", "pom.xml", "build.gradle", "Gemfile", "build.sbt")


def _write(root: Path, rel: str, body: str = "x = 1\n") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# The single source of truth
# ---------------------------------------------------------------------------


def test_registering_a_language_grants_monorepo_bucketing():
    """A new spec's manifests count as package roots with no second edit.

    The gap this closes: the traverser's list was four hardcoded names, so
    adding a language to the registry did nothing for monorepo detection and
    the omission was invisible. ``build_config_manifests`` defaults to empty
    precisely so the default is "works".
    """
    names = package_manifest_names()
    for spec in REGISTRY._specs.values():
        expected = set(spec.manifest_files) - set(spec.build_config_manifests)
        assert expected <= names, spec.tag


def test_build_config_is_excluded_because_it_is_not_a_package():
    """The registry union is not usable directly, which is why this subtracts.

    ``vite.config.js`` is the entry usually cited, but the measured offender is
    the .NET family: 135 directories across the repos censused hold a
    ``Directory.Build.props`` / ``global.json`` / ``nuget.config`` and no
    package at all. Each would be a false root, and a false root fragments the
    module rollup.
    """
    names = package_manifest_names()
    for config_only in (
        "vite.config.js",
        "nuxt.config.ts",
        "svelte.config.js",
        "Directory.Build.props",
        "Directory.Packages.props",
        "global.json",
        "nuget.config",
        "lean-toolchain",
        "Setup.hs",
    ):
        assert config_only in REGISTRY.manifest_filenames(), config_only
        assert config_only not in names, config_only


def test_the_traverser_reads_the_same_list():
    """Not a third copy: monorepo detection and health share one source."""
    from repowise.core.ingestion.traverser import _MANIFEST_FILES

    assert package_manifest_names() == _MANIFEST_FILES


# ---------------------------------------------------------------------------
# The disk scan
# ---------------------------------------------------------------------------


def test_the_scan_finds_manifests_the_traverser_drops_as_files(tmp_path):
    """The whole point. These never reach ``parsed_files``, but they are roots."""
    for i, name in enumerate(DROPPED_BUT_REAL):
        _write(tmp_path, f"pkg{i}/{name}", "<x/>\n")
        _write(tmp_path, f"pkg{i}/code.py", "y = 1\n")

    emitted = {fi.path.replace("\\", "/") for fi in FileTraverser(tmp_path).traverse()}
    for i, name in enumerate(DROPPED_BUT_REAL):
        assert f"pkg{i}/{name}" not in emitted, name

    assert scan_package_roots(tmp_path) == {f"pkg{i}" for i in range(len(DROPPED_BUT_REAL))}


def test_a_repo_root_manifest_is_not_a_package_root(tmp_path):
    """Otherwise a plain single-package repo puts every file in one bucket."""
    _write(tmp_path, "go.mod", "module x\n")
    _write(tmp_path, "main.go", "package main\n")
    assert scan_package_roots(tmp_path) == set()


def test_a_directory_with_only_build_config_is_not_a_root(tmp_path):
    _write(tmp_path, "solution/Directory.Build.props", "<Project/>\n")
    _write(tmp_path, "solution/global.json", "{}\n")
    _write(tmp_path, "app/pom.xml", "<project/>\n")
    assert scan_package_roots(tmp_path) == {"app"}


def test_a_nested_package_is_found_at_any_depth(tmp_path):
    """``RepoStructure.packages`` stops at depth 2; module attribution must not.

    ``packages/vscode/webview`` on this repo is depth 3, and a Go module under
    ``services/api/internal/tools`` is depth 4.
    """
    _write(tmp_path, "services/api/go.mod", "module api\n")
    _write(tmp_path, "services/api/internal/tools/go.mod", "module tools\n")
    roots = scan_package_roots(tmp_path)
    assert roots == {"services/api", "services/api/internal/tools"}
    assert module_for("services/api/internal/tools/t.go", roots) == "services/api/internal/tools"
    assert module_for("services/api/main.go", roots) == "services/api"


def test_a_package_with_no_manifest_falls_back_to_its_top_level_directory(tmp_path):
    _write(tmp_path, "libs/billing/pom.xml", "<project/>\n")
    roots = scan_package_roots(tmp_path)
    assert module_for("scripts/deploy/run.py", roots) == "scripts"


def test_the_scan_stops_where_traversal_stops(tmp_path):
    """A vendored or ignored tree must not contribute roots.

    Cost, mostly: a root under ``node_modules`` can never match an analyzed
    file, but walking one is the difference between milliseconds and minutes.
    """
    _write(tmp_path, "app/go.mod", "module app\n")
    _write(tmp_path, "node_modules/dep/package.json", '{"name":"dep"}\n')
    _write(tmp_path, "vendor/lib/go.mod", "module lib\n")
    assert scan_package_roots(tmp_path) == {"app"}


def test_a_nested_gitignore_prunes_the_scan(tmp_path):
    """``dir_chain_skipped`` used to ignore per-directory ignore files.

    It passes ``_should_skip_dir`` the containing directory's spec, and got
    ``None``, so a directory ignored by a nested ``.gitignore`` was reported as
    walkable even though ``_walk`` prunes it. On this repo that admitted
    ``packages/vscode/.vscode-test`` — a downloaded VS Code archive ignored by
    ``packages/vscode/.gitignore`` — and the 500 vendored ``package.json``
    files inside it, taking the scanned-root count from 9 to 512.
    """
    _write(tmp_path, "packages/tool/package.json", '{"name":"tool"}\n')
    _write(tmp_path, "packages/tool/.gitignore", ".test-fixtures/\n")
    _write(tmp_path, "packages/tool/.test-fixtures/vendored/package.json", '{"name":"v"}\n')

    traverser = FileTraverser(tmp_path)
    assert traverser.dir_chain_skipped(Path("packages/tool/.test-fixtures"))
    assert traverser.package_root_dirs() == {"packages/tool"}
