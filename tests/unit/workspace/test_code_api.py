"""Code-API contracts — a library's published surface as a cross-repo contract.

The four transports that existed all cross a wire, so a repo that breaks its
consumers by deleting a public method or adding a required parameter to one
produced nothing. That is the reported .NET case, and the headline test drives
it end to end through the real parser and the real ``.csproj`` reader.

Two further claims are under test. The surface is what the *manifest* publishes,
not every public symbol under the package — an entry-file rule that also decides
which manifests count as publishing anything at all. And the whole thing is
additive: no breaking-change rule is added, so ``_removed_endpoint`` and the
three field rules do the work unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser
from repowise.core.workspace.breaking_change import detect_breaking_changes
from repowise.core.workspace.code_api import (
    CODE_CONTRACT_TYPE,
    build_code_surface,
    find_published_packages,
)
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import ContractStore, run_contract_extraction
from repowise.core.workspace.extractors.base import make_exclude_predicate
from repowise.core.workspace.repo_index import WorkspaceIndex

from ._repo_index import make_repo_index

# ---------------------------------------------------------------------------
# The .NET fixture — the reported case
# ---------------------------------------------------------------------------

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <IsPackable>true</IsPackable>
    <PackageId>Contoso.Orders</PackageId>
  </PropertyGroup>
</Project>
"""

LIB_BEFORE = """namespace Contoso.Orders;

public class OrderService
{
    public void PlaceOrder(string sku)
    {
    }
}
"""
LIB_AFTER = LIB_BEFORE.replace("PlaceOrder(string sku)", "PlaceOrder(string sku, string tenant)")
LIB_REMOVED = LIB_BEFORE.replace("public void PlaceOrder(string sku)\n    {\n    }\n", "")


def _parse(repo: Path) -> dict[str, list]:
    """Real ingestion output for *repo*, so the test reads real signatures."""
    parser = ASTParser()
    out: dict[str, list] = {}
    for file_info in FileTraverser(repo).traverse():
        parsed = parser.parse_file(file_info, (repo / file_info.path).read_bytes())
        if parsed.symbols:
            out[file_info.path] = list(parsed.symbols)
    return out


async def _extract(root: Path, lib_source: str, monkeypatch) -> ContractStore:
    """A two-repo .NET workspace: `lib` publishes the package, `app` imports it."""
    from repowise.core.workspace import contracts as contracts_mod

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, path: path)

    lib = root / "lib"
    (lib / ".repowise").mkdir(parents=True, exist_ok=True)
    (lib / "Contoso.Orders.csproj").write_text(CSPROJ, encoding="utf-8")
    (lib / "OrderService.cs").write_text(lib_source, encoding="utf-8")

    app = root / "app"
    (app / ".repowise").mkdir(parents=True, exist_ok=True)

    lib_index = await make_repo_index(lib, _parse(lib), alias="lib")
    # The `nuget:` prefix and the child namespace are what `resolve_csharp_import`
    # actually writes for a `using` that matches a PackageReference — not the
    # bare package id, which is why the join strips and walks dotted prefixes.
    app_index = await make_repo_index(
        app,
        {},
        alias="app",
        external_edges=[
            ("Program.cs", "external:nuget:Contoso.Orders.Models", ["OrderService"])
        ],
    )
    config = WorkspaceConfig(
        repos=[RepoEntry(path="lib", alias="lib"), RepoEntry(path="app", alias="app")],
        contracts=ContractConfig(),
    )
    try:
        return await run_contract_extraction(
            config,
            root,
            [],
            workspace_index=WorkspaceIndex({"lib": lib_index, "app": app_index}),
        )
    finally:
        await lib_index.close()
        await app_index.close()


class TestTheReportedCase:
    """A required parameter added to a published method, on a two-repo fixture."""

    async def test_adding_a_required_parameter_names_the_impacted_consumer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        previous = await _extract(tmp_path / "a", LIB_BEFORE, monkeypatch)
        current = await _extract(tmp_path / "b", LIB_AFTER, monkeypatch)

        method = "code::Contoso.Orders::OrderService.PlaceOrder"
        provider = next(c for c in previous.contracts if c.contract_id == method)
        assert provider.role == "provider" and provider.repo == "lib"
        # The schema is W4's mapper reading the parsed signature — not written here.
        assert provider.schema is not None
        assert [f.name for f in provider.schema.request_fields] == ["sku"]
        assert previous.contract_links, "the consumer must match for impact to resolve"

        report = detect_breaking_changes(previous, current)
        tightened = [c for c in report.changes if c.kind == "field_required"]
        assert [c.field_name for c in tightened] == ["tenant"], report.to_dict()
        change = tightened[0]
        assert change.severity == "breaking"
        assert change.contract_type == CODE_CONTRACT_TYPE
        assert change.provider_symbol_id
        assert [i.repo for i in change.impacted_consumers] == ["app"]

    async def test_deleting_a_published_method_reuses_the_removed_endpoint_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        previous = await _extract(tmp_path / "a", LIB_BEFORE, monkeypatch)
        current = await _extract(tmp_path / "b", LIB_REMOVED, monkeypatch)

        report = detect_breaking_changes(previous, current)
        removed = [c for c in report.changes if c.kind == "removed_endpoint"]
        assert [c.contract_id for c in removed] == [
            "code::Contoso.Orders::OrderService.PlaceOrder"
        ], report.to_dict()
        assert [i.repo for i in removed[0].impacted_consumers] == ["app"]

    async def test_an_unchanged_library_reports_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        previous = await _extract(tmp_path / "a", LIB_BEFORE, monkeypatch)
        current = await _extract(tmp_path / "b", LIB_BEFORE, monkeypatch)
        assert detect_breaking_changes(previous, current).changes == []

    async def test_importing_a_type_consumes_the_members_it_owns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The v1 non-goal, stated positively: no call-site argument matching.

        ``app`` imports ``OrderService``, never ``PlaceOrder``, and is still
        impacted by a change to it — a package importer takes the whole type.
        """
        store = await _extract(tmp_path / "a", LIB_BEFORE, monkeypatch)
        consumed = sorted(
            c.contract_id for c in store.contracts if c.role == "consumer"
        )
        assert consumed == [
            "code::Contoso.Orders::OrderService",
            "code::Contoso.Orders::OrderService.PlaceOrder",
        ]


class TestPublishability:
    """Which manifests declare a package, and which only look like they do."""

    def _packages(self, repo: Path):
        packages, counts = find_published_packages("r", repo)
        return {p.name: p for p in packages}, counts

    def test_a_csproj_must_opt_in_to_being_packable(self, tmp_path: Path):
        # SDK-style projects are packable by default, so silence cannot mean
        # yes: every internal project in a solution would become a library.
        (tmp_path / "Silent.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>",
            encoding="utf-8",
        )
        packages, counts = self._packages(tmp_path)
        assert packages == {}
        assert counts["code_unpublished_manifest"] == 1

    @pytest.mark.parametrize(
        "properties",
        [
            "<IsPackable>true</IsPackable>",
            "<GeneratePackageOnBuild>true</GeneratePackageOnBuild>",
            "<PackageId>Contoso.Orders</PackageId>",
        ],
    )
    def test_any_explicit_publish_signal_counts(self, tmp_path: Path, properties: str):
        (tmp_path / "Contoso.Orders.csproj").write_text(
            f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>{properties}'
            "</PropertyGroup></Project>",
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert set(packages) == {"Contoso.Orders"}
        assert packages["Contoso.Orders"].entry_files is None  # whole project

    def test_is_packable_false_overrides_a_package_id(self, tmp_path: Path):
        (tmp_path / "Internal.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<PackageId>Contoso.Internal</PackageId>"
            "<IsPackable>false</IsPackable></PropertyGroup></Project>",
            encoding="utf-8",
        )
        assert self._packages(tmp_path)[0] == {}

    def test_an_npm_app_with_no_entry_point_publishes_nothing(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "my-app"}', encoding="utf-8")
        assert self._packages(tmp_path)[0] == {}

    def test_a_private_npm_package_still_publishes_into_the_workspace(self, tmp_path: Path):
        # `private` blocks the public registry, not workspace consumption.
        (tmp_path / "package.json").write_text(
            '{"name": "@scope/types", "private": true, "exports": {".": "./src/index.ts"}}',
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert packages["@scope/types"].entry_files == frozenset({"src/index.ts"})

    def test_npm_conditional_exports_all_count_as_entry_points(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"name": "p", "exports": {".": {"import": "./esm/i.js", "require": "./cjs/i.js"}},'
            ' "types": "./types/i.d.ts"}',
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert packages["p"].entry_files == frozenset(
            {"esm/i.js", "cjs/i.js", "types/i.d.ts"}
        )

    def test_a_python_entry_comes_from_the_build_backend_not_the_dist_name(
        self, tmp_path: Path
    ):
        # `repowise-core` ships `src/repowise`; deriving the module from the
        # distribution name would look for `src/repowise_core` and find nothing.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "repowise-core"\n'
            '[tool.hatch.build.targets.wheel]\npackages = ["src/repowise"]\n',
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert packages["repowise-core"].entry_files == frozenset(
            {"src/repowise/__init__.py"}
        )

    def test_a_python_dunder_all_re_export_joins_the_surface(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "lib"\n', encoding="utf-8"
        )
        pkg = tmp_path / "lib"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            'from .core import Widget\n\n__all__ = ["Widget"]\n', encoding="utf-8"
        )
        packages, _ = self._packages(tmp_path)
        assert packages["lib"].reexported == frozenset({"Widget"})

    def test_a_computed_dunder_all_is_refused_rather_than_half_read(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "lib"\n', encoding="utf-8"
        )
        pkg = tmp_path / "lib"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(
            "__all__ = [*_generated(), 'Widget']\n", encoding="utf-8"
        )
        # The literal is still read; the computed half simply cannot be, and a
        # partial list is a surface, not a lie about one.
        assert self._packages(tmp_path)[0]["lib"].reexported == frozenset({"Widget"})

    def test_a_cargo_crate_publishes_its_lib_root(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "widget"\n', encoding="utf-8"
        )
        packages, _ = self._packages(tmp_path)
        assert packages["widget"].entry_files == frozenset({"src/lib.rs"})

    def test_an_ecosystem_with_no_publish_opt_in_is_counted_not_guessed(
        self, tmp_path: Path
    ):
        (tmp_path / "go.mod").write_text("module example.com/widget\n", encoding="utf-8")
        packages, counts = self._packages(tmp_path)
        assert packages == {}
        assert counts["code_unsupported_ecosystem"] == 1


class TestManifestSpellings:
    """Shapes a real manifest uses that a happy-path reader silently loses."""

    def _packages(self, repo: Path):
        packages, counts = find_published_packages("r", repo)
        return {p.name: p for p in packages}, counts

    def test_a_bare_main_is_an_entry_point(self, tmp_path: Path):
        # `npm init` writes `"main": "index.js"`. Only `exports` values need `./`.
        (tmp_path / "package.json").write_text(
            '{"name": "@acme/sdk", "main": "index.js"}', encoding="utf-8"
        )
        packages, _ = self._packages(tmp_path)
        assert packages["@acme/sdk"].entry_files == frozenset({"index.js"})

    def test_an_exports_fallback_array_is_walked(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"name": "p", "exports": {".": [{"import": "./esm.js"}, "./cjs.js"]}}',
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert packages["p"].entry_files == frozenset({"esm.js", "cjs.js"})

    def test_setuptools_auto_discovery_is_a_table_not_a_package_list(
        self, tmp_path: Path
    ):
        # `packages = {find = {where = ["src"]}}` iterated as a list yields the
        # key "find", which both invents a directory and suppresses the guess.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mylib"\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
            encoding="utf-8",
        )
        packages, _ = self._packages(tmp_path)
        assert packages["mylib"].entry_files == frozenset(
            {"src/mylib/__init__.py", "mylib/__init__.py"}
        )

    def test_a_malformed_pyproject_cannot_abort_the_workspace(self, tmp_path: Path):
        # `tool` as a scalar is legal TOML; an unguarded .get() chain would
        # raise and take every other contract type down with it.
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "a"\ntool = "oops"\n', encoding="utf-8"
        )
        assert set(self._packages(tmp_path)[0]) == {"a"}

    def test_an_unevaluated_msbuild_property_is_unknown_not_no(self, tmp_path: Path):
        # <IsPackable>$(PublishLibraries)</IsPackable> is the normal way to
        # centralise the flag; reading it as false would drop a real library.
        (tmp_path / "Lib.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
            "<PackageId>Contoso.Lib</PackageId>"
            "<IsPackable>$(PublishLibraries)</IsPackable>"
            "</PropertyGroup></Project>",
            encoding="utf-8",
        )
        assert set(self._packages(tmp_path)[0]) == {"Contoso.Lib"}


class TestTheImportJoin:
    """What an importing source writes is not always the manifest's name."""

    @pytest.mark.parametrize(
        ("ecosystem", "manifest_name", "external_name"),
        [
            ("npm", "@scope/ui", "@scope/ui/lib/format"),
            ("nuget", "Contoso.Orders", "nuget:Contoso.Orders.Models"),
            ("cargo", "tokio-util", "tokio_util"),
            ("pypi", "repowise-core", "repowise.workspace"),
        ],
        ids=["npm-subpath", "nuget-prefixed-namespace", "cargo-underscore", "pypi-module"],
    )
    def test_a_reachable_spelling_resolves_to_its_package(
        self, ecosystem: str, manifest_name: str, external_name: str
    ):
        from repowise.core.workspace.code_api import (
            PublishedPackage,
            _import_names,
            _package_for,
        )

        entries = frozenset({"src/repowise/__init__.py"}) if ecosystem == "pypi" else None
        package = PublishedPackage(
            name=manifest_name,
            ecosystem=ecosystem,
            repo="lib",
            manifest="m",
            root="",
            entry_files=entries,
            import_names=_import_names(ecosystem, manifest_name, entries),
        )
        by_import = {n: package for n in package.import_names}
        assert _package_for(external_name, by_import) is package

    def test_a_sibling_name_sharing_a_prefix_does_not_match(self):
        from repowise.core.workspace.code_api import PublishedPackage, _package_for

        ui = PublishedPackage(
            name="@scope/ui", ecosystem="npm", repo="lib", manifest="m", root=""
        )
        assert _package_for("@scope/ui-icons", {"@scope/ui": ui}) is None


class TestSurfaceScope:
    """Published, not merely public."""

    async def test_a_public_symbol_outside_an_entry_file_is_not_published(
        self, tmp_path: Path
    ):
        repo = tmp_path / "lib"
        (repo / "src").mkdir(parents=True)
        (repo / ".repowise").mkdir()
        (repo / "package.json").write_text(
            '{"name": "@scope/lib", "exports": {".": "./src/index.ts"}}', encoding="utf-8"
        )
        (repo / "src" / "index.ts").write_text(
            "export function published(a: string) {}\n", encoding="utf-8"
        )
        (repo / "src" / "internal.ts").write_text(
            "export function hidden(a: string) {}\n", encoding="utf-8"
        )
        index = await make_repo_index(repo, _parse(repo), alias="lib")
        try:
            surface = build_code_surface(
                {"lib": repo}, WorkspaceIndex({"lib": index}), make_exclude_predicate()
            )
        finally:
            await index.close()
        assert [c.contract_id for c in surface.for_repo("lib")] == [
            "code::@scope/lib::published"
        ]

    async def test_an_ambiguous_dunder_all_name_is_refused_not_guessed(
        self, tmp_path: Path
    ):
        """A contract's ``symbol_id`` must not be decided by index row order."""
        repo = tmp_path / "lib"
        (repo / "pkg").mkdir(parents=True)
        (repo / ".repowise").mkdir()
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\n', encoding="utf-8"
        )
        (repo / "pkg" / "__init__.py").write_text(
            "__all__ = ['Widget', 'Gauge']\n", encoding="utf-8"
        )
        (repo / "pkg" / "a.py").write_text("class Widget:\n    pass\n", encoding="utf-8")
        (repo / "pkg" / "b.py").write_text(
            "class Widget:\n    pass\n\n\nclass Gauge:\n    pass\n", encoding="utf-8"
        )
        index = await make_repo_index(repo, _parse(repo), alias="lib")
        try:
            surface = build_code_surface(
                {"lib": repo}, WorkspaceIndex({"lib": index}), make_exclude_predicate()
            )
        finally:
            await index.close()
        # `Gauge` is unique and published; `Widget` names two symbols, so it is
        # not published at all rather than published as a coin flip.
        assert [c.contract_id for c in surface.for_repo("lib")] == ["code::pkg::Gauge"]

    async def test_an_import_of_an_unpublished_package_yields_no_consumer(
        self, tmp_path: Path
    ):
        app = tmp_path / "app"
        (app / ".repowise").mkdir(parents=True)
        index = await make_repo_index(
            app, {}, alias="app", external_edges=[("a.ts", "external:react", ["useState"])]
        )
        try:
            surface = build_code_surface(
                {"app": app}, WorkspaceIndex({"app": index}), make_exclude_predicate()
            )
        finally:
            await index.close()
        assert surface.for_repo("app") == []
