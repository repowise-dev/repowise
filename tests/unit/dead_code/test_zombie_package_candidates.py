"""A zombie package is a real package nothing imports.

A top-level folder is judged only when it declares itself a package with a
manifest of its own, holds a source file that is not a test, and ships no
program: a Next.js ``app/``, a ``benchmarks/`` folder, a test suite or a CLI
started by its shebang is run, never imported.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph


def _zombies(
    nodes: dict[str, dict],
    source: dict[str, bytes] | None = None,
    repo_root: Path | None = None,
) -> set[str]:
    nodes.setdefault("lib/index.ts", {"symbols": []})
    report = DeadCodeAnalyzer(
        _build_graph(nodes), git_meta_map={}, source_map=source, repo_root=repo_root
    ).analyze(
        {
            "detect_unreachable_files": False,
            "detect_unused_exports": False,
            "detect_unused_internals": False,
            "min_confidence": 0.0,
        }
    )
    return {f.file_path for f in report.findings if f.kind is DeadCodeKind.ZOMBIE_PACKAGE}


def _manifest() -> dict:
    return {"language": "json", "symbols": []}


def test_a_folder_without_a_manifest_is_not_a_package():
    nodes = {
        "app/page.tsx": {"symbols": []},
        "types/index.d.ts": {"symbols": []},
        "benchmarks/run.ts": {"symbols": []},
    }
    assert _zombies(nodes) == set()


def test_an_unimported_package_with_a_manifest_is_a_zombie():
    nodes = {"old-lib/package.json": _manifest(), "old-lib/src/index.ts": {"symbols": []}}
    assert _zombies(nodes) == {"old-lib"}


def test_a_manifest_read_from_disk_counts(tmp_path: Path):
    # Maven and Gradle manifests are not indexed; the checkout still has them.
    (tmp_path / "api-client").mkdir()
    (tmp_path / "api-client" / "pom.xml").write_text("<project/>", encoding="utf-8")
    nodes = {"api-client/src/Client.java": {"language": "java", "symbols": []}}
    assert _zombies(nodes, repo_root=tmp_path) == {"api-client"}


def test_a_dotnet_project_file_is_a_manifest(tmp_path: Path):
    (tmp_path / "Old.Lib").mkdir()
    (tmp_path / "Old.Lib" / "Old.Lib.csproj").write_text("<Project/>", encoding="utf-8")
    nodes = {"Old.Lib/Thing.cs": {"language": "csharp", "symbols": []}}
    assert _zombies(nodes, repo_root=tmp_path) == {"Old.Lib"}


def test_a_package_of_tests_is_run_not_imported():
    nodes = {
        "runtime-tests/package.json": _manifest(),
        "runtime-tests/node/index.test.ts": {"is_test": True, "symbols": []},
    }
    assert _zombies(nodes) == set()


def test_a_package_shipping_a_program_is_run_not_imported():
    nodes = {
        "evals/package.json": _manifest(),
        "evals/src/cli.ts": {"symbols": []},
        "evals/src/report.ts": {"symbols": []},
    }
    source = {"evals/src/cli.ts": b"#!/usr/bin/env node\nmain()\n", "evals/src/report.ts": b""}
    assert _zombies(nodes, source) == set()


def test_a_rust_inner_attribute_is_not_a_shebang():
    from repowise.core.analysis.dead_code.entry_shape import is_program

    assert is_program("lib.rs", b'#![doc(html_root_url = "https://docs.rs/x")]\npub fn f() {}\n') is False
    assert is_program("bin/run", b"#!/usr/bin/env node\nmain()\n") is True
