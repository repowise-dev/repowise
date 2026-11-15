"""Package reach: the provider files another repo's imports reach through a package.

A package dependency names no file, so get_risk's ``affected_repos`` and the
dead-code cross-repo cut both need this file-level link before they claim a
file is used across repos. The workspace fixture is two real indexes: one repo
publishes a package, the other imports from its entry and one subpath.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.dead_code.serving import serialize_finding, serve_findings
from repowise.core.workspace.code_api import PublishedPackage
from repowise.core.workspace.manifests import CrossRepoPackageDep
from repowise.core.workspace.package_reach import (
    ProviderGraph,
    attach_reached_files,
    package_link_reaches,
    reached_files,
)
from repowise.core.workspace.repo_index import ExternalImport
from repowise.server.mcp_server._enrichment import CrossRepoEnricher

from ._repo_index import make_repo_index

# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_recorded_reach_is_the_whole_answer() -> None:
    link = {"target_manifest": "pkg/package.json", "reached_files": frozenset({"pkg/a.ts"})}
    assert package_link_reaches(link, "pkg/a.ts")
    assert not package_link_reaches(link, "pkg/b.ts")


def test_without_a_recorded_reach_the_package_directory_counts() -> None:
    link = {"target_manifest": "pkg/package.json"}
    assert package_link_reaches(link, "pkg/b.ts")
    assert not package_link_reaches(link, "other/b.ts")
    assert package_link_reaches({"target_manifest": "pyproject.toml"}, "any/file.py")


# ---------------------------------------------------------------------------
# Reach over a provider graph
# ---------------------------------------------------------------------------

_PY = PublishedPackage(
    name="acme",
    ecosystem="pypi",
    repo="core",
    manifest="pyproject.toml",
    root="",
    import_names=frozenset({"acme"}),
)
_PY_GRAPH = ProviderGraph(
    files=frozenset(
        {
            "src/acme/__init__.py",
            "src/acme/models.py",
            "src/acme/_util.py",
            "src/acme/cli.py",
        }
    ),
    imports={
        "src/acme/__init__.py": [("src/acme/models.py", frozenset({"Thing"}))],
        "src/acme/models.py": [("src/acme/_util.py", frozenset({"helper"}))],
    },
    declared={"src/acme/models.py": frozenset({"Thing"})},
)


def test_a_python_import_reaches_the_module_and_the_file_declaring_the_name() -> None:
    imports = [
        ExternalImport("app.py", "acme", ("Thing",)),
        ExternalImport("app.py", "acme.cli", ("main",)),
        ExternalImport("app.py", "requests", ("get",)),  # another package: ignored
    ]
    assert reached_files(imports, _PY, _PY_GRAPH) == [
        "src/acme/__init__.py",
        "src/acme/cli.py",
        "src/acme/models.py",
    ]


def test_an_import_that_cannot_be_placed_leaves_reach_unknown() -> None:
    imports = [ExternalImport("app.py", "acme.gone", ("x",))]
    assert reached_files(imports, _PY, _PY_GRAPH) is None


def test_a_dependency_with_no_import_of_the_package_is_unknown() -> None:
    assert reached_files([ExternalImport("app.py", "requests", ("get",))], _PY, _PY_GRAPH) is None


def test_a_name_with_no_known_route_reaches_its_module_directory() -> None:
    # A lazy `__getattr__` export: the `__init__` neither declares nor imports it.
    lazy = ProviderGraph(
        files=frozenset({"src/acme/__init__.py", "src/acme/jobs.py", "src/other.py"}),
        imports={},
        declared={},
    )
    imports = [ExternalImport("app.py", "acme", ("run_job",))]
    assert reached_files(imports, _PY, lazy) == ["src/acme/__init__.py", "src/acme/jobs.py"]


# ---------------------------------------------------------------------------
# Two-repo workspace fixture
# ---------------------------------------------------------------------------

_KIT_MANIFEST = {
    "name": "@acme/kit",
    "exports": {".": "./src/index.ts", "./widgets/*": "./src/widgets/*.tsx"},
}
_KIT_FILES = [
    "packages/kit/src/index.ts",
    "packages/kit/src/a.ts",
    "packages/kit/src/b.ts",
    "packages/kit/src/widgets/button.tsx",
]


async def _workspace(root: Path) -> tuple[dict[str, Path], list[CrossRepoPackageDep]]:
    """`lib` publishes @acme/kit; `web` imports its entry barrel and one widget."""
    lib, web = root / "lib", root / "web"
    manifest = lib / "packages" / "kit" / "package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(_KIT_MANIFEST), encoding="utf-8")
    lib_index = await make_repo_index(
        lib,
        {},
        alias="lib",
        graph_nodes=[(path, False) for path in _KIT_FILES],
        # Any `imports` edge with names: the barrel's `export * from "./a"`.
        external_edges=[("packages/kit/src/index.ts", "packages/kit/src/a.ts", ["*"])],
    )
    web_index = await make_repo_index(
        web,
        {},
        alias="web",
        external_edges=[
            ("app/page.tsx", "external:@acme/kit", ["a"]),
            ("app/page.tsx", "external:@acme/kit/widgets/button", ["Button"]),
        ],
    )
    await lib_index.close()
    await web_index.close()
    dep = CrossRepoPackageDep(
        source_repo="web",
        target_repo="lib",
        source_manifest="package.json",
        kind="npm_package",
        target_package="@acme/kit",
        target_manifest="packages/kit/package.json",
    )
    return {"lib": lib, "web": web}, [dep]


async def test_the_workspace_records_the_files_an_import_reaches(tmp_path: Path) -> None:
    repo_paths, deps = await _workspace(tmp_path)
    await attach_reached_files(deps, repo_paths)
    assert deps[0].reached_files == [
        "packages/kit/src/a.ts",
        "packages/kit/src/index.ts",
        "packages/kit/src/widgets/button.tsx",
    ]


async def _enricher(tmp_path: Path) -> CrossRepoEnricher:
    from dataclasses import asdict

    repo_paths, deps = await _workspace(tmp_path)
    await attach_reached_files(deps, repo_paths)
    return CrossRepoEnricher.from_data(
        overlay={"version": 4, "co_changes": [], "package_deps": [asdict(d) for d in deps]}
    )


async def test_only_a_reached_file_has_an_affected_repo(tmp_path: Path) -> None:
    enricher = await _enricher(tmp_path)
    assert enricher.get_affected_repos("lib", "packages/kit/src/a.ts") == ["web"]
    assert enricher.get_affected_repos("lib", "packages/kit/src/b.ts") == []


async def test_get_risk_omits_the_impact_block_without_a_concrete_link(tmp_path: Path) -> None:
    from repowise.server.mcp_server.tool_risk.enrichment import _enrich_target

    enricher = await _enricher(tmp_path)
    states = (enricher.cross_repo_analysis, enricher.contract_analysis)
    cards = {
        path: {"target": path, "relationship_analysis": {}}
        for path in ("packages/kit/src/a.ts", "packages/kit/src/b.ts")
    }
    for card in cards.values():
        _enrich_target(card, enricher, states, "lib", None, include_graph=True)

    reached, unreached = cards.values()
    assert reached["cross_repo_impact"]["affected_repos"] == ["web"]
    assert reached["cross_repo_links"][0]["granularity"] == "file"
    assert "cross_repo_impact" not in unreached
    assert unreached["cross_repo_links"] == []


async def test_dead_code_withholds_safety_only_where_the_import_reaches(tmp_path: Path) -> None:
    enricher = await _enricher(tmp_path)
    rows = [
        {"kind": "unused_export", "file_path": path, "confidence": 0.9, "safe_to_delete": True}
        for path in ("packages/kit/src/a.ts", "packages/kit/src/b.ts")
    ]
    reached, unreached = (serialize_finding(f) for f in serve_findings(rows, enricher, "lib"))
    assert not reached["safe_to_delete"]
    assert "web may consume this export." in reached["cross_repo_note"]
    assert unreached["safe_to_delete"]
    assert "cross_repo_note" not in unreached
