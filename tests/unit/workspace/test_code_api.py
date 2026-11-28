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

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path
from textwrap import dedent

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.external_calls import ExternalBinding, ExternalCallSite
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

    def _crate(self, crate_dir: Path, manifest: str, *sources: str) -> None:
        (crate_dir / "src").mkdir(parents=True)
        (crate_dir / "Cargo.toml").write_text(manifest, encoding="utf-8")
        for source in sources:
            (crate_dir / "src" / source).write_text("", encoding="utf-8")

    def test_a_cargo_crate_publishes_its_lib_root(self, tmp_path: Path):
        self._crate(tmp_path, '[package]\nname = "widget"\n', "lib.rs")
        packages, _ = self._packages(tmp_path)
        assert packages["widget"].entry_files == frozenset({"src/lib.rs"})

    def test_a_bin_only_cargo_crate_is_counted_not_published(self, tmp_path: Path):
        self._crate(tmp_path / "app", '[package]\nname = "app"\n', "main.rs")
        self._crate(tmp_path / "lib", '[package]\nname = "corelib"\n', "lib.rs")
        packages, counts = self._packages(tmp_path)
        assert set(packages) == {"corelib"}
        assert counts["code_unpublished_manifest"] == 1

    def test_an_explicit_cargo_lib_path_wins(self, tmp_path: Path):
        self._crate(
            tmp_path, '[package]\nname = "widget"\n\n[lib]\npath = "src/api.rs"\n', "api.rs"
        )
        packages, _ = self._packages(tmp_path)
        assert packages["widget"].entry_files == frozenset({"src/api.rs"})

    def test_a_cargo_crate_with_lib_and_main_publishes_its_lib_root(self, tmp_path: Path):
        self._crate(tmp_path, '[package]\nname = "widget"\n', "lib.rs", "main.rs")
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


# ---------------------------------------------------------------------------
# Maven and Gradle
# ---------------------------------------------------------------------------

JVM_WORKSPACE = Path(__file__).parents[2] / "fixtures" / "jvm_workspace"
CLIENT_SOURCE = "billing-client/src/main/java/com/acme/billing/client/BillingClient.java"
CLIENT = "com.acme.billing:billing-client"
CHARGE_ID = f"{CLIENT_SOURCE}::BillingClient::charge"
CHARGE_CONTROLLER = "billing-service/src/main/java/com/acme/billing/service/ChargeController.java"
ORDER_API = "src/main/java/com/shop/orders/OrderApi.java"
PKG = "com.acme.billing.client"
CHARGE = """    public Invoice charge(String accountId, long cents) {
        return rest.postForObject("/charges", accountId, Invoice.class);
    }
"""


def _graph(repo: Path) -> tuple[list[tuple[str, str, list[str]]], list[ExternalCallSite]]:
    """The ``imports`` edges to an ``external:`` target and the calls into packages
    that a real graph build writes for *repo*."""
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for file_info in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(file_info, (repo / file_info.path).read_bytes()))
    edges = [
        (source, target, list(data.get("imported_names", [])))
        for source, target, data in builder.build().edges(data=True)
        if data.get("edge_type") == "imports" and target.startswith("external:")
    ]
    return edges, [s for sites in (builder.external_call_sites or {}).values() for s in sites]


def _heritage(repo: Path) -> list[tuple[str, str, str]]:
    """The ``extends`` and ``implements`` edges that a real graph build writes for *repo*."""
    parser = ASTParser()
    builder = GraphBuilder(repo)
    for file_info in FileTraverser(repo).traverse():
        builder.add_file(parser.parse_file(file_info, (repo / file_info.path).read_bytes()))
    return [
        (source, target, data["edge_type"])
        for source, target, data in builder.build().edges(data=True)
        if data.get("edge_type") in ("extends", "implements")
    ]


async def _extract_jvm(root: Path, monkeypatch, *, billing_edit: tuple[str, str] | None = None):
    """The contracts of the ``jvm_workspace`` fixture, copied to *root*.

    `billing` ships the client module, and `orders` uses it.
    """
    from repowise.core.workspace import contracts as contracts_mod

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, path: path)
    shutil.copytree(JVM_WORKSPACE, root)
    if billing_edit is not None:
        source = root / "billing" / CLIENT_SOURCE
        source.write_text(
            source.read_text(encoding="utf-8").replace(*billing_edit), encoding="utf-8"
        )

    billing = await make_repo_index(root / "billing", _parse(root / "billing"), alias="billing")
    edges, calls = _graph(root / "orders")
    orders = await make_repo_index(
        root / "orders", _parse(root / "orders"), alias="orders",
        external_edges=edges, call_sites=calls,
    )  # fmt: skip
    config = WorkspaceConfig(
        repos=[
            RepoEntry(path="billing", alias="billing"),
            RepoEntry(path="orders", alias="orders"),
        ],
        contracts=ContractConfig(),
    )
    try:
        return await run_contract_extraction(
            config,
            root,
            [],
            workspace_index=WorkspaceIndex({"billing": billing, "orders": orders}),
        )
    finally:
        await billing.close()
        await orders.close()


@pytest.fixture(scope="module")
def billing_stores(tmp_path_factory: pytest.TempPathFactory):
    """The fixture workspace extracted once, and once more with ``charge`` deleted."""
    root = tmp_path_factory.mktemp("jvm-workspace")
    with pytest.MonkeyPatch.context() as monkeypatch:
        before = asyncio.run(_extract_jvm(root / "a", monkeypatch))
        after = asyncio.run(_extract_jvm(root / "b", monkeypatch, billing_edit=(CHARGE, "")))
    return before, after


class TestMavenCodeContracts:
    """A service, its client artifact and a repository that uses it."""

    def test_the_client_module_provides_its_public_types_and_the_import_consumes_them(
        self, billing_stores
    ):
        store = billing_stores[0]

        # None of the service module: Spring Boot makes it an application.
        providers = sorted(
            c.contract_id
            for c in store.contracts
            if c.role == "provider" and c.contract_type == CODE_CONTRACT_TYPE
        )
        assert providers == [
            f"code::{CLIENT}::{PKG}.{name}"
            for name in (
                "BillingClient",
                "BillingClient.charge",
                "Invoice",
                "Invoice.Invoice",
                "Invoice.cents",
            )
        ]
        consumers = [
            c
            for c in store.contracts
            if c.role == "consumer" and c.contract_type == CODE_CONTRACT_TYPE
        ]
        assert {c.repo for c in consumers} == {"orders"}
        assert {c.contract_id for c in consumers} == set(providers)

    def test_the_import_links_to_the_client_module(self, billing_stores):
        linked = {
            (link.consumer_repo, link.provider_repo, link.contract_id)
            for link in billing_stores[0].contract_links
            if link.contract_type == CODE_CONTRACT_TYPE
        }
        assert ("orders", "billing", f"code::{CLIENT}::{PKG}.BillingClient.charge") in linked
        assert {(s, t) for s, t, _ in linked} == {("orders", "billing")}

    def test_a_route_of_orders_reaches_post_charges_through_billing_client_charge(
        self, billing_stores
    ):
        store = billing_stores[0]
        route = next(c for c in store.contracts if c.contract_id == "http::POST::/orders")
        assert route.symbol_id == f"{ORDER_API}::OrderApi::bill"

        # The route's method calls BillingClient.charge: the link of that call
        # names the calling method, and the consumer its line.
        code = [link for link in store.contract_links if link.consumer_symbol_id == route.symbol_id]
        assert [(link.contract_id, link.provider_symbol_id) for link in code] == [
            (f"code::{CLIENT}::{PKG}.BillingClient.charge", CHARGE_ID)
        ]
        call = next(
            c
            for c in store.contracts
            if c.role == "consumer" and c.symbol_id == route.symbol_id
        )
        assert (call.file_path, call.line) == (ORDER_API, 18)

        # BillingClient.charge calls POST /charges of the billing service.
        http = [link for link in store.contract_links if link.consumer_symbol_id == CHARGE_ID]
        assert [(link.contract_id, link.provider_symbol_id) for link in http] == [
            ("http::POST::/charges", f"{CHARGE_CONTROLLER}::ChargeController::create")
        ]

    def test_deleting_a_client_method_names_the_repo_that_uses_it(self, billing_stores):
        # The removal rule needs only the contract, and reports nothing else of
        # a client whose other contracts did not change.
        report = detect_breaking_changes(*billing_stores)
        removed = [c for c in report.changes if c.kind == "removed_endpoint"]
        assert [c.contract_id for c in removed] == [
            f"code::{CLIENT}::{PKG}.BillingClient.charge"
        ], report.to_dict()
        assert len(report.changes) == 1
        assert {(i.repo, Path(i.file).name) for i in removed[0].impacted_consumers} == {
            ("orders", "OrderApi.java")
        }


def _pom(artifact: str, extra: str = "", group: str = "<groupId>com.acme</groupId>") -> str:
    return f"<project>{group}<artifactId>{artifact}</artifactId>{extra}</project>"


DEPLOYS = "<distributionManagement></distributionManagement>"
PARENT = (
    "<parent><groupId>com.acme</groupId><artifactId>parent</artifactId>"
    "<version>1</version></parent>"
)


def _source(repo: Path, rel: str, text: str) -> None:
    """Write *text* to *rel* under *repo*, making its directories."""
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8")


async def _surface(
    lib: Path,
    edges: list[tuple[str, str, list[str]]] = (),
    own_edges: list[tuple[str, str, list[str]]] = (),
    calls: list[ExternalCallSite] | None = None,
    own_calls: list[ExternalCallSite] | None = None,
):
    """The code surface of the module *lib*, and of a repo `app` that holds the import
    *edges* and the *calls*."""
    app = lib.parent / "app"
    app.mkdir(exist_ok=True)
    lib_index = await make_repo_index(
        lib, _parse(lib), alias="lib", external_edges=own_edges, call_sites=own_calls,
        graph_edges=_heritage(lib),
    )  # fmt: skip
    app_index = await make_repo_index(
        app, {}, alias="app", external_edges=edges, call_sites=calls
    )
    try:
        return build_code_surface(
            {"lib": lib, "app": app},
            WorkspaceIndex({"lib": lib_index, "app": app_index}),
            make_exclude_predicate(),
        )
    finally:
        await lib_index.close()
        await app_index.close()


JAVA = "core/src/main/java/com/acme/core"
KOTLIN = "core/src/main/kotlin/com/acme/core"

#: The Java files of the module `core`, each with the rule of the surface it holds.
CORE_JAVA = {
    # Nested types are named by their path, two outer types may hold one `Builder`,
    # and `Order.build` is not `Order.Builder.build`.
    "Order.java": dedent("""\
        public class Order {
            public void build() {}
            public static class Builder {
                public void build() {}
            }
        }
        """),
    "Refund.java": dedent("""\
        public class Refund {
            public static class Builder {
                public void build() {}
                public void reason() {}
            }
        }
        """),
    # One simple name in two packages stays two contracts.
    "api/Error.java": "public class Error {\n    public String code() { return null; }\n}\n",
    "model/Error.java": "public class Error {\n    public String message() { return null; }\n}\n",
    # What another package cannot use: a package-private member and class, and a field.
    "Api.java": dedent("""\
        public class Api {
            public static final int MAX = 1;
            public void run() {}
            public void stop() {}
            void local() {}
        }
        class Hidden {
            public void x() {}
        }
        """),
    # An anonymous class in a field, an initializer, a method and an enum constant.
    "Anon.java": dedent("""\
        public class Anon {
            private static final Comparator<Anon> BY_ID = new Comparator<Anon>() {
                public int compare(Anon a, Anon b) { return 0; }
            };
            static { new Object() { public void inStatic() {} }; }
            public Runnable task() {
                return new Runnable() {
                    public void execute() {}
                    public void run() {}
                };
            }
            public Runnable once() { return new Runnable() { public void run() {} }; }
            public enum Kind {
                A { public String label() { return "a"; } },
                B;
                public String label() { return "b"; }
            }
        }
        """),
    # Overloads share one contract.
    "Over.java": dedent("""\
        public class Over {
            public void call() {}
            public void call(int times) {}
            public void call(int times, String... tags) {}
        }
        """),
    # A record's accessor shares the header line with the record's constructor.
    "Point.java": "public record Point(int x) {}\n",
    # What `Mail` inherits: `push` from `Sender` past the package-private `Base`, which
    # declares `close` again, and `ping` from `LoudPing`, which declares it again.
    "Mail.java": dedent("""\
        public abstract class Mail extends Base implements LoudPing {}
        class Base extends Sender {
            public void close() {}
        }
        """),
    "Sender.java": dedent("""\
        public class Sender {
            public void push() {}
            public void close() {}
        }
        """),
    "Ping.java": "public interface Ping {\n    void ping();\n}\n",
    "LoudPing.java": "public interface LoudPing extends Ping {\n    void ping();\n}\n",
}
CORE_KOTLIN = {
    "A.kt": dedent("""\
        package com.acme.core

        class A {
            companion object {
                fun make(): A = A()
            }
        }
        """),
    "B.kt": dedent("""\
        package com.acme.core

        class B {
            companion object Factory {
                fun make(): B = B()
            }
        }
        """),
    "Utils.kt": "package com.acme.core\n\nfun util() {}\n",
    "Inner.kt": "package com.acme.core\n\ninternal class Inner {\n    fun y() {}\n}\n",
}
#: The import of each file of the repo `app`, and the exports of `core` it consumes.
IMPORTS = {
    # A type gives its members; a member or a static wildcard gives its type too.
    "Type.java": ("com.acme.core.Api", {"Api", "Api.run", "Api.stop"}),
    "Member.java": ("com.acme.core.Api.run", {"Api", "Api.run"}),
    "StaticWildcard.java": ("com.acme.core.Api.*", {"Api", "Api.run", "Api.stop"}),
    "Nested.java": (
        "com.acme.core.Order.Builder",
        {"Order", "Order.Builder", "Order.Builder.build"},
    ),
    "NestedMember.java": (
        "com.acme.core.Order.Builder.build",
        {"Order", "Order.Builder", "Order.Builder.build"},
    ),
    "ChildPackage.java": ("com.acme.core.model.Error", {"model.Error", "model.Error.message"}),
    # A field is no export: its import consumes its type alone.
    "Field.java": ("com.acme.core.Api.MAX", {"Api"}),
    # No export names a companion object, named or not: its member is a member of the type.
    "Companion.kt": ("com.acme.core.A.Companion.make", {"A", "A.make"}),
    "CompanionObject.kt": ("com.acme.core.A.Companion", {"A", "A.make"}),
    "NamedCompanion.kt": ("com.acme.core.B.Factory.make", {"B", "B.make"}),
    # A Kotlin top-level function, by its package or by the class of its file.
    "TopLevel.kt": ("com.acme.core.util", {"util"}),
    "FileClass.java": ("com.acme.core.UtilsKt", {"util"}),
    "FileFunction.java": ("com.acme.core.UtilsKt.util", {"util"}),
    # No join: a name nothing declares, the artifact id, and a wildcard.
    "UndeclaredName.java": ("com.acme.core.Missing", set()),
    "ArtifactId.java": ("core", set()),
    "Wildcard.java": ("com.acme.core.*", set()),
}


APP_PARENT = (
    "<parent><groupId>com.shop</groupId><artifactId>app</artifactId>"
    "<version>1</version></parent>"
)
#: A file of each module of the repo `app` that imports and calls `Twin`, a class both
#: `rest` and `starter` hold, and the modules the import and the call join: `service`
#: depends on `starter`, `web` on `service`, and `batch` on neither, so it joins both: the
#: guard of that fall-back.
TWIN_HOLDERS = (("rest", "Twin"), ("starter", "Twin"))
TWIN = {
    "service/src/main/java/com/shop/service/Direct.java": {"com.acme:starter"},
    "web/src/main/java/com/shop/web/HandedOn.java": {"com.acme:starter"},
    "batch/src/main/java/com/shop/batch/Neither.java": {"com.acme:rest", "com.acme:starter"},
}


def _dependency(group: str, artifact: str) -> str:
    return (
        f"<dependencies><dependency><groupId>{group}</groupId>"
        f"<artifactId>{artifact}</artifactId></dependency></dependencies>"
    )


#: The call of each file of the repo `app`, as ingestion records it (the import's
#: target and the called path), and the export of `core` it reaches.
CALLS = {
    # A call on a receiver of an imported type, and a call through a static import.
    "Call.java": ("com.acme.core.Api", "Api.run", "Api.run"),
    "StaticCall.java": ("com.acme.core.Api.run", "Api.run", "Api.run"),
    "OnDemandCall.java": ("com.acme.core.*", "Api.run", "Api.run"),
    "NestedCall.java": ("com.acme.core.Order", "Order.Builder.build", "Order.Builder.build"),
    "CompanionCall.java": ("com.acme.core.A", "A.Companion.make", "A.make"),
    "FileClassCall.java": ("com.acme.core.UtilsKt", "UtilsKt.util", "util"),
    "TopLevelCall.kt": ("com.acme.core.util", "util", "util"),
    # A method a supertype declares is the supertype's export.
    "InheritedCall.java": ("com.acme.core.Mail", "Mail.push", "Sender.push"),
    "InterfaceCall.java": ("com.acme.core.Mail", "Mail.ping", "LoudPing.ping"),
    # None where the nearest declaring type is not exported, or is not in the workspace.
    "HiddenOverrideCall.java": ("com.acme.core.Mail", "Mail.close", None),
    "ObjectCall.java": ("com.acme.core.Api", "Api.toString", None),
    # A test file is left out, as for an import.
    "CallTest.java": ("com.acme.core.Api", "Api.run", None),
}
#: The number of arguments each file passes to `Over.call`: one fits `call(int)` exactly and
#: `call(int, String...)` by expanding, and Java takes the fixed arity first.
OVERLOAD_CALLS = {"OverloadOne.java": 1}
#: Functions of one file that call one export: two on one line, one of them on two lines.
TWICE = [("f", 3), ("h", 3), ("f", 4)]


def _call(
    file: str,
    module: str,
    symbol_path: str,
    ecosystem: str = "maven",
    binding: ExternalBinding = "named",
    caller: tuple[str, int] = ("f", 3),
) -> ExternalCallSite:
    """The record of a call of *file*, by default on line 3 by its function ``f``."""
    return ExternalCallSite(
        file, caller[1], f"{file}::{caller[0]}", ecosystem, module, "", f"external:{module}",
        symbol_path.split(".")[0], symbol_path, binding,
    )  # fmt: skip


@pytest.fixture(scope="module")
def jvm_lib(tmp_path_factory: pytest.TempPathFactory):
    """The surface of a repo `lib` and of a repo `app` that imports it, built once.

    `lib` is a pom that deploys three modules: `core`, which holds a file for each
    rule of the surface, and `rest` and `starter`, which split one Java package.
    `app` is a reactor of three modules (:data:`TWIN`).
    """
    lib = tmp_path_factory.mktemp("jvm-lib") / "lib"
    modules = "".join(f"<module>{name}</module>" for name in ("core", "rest", "starter"))
    parent = f"<packaging>pom</packaging><modules>{modules}</modules>{DEPLOYS}"
    _source(lib, "pom.xml", _pom("parent", parent))
    for name in ("core", "rest", "starter"):
        _source(lib, f"{name}/pom.xml", _pom(name, group=PARENT))
    for rel, text in CORE_JAVA.items():
        package = ".".join(["com.acme.core", *rel.split("/")[:-1]])
        _source(lib, f"{JAVA}/{rel}", f"package {package};\n\n{text}")
    for rel, text in CORE_KOTLIN.items():
        _source(lib, f"{KOTLIN}/{rel}", text)
    for module, name in (("rest", "Client"), ("starter", "AutoConfig"), *TWIN_HOLDERS):
        _source(
            lib,
            f"{module}/src/main/java/com/acme/split/{name}.java",
            f"package com.acme.split;\npublic class {name} {{}}\n",
        )
    app = lib.parent / "app"
    modules = "".join(f"<module>{name}</module>" for name in ("service", "web", "batch"))
    reactor = f"<packaging>pom</packaging><modules>{modules}</modules>"
    _source(app, "pom.xml", _pom("app", reactor, group="<groupId>com.shop</groupId>"))
    needs = {"service": ("com.acme", "starter"), "web": ("com.shop", "service")}
    for name in ("service", "web", "batch"):
        dependency = _dependency(*needs[name]) if name in needs else ""
        _source(app, f"{name}/pom.xml", _pom(name, dependency, group=APP_PARENT))
    for file in TWIN:
        _source(app, file, "")
    edges = [(file, f"external:{target}", []) for file, (target, _) in IMPORTS.items()]
    edges += [(file, "external:com.acme.split.Twin", []) for file in TWIN]
    edges.append(("Split.java", "external:com.acme.split.AutoConfig", []))
    # A suffix in capitals is the language its lower case names, as for ingestion.
    edges.append(("Upper.JAVA", "external:com.acme.core.Api", []))
    # A package that only begins with the name of one of `core`.
    edges.append(("SiblingPrefix.java", "external:com.acme.corex.Api", []))
    # An import of the module's own package, in a file of the repo that holds it.
    own = [("rest/src/main/java/com/acme/split/Client.java", "external:com.acme.split.*", [])]
    calls = [_call(file, module, path) for file, (module, path, _) in CALLS.items()]
    calls += [_call("Twice.java", "com.acme.core.Api", "Api.run", caller=c) for c in TWICE]
    calls += [_call(file, "com.acme.split.Twin", "Twin") for file in TWIN]
    # The call of `f` on line 3 once more, through another name the file binds (an alias).
    calls.append(_call("Twice.java", "com.acme.core.Api", "Api.run", caller=TWICE[0]))
    calls[-1] = replace(calls[-1], local_name="Alias")
    # A call of `Over.call` with one argument.
    calls += [
        replace(_call(file, "com.acme.core.Over", "Over.call"), argument_count=count)
        for file, count in OVERLOAD_CALLS.items()
    ]
    # A call of the repo's own module, which no other repo publishes to it.
    own_calls = [_call("Split.java", "com.acme.split.AutoConfig", "AutoConfig")]
    return lib, asyncio.run(_surface(lib, edges, own, calls, own_calls))


def _in(contracts, *files: str) -> set[str]:
    """The Java names of the contracts in the files whose path ends with one of *files*."""
    return {c.symbol_name for c in contracts if c.file_path.endswith(files)}


class TestJvmModuleSurface:
    """The exported path and the visibility of a symbol of a published module."""

    def test_a_nested_type_is_exported_by_its_path_after_the_package(self, jvm_lib):
        surface = jvm_lib[1]

        assert _in(surface.for_repo("lib"), "/Order.java", "/Refund.java") == {
            f"com.acme.core.{name}"
            for name in (
                "Order",
                "Order.build",
                "Order.Builder",
                "Order.Builder.build",
                "Refund",
                "Refund.Builder",
                "Refund.Builder.build",
                "Refund.Builder.reason",
            )
        }

    def test_one_simple_name_in_two_packages_is_two_contracts(self, jvm_lib):
        provided = _in(jvm_lib[1].for_repo("lib"), "/Error.java")

        assert provided == {
            "com.acme.core.api.Error",
            "com.acme.core.api.Error.code",
            "com.acme.core.model.Error",
            "com.acme.core.model.Error.message",
        }
        # The module's name holds a colon and a Java name none: both split back.
        for c in jvm_lib[1].for_repo("app"):
            assert c.symbol_name.rpartition(":")[0] == c.contract_id.split("::")[1]

    def test_a_member_of_a_type_other_packages_cannot_use_is_no_provider(self, jvm_lib):
        # Not `local`, `Hidden.x`, the field `MAX`, or the member of a Kotlin `internal` class.
        assert _in(jvm_lib[1].for_repo("lib"), "/Api.java", "/Inner.kt") == {
            "com.acme.core.Api",
            "com.acme.core.Api.run",
            "com.acme.core.Api.stop",
        }

    def test_a_member_of_an_anonymous_class_is_no_provider(self, jvm_lib):
        # Not `compare` (a field), `inStatic` (an initializer), `execute` or `run` (a
        # method), nor the body of the enum constant `A`.
        assert _in(jvm_lib[1].for_repo("lib"), "/Anon.java") == {
            f"com.acme.core.{name}"
            for name in ("Anon", "Anon.task", "Anon.once", "Anon.Kind", "Anon.Kind.label")
        }

    def test_a_record_component_is_a_member_of_the_record(self, jvm_lib):
        assert _in(jvm_lib[1].for_repo("lib"), "/Point.java") == {
            f"com.acme.core.{name}"
            for name in (
                "Point",
                "Point.Point",
                "Point.x",
                "Point.equals",
                "Point.hashCode",
                "Point.toString",
            )
        }

    def test_overloads_share_one_contract_and_are_no_ambiguity(self, jvm_lib):
        assert _in(jvm_lib[1].for_repo("lib"), "/Over.java") == {
            "com.acme.core.Over",
            "com.acme.core.Over.call",
        }
        assert "code_ambiguous_export" not in jvm_lib[1].stats["lib"]


class TestJvmImports:
    """What the imports of a repo consume of the published modules."""

    @pytest.mark.parametrize("file", list(IMPORTS))
    def test_an_import_consumes_the_exports_it_names(self, jvm_lib, file: str):
        consumed = {
            c.symbol_name.split(":", 2)[2]
            for c in jvm_lib[1].for_repo("app")
            if c.file_path == file
        }
        assert consumed == {f"com.acme.core.{name}" for name in IMPORTS[file][1]}

    def test_a_java_package_split_across_modules_joins_the_module_that_declares_the_name(
        self, jvm_lib
    ):
        split = [c for c in jvm_lib[1].for_repo("app") if c.file_path == "Split.java"]

        assert {c.meta["package"] for c in split} == {"com.acme:starter"}

    @pytest.mark.parametrize("file", list(TWIN), ids=lambda f: f.split("/")[0])
    def test_a_class_two_modules_hold_joins_the_one_the_importing_module_depends_on(
        self, jvm_lib, file: str
    ):
        app = jvm_lib[1].for_repo("app")
        joined = {(c.line is None, c.meta["package"]) for c in app if c.file_path == file}

        # The import, with no line, and the call.
        assert joined == {(imported, name) for imported in (True, False) for name in TWIN[file]}

    def test_an_import_that_names_no_export_is_counted(self, jvm_lib):
        # UndeclaredName and Field: each joins a package of the module and names no
        # export of it. SiblingPrefix joins none.
        assert jvm_lib[1].stats["app"]["code_unresolved_imports"] == 2

    def test_an_import_of_a_module_of_the_same_repository_is_counted_nowhere(self, jvm_lib):
        stats = jvm_lib[1].stats

        assert stats["app"]["code_wildcard_imports"] == 1
        assert "code_wildcard_imports" not in stats["lib"]


class TestJvmCalls:
    """The export each call of a repo reaches in the published modules."""

    def test_a_call_consumes_the_export_it_calls(self, jvm_lib):
        # One table: each call that consumes nothing guards a limit beside the calls that do.
        app = jvm_lib[1].for_repo("app")
        consumed = {
            file: [
                (c.symbol_name.split(":", 2)[2], c.line, c.symbol_id)
                for c in app
                if c.file_path == file
            ]
            for file in CALLS
        }
        assert consumed == {
            file: [(f"com.acme.core.{expected}", 3, f"{file}::f")] if expected else []
            for file, (_, _, expected) in CALLS.items()
        }

    def test_a_call_names_the_overload_its_arguments_select(self, jvm_lib):
        named = {
            c.file_path: c.meta.get("overload")
            for c in jvm_lib[1].for_repo("app")
            if c.file_path in OVERLOAD_CALLS
        }
        assert named == {"OverloadOne.java": f"{JAVA}/Over.java::Over::call#1"}

    def test_each_calling_function_and_line_is_one_consumer(self, jvm_lib):
        consumed = sorted(
            (c.line, c.symbol_id) for c in jvm_lib[1].for_repo("app") if c.file_path == "Twice.java"
        )
        assert consumed == [(3, "Twice.java::f"), (3, "Twice.java::h"), (4, "Twice.java::f")]

    def test_a_call_that_reaches_no_export_is_counted(self, jvm_lib):
        # HiddenOverrideCall and ObjectCall.
        assert jvm_lib[1].stats["app"]["code_unresolved_calls"] == 2

    def test_a_call_of_a_module_of_the_same_repository_is_counted_nowhere(self, jvm_lib):
        # A guard: such a call reaches a package and is still no unresolved call.
        assert not [c for c in jvm_lib[1].for_repo("lib") if c.role == "consumer"]
        assert "code_unresolved_calls" not in jvm_lib[1].stats["lib"]

    def test_a_repo_whose_calls_are_all_recorded_has_no_unknown_calls(self, jvm_lib):
        # A guard: Upper.JAVA imports the module, and its suffix names Java.
        assert "code_call_sites_unknown" not in jvm_lib[1].stats["app"]


#: A call of each file of the repo `app` into the npm package `@acme/lib`, and the
#: export it reaches.
NPM_CALLS = {
    "member.ts": (_call("member.ts", "@acme/lib", "Client.charge", "npm"), "Client.charge"),
    # A member the exported type does not declare, such as an inherited static.
    "inherited.ts": (_call("inherited.ts", "@acme/lib", "Client.get", "npm"), None),
    # Into a subpath or a submodule, whose `get` may not be the root's `get`.
    "subpath.ts": (_call("subpath.ts", "@acme/lib/sub", "get", "npm"), None),
    # A Python package of the same import name is another package.
    "other.py": (_call("other.py", "@acme/lib", "get", "pypi"), None),
}  # fmt: skip


@pytest.fixture(scope="module")
def npm_lib(tmp_path_factory: pytest.TempPathFactory):
    """The surface of an npm package `@acme/lib` and of a repo `app` that calls it, built once."""
    lib = tmp_path_factory.mktemp("npm-lib") / "lib"
    _source(lib, "package.json", '{"name": "@acme/lib", "exports": {".": "./index.ts"}}')
    index = "export class Client {\n  charge(id: string) {}\n}\nexport function get() {}\n"
    _source(lib, "index.ts", index)
    # A Vue file imports the package; ingestion records no call of a Vue file.
    vue = [("App.vue", "external:@acme/lib", ["get"])]
    return asyncio.run(_surface(lib, vue, calls=[site for site, _ in NPM_CALLS.values()]))


class TestCallsInOtherEcosystems:
    """A call of any language that ingestion records is read the same way."""

    def test_a_call_consumes_the_export_its_path_names(self, npm_lib):
        # One table: each call that consumes nothing guards a limit beside the call that does.
        app = npm_lib.for_repo("app")
        consumed = {file: [c.contract_id for c in app if c.file_path == file] for file in NPM_CALLS}
        assert consumed == {
            file: [f"code::@acme/lib::{expected}"] if expected else []
            for file, (_, expected) in NPM_CALLS.items()
        }

    def test_a_repo_with_no_complete_call_record_has_unknown_calls(self, npm_lib):
        assert npm_lib.stats["lib"]["code_call_sites_unknown"] == 1

    def test_an_import_of_a_language_whose_calls_are_not_recorded_makes_calls_unknown(
        self, npm_lib
    ):
        assert npm_lib.stats["app"]["code_call_sites_unknown"] == 1


class TestMavenPublishability:
    """Which modules deploy, and the name each one is published by."""

    def _packages(self, repo: Path):
        packages, counts = find_published_packages("r", repo)
        return {p.name: p for p in packages}, counts

    def _module(
        self, repo: Path, rel: str, package: str = "com.acme.lib", name: str = "Api"
    ) -> None:
        folder = f"{rel}/src/main/java" if rel else "src/main/java"
        _source(
            repo,
            f"{folder}/{package.replace('.', '/')}/{name}.java",
            f"package {package};\npublic class {name} {{}}\n",
        )

    def test_a_pom_that_deploys_publishes_its_java_packages(self, tmp_path: Path):
        _source(tmp_path, "pom.xml", _pom("lib", DEPLOYS))
        self._module(tmp_path, "")
        # A package only the tests declare is no package of the module.
        _source(
            tmp_path,
            "src/test/java/com/acme/lib/testing/Fake.java",
            "package com.acme.lib.testing;\npublic class Fake {}\n",
        )
        packages, _ = self._packages(tmp_path)
        lib = packages["com.acme:lib"]
        assert (lib.ecosystem, lib.manifest, lib.root) == ("maven", "pom.xml", "")
        assert lib.entry_files == frozenset({"src/main/java/com/acme/lib/Api.java"})
        assert lib.import_names == frozenset({"com.acme.lib"})
        assert lib.file_packages == {"src/main/java/com/acme/lib/Api.java": "com.acme.lib"}

    def test_a_pom_that_does_not_deploy_publishes_nothing_and_reads_no_build_unit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from repowise.core.build_units import collect

        def fail(ctx):
            raise AssertionError("build units read for a repo that publishes nothing")

        monkeypatch.setattr(collect, "collect_build_units", fail)
        _source(tmp_path, "pom.xml", _pom("lib"))
        self._module(tmp_path, "")
        packages, counts = self._packages(tmp_path)
        assert packages == {}
        assert counts["code_unpublished_manifest"] == 1

    def test_a_module_that_deploys_but_has_no_sources_is_counted(self, tmp_path: Path):
        _source(
            tmp_path,
            "pom.xml",
            _pom(
                "parent",
                "<packaging>pom</packaging><modules><module>lib</module>"
                f"<module>docs</module></modules>{DEPLOYS}",
            ),
        )
        _source(tmp_path, "lib/pom.xml", _pom("lib", group=PARENT))
        self._module(tmp_path, "lib")
        _source(tmp_path, "docs/pom.xml", _pom("docs", group=PARENT))
        packages, counts = self._packages(tmp_path)
        assert set(packages) == {"com.acme:lib"}
        assert counts["code_module_without_sources"] == 1
        assert counts["code_unpublished_manifest"] == 1  # the parent, packaging pom

    def test_an_application_module_publishes_nothing_although_its_parent_deploys(
        self, tmp_path: Path
    ):
        _source(
            tmp_path,
            "pom.xml",
            _pom(
                "parent",
                "<version>1</version><packaging>pom</packaging>"
                f"<modules><module>app</module><module>lib</module></modules>{DEPLOYS}",
            ),
        )
        for module, extra in (("app", "<packaging>war</packaging>"), ("lib", "")):
            _source(tmp_path, f"{module}/pom.xml", _pom(module, extra, group=PARENT))
            self._module(tmp_path, module, f"com.acme.{module}")
        packages, _ = self._packages(tmp_path)
        # The lib's groupId is its parent's.
        assert set(packages) == {"com.acme:lib"}
        assert packages["com.acme:lib"].manifest == "lib/pom.xml"

    def test_a_gradle_module_publishes_through_maven_publish(self, tmp_path: Path):
        _source(
            tmp_path,
            "settings.gradle.kts",
            'rootProject.name = "shop"\ninclude("client", "tool")\n',
        )
        _source(
            tmp_path, "client/build.gradle.kts", "plugins { `java-library`\n `maven-publish` }\n"
        )
        self._module(tmp_path, "client")
        _source(tmp_path, "tool/build.gradle.kts", "plugins { application }\n")
        self._module(tmp_path, "tool", "com.acme.tool")
        packages, _ = self._packages(tmp_path)
        assert set(packages) == {"shop:client"}
        assert packages["shop:client"].manifest == "client/build.gradle.kts"

    @pytest.mark.parametrize(
        "declared",
        ["rootProject.name = 'shop'", "rootProject.name = ('shop')", "rootProject.setName('shop')"],
    )
    def test_a_gradle_module_is_named_after_the_root_name_the_settings_declare(
        self, tmp_path: Path, declared: str
    ):
        _source(tmp_path, "settings.gradle", f"{declared}\ninclude 'client'\n")
        _source(tmp_path, "client/build.gradle", "plugins { id 'maven-publish' }\n")
        self._module(tmp_path, "client")
        assert set(self._packages(tmp_path)[0]) == {"shop:client"}

    def test_a_gradle_build_with_no_root_name_gives_no_package(self, tmp_path: Path):
        # The checkout directory is no name: two checkouts may share it.
        _source(tmp_path, "build.gradle", "plugins { id 'java-library'\n id 'maven-publish' }\n")
        self._module(tmp_path, "")
        packages, counts = self._packages(tmp_path)
        assert packages == {}
        assert counts["code_unnamed_module"] == 1
        # Declared, the name joins.
        _source(tmp_path, "settings.gradle", "rootProject.name = 'shop'\n")
        assert set(self._packages(tmp_path)[0]) == {"shop"}

    def test_a_file_under_a_build_the_root_does_not_list_is_no_file_of_the_root(
        self, tmp_path: Path
    ):
        _source(tmp_path, "pom.xml", _pom("root", DEPLOYS))
        self._module(tmp_path, "", "com.acme.root")
        # An example with a pom of its own, which the root pom does not list.
        _source(tmp_path, "extra/pom.xml", _pom("extra", DEPLOYS))
        self._module(tmp_path, "extra", "com.acme.extra")
        packages, counts = self._packages(tmp_path)
        assert set(packages) == {"com.acme:root"}
        assert packages["com.acme:root"].import_names == frozenset({"com.acme.root"})
        assert counts["code_unnamed_module"] == 1

    def test_a_module_info_exports_only_the_packages_it_names(self, tmp_path: Path):
        _source(tmp_path, "pom.xml", _pom("lib", DEPLOYS))
        self._module(tmp_path, "")
        self._module(tmp_path, "", "com.acme.lib.internal", "Impl")
        module_info = "module acme.lib { exports com.acme.lib; }\n"
        _source(tmp_path, "src/main/java/module-info.java", module_info)
        lib = self._packages(tmp_path)[0]["com.acme:lib"]
        assert lib.entry_files == frozenset({"src/main/java/com/acme/lib/Api.java"})
        assert lib.import_names == frozenset({"com.acme.lib"})

    def test_a_repo_with_no_build_file_is_not_read_as_a_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from repowise.core.workspace import code_api_jvm

        def fail(*args):
            raise AssertionError("JVM modules read for a repo with no build file")

        monkeypatch.setattr(code_api_jvm, "find_packages", fail)
        self._module(tmp_path, "")
        assert self._packages(tmp_path) == ({}, {})
