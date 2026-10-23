"""The service-name map: sources, precedence, and what the matcher does with it."""

from __future__ import annotations

import importlib
import pkgutil
import posixpath
from pathlib import Path, PurePosixPath

import pytest

import repowise.core.workspace.service_names as service_names
from repowise.core.workspace.contracts import Contract, ContractStore
from repowise.core.workspace.cross_repo import CrossRepoOverlay
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.origins import LinkStrategy, link_confidence
from repowise.core.workspace.service_names import (
    ENV,
    HOST,
    SOURCES,
    ServiceNameMap,
    build_service_name_map,
    service_names_fingerprint,
)
from repowise.core.workspace.service_names.base import (
    ServiceNameSource,
    host_of,
    join_config_path,
)
from repowise.core.workspace.system_graph import build_system_graph

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "service_names"


def _repos(root: Path, *names: str) -> dict[str, Path]:
    return {n.rsplit("/", 1)[-1]: root / n for n in names}


def _mapped(m: ServiceNameMap, kind: str = HOST) -> dict[str, tuple[str, str]]:
    return {n: (e.repo, e.source) for (k, n), e in m.entries.items() if k == kind}


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------


def test_registry_is_sorted_unique_and_every_source_module_is_registered() -> None:
    names = [s.name for s in SOURCES]
    assert names == sorted(names) and len(set(names)) == len(names)
    registered = {id(s) for s in SOURCES}
    for info in pkgutil.iter_modules(service_names.__path__):
        module = importlib.import_module(f"{service_names.__name__}.{info.name}")
        source = getattr(module, "SOURCE", None)
        if source is not None:
            assert id(source) in registered, f"{info.name}.SOURCE is not in SOURCES"


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.name)
def test_every_source_declares_a_gate(source: ServiceNameSource) -> None:
    assert source.filenames and callable(source.read)
    # A glob that admits any file of a type must narrow by directory or marker,
    # so the walk reads a few config files, never every source file.
    for glob in source.filenames:
        if glob.startswith("*"):
            assert source.under or source.marker, (source.name, glob)
    assert source.marker or source.under or all("*" not in g for g in source.filenames)


def test_only_gated_files_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "svc"
    (repo / "src").mkdir(parents=True)
    for i in range(50):
        (repo / "src" / f"m{i}.py").write_text("x = 1\n")
        (repo / "src" / f"c{i}.yaml").write_text("a: 1\n")
    (repo / "docker-compose.yml").write_text("services: {}\n")
    seen: list[str] = []
    real = service_names._read
    monkeypatch.setattr(service_names, "_read", lambda p: seen.append(p.name) or real(p))
    build_service_name_map({"svc": repo})
    assert seen == ["docker-compose.yml"]


# ---------------------------------------------------------------------------
# Sources, on real files
# ---------------------------------------------------------------------------


def test_compose_at_the_workspace_root_maps_build_contexts() -> None:
    root = FIXTURES / "robotshop"
    repos = _repos(root, "catalogue", "cart", "user", "mongo", "mysql", "shipping",
                   "ratings", "payment", "dispatch", "web")
    hosts = _mapped(build_service_name_map(repos, root))
    assert hosts["mongodb"] == ("mongo", "compose")
    assert hosts["catalogue"] == ("catalogue", "compose")
    assert "redis" not in hosts and "rabbitmq" not in hosts  # images, no repo


def test_compose_context_and_container_name() -> None:
    root = FIXTURES / "gva"
    m = build_service_name_map({"gva": root, "web": root / "web", "server": root / "server"})
    hosts = _mapped(m)
    assert hosts["server"] == ("server", "compose")
    assert hosts["gva-web"] == ("web", "compose")
    assert "mysql" not in hosts
    assert not _mapped(m, ENV)  # MYSQL_USER: 'gva' is not an address


def test_compose_dockerfile_narrows_a_shared_context() -> None:
    root = FIXTURES / "eshoponweb"
    m = build_service_name_map(
        {"web": root / "src" / "Web", "publicapi": root / "src" / "PublicApi"}, root
    )
    assert _mapped(m) == {
        "eshopwebmvc": ("web", "compose"),
        "eshoppublicapi": ("publicapi", "compose"),
    }


def test_compose_environment_addresses_become_token_entries(tmp_path: Path) -> None:
    for r in ("deploy", "catalogue", "cart"):
        (tmp_path / r).mkdir()
    (tmp_path / "deploy" / "docker-compose.yml").write_text(
        "services:\n"
        "  catalogue: {build: ../catalogue}\n"
        "  cart:\n"
        "    build: {context: ../cart}\n"
        "    environment:\n"
        "      CATALOGUE_URL: http://catalogue:8080\n"
        "      MYSQL_USER: cart\n"
        "  web:\n"
        "    image: nginx\n"
        "    environment: ['CART_HOST=cart', 'API=https://api.stripe.com']\n"
    )
    m = build_service_name_map(_repos(tmp_path, "deploy", "catalogue", "cart"))
    assert _mapped(m, ENV) == {
        "catalogue_url": ("catalogue", "compose"),
        "cart_host": ("cart", "compose"),
    }


def test_a_name_claimed_for_two_repos_is_left_unmapped(tmp_path: Path) -> None:
    for r in ("a", "b"):
        (tmp_path / r).mkdir()
        (tmp_path / r / "docker-compose.yml").write_text("services:\n  api: {build: .}\n")
    m = build_service_name_map(_repos(tmp_path, "a", "b"))
    assert m.lookup("api") is None
    assert m.ambiguous[(HOST, "api")] == ("a", "b")


def test_k8s_service_by_selector_label_and_never_a_sibling_by_location() -> None:
    m = build_service_name_map({"petclinic": FIXTURES / "petclinic"})
    # demo-db sits in the same repo beside another Service: not its own.
    assert _mapped(m) == {"petclinic": ("petclinic", "k8s_service")}


def test_k8s_service_alone_in_its_repo_and_helm_templates(tmp_path: Path) -> None:
    (tmp_path / "orders" / "k8s").mkdir(parents=True)
    (tmp_path / "orders" / "k8s" / "svc.yaml").write_text(
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: orders-api\nspec:\n  ports: [{port: 80}]\n"
    )
    (tmp_path / "chart" / "deploy").mkdir(parents=True)
    (tmp_path / "chart" / "deploy" / "svc.yaml").write_text(
        "kind: Service\nmetadata:\n  name: {{ .Values.name }}\n"
    )
    hosts = _mapped(build_service_name_map(_repos(tmp_path, "orders", "chart")))
    assert hosts == {"orders-api": ("orders", "k8s_service")}


def test_spring_application_name_from_main_resources_only(tmp_path: Path) -> None:
    root = FIXTURES / "spring_pm"
    hosts = _mapped(
        build_service_name_map(_repos(root, "account-service", "auth-service", "config"))
    )
    assert hosts == {
        "account-service": ("account-service", "spring_application_name"),
        "auth-service": ("auth-service", "spring_application_name"),
    }


def test_spring_properties_and_test_resources(tmp_path: Path) -> None:
    res = tmp_path / "shipping" / "src" / "main" / "resources"
    res.mkdir(parents=True)
    (res / "application.properties").write_text("spring.application.name=shipping-svc\n")
    test_res = tmp_path / "shipping" / "src" / "test" / "resources"
    test_res.mkdir(parents=True)
    (test_res / "application.properties").write_text("spring.application.name=other\n")
    hosts = _mapped(build_service_name_map(_repos(tmp_path, "shipping")))
    assert hosts == {"shipping-svc": ("shipping", "spring_application_name")}


def test_aspire_add_project_with_a_path() -> None:
    root = FIXTURES / "eshop"
    aliases = ["eshop.apphost", "catalog.api", "basket.api", "ordering.api", "identity.api",
               "orderprocessor", "paymentprocessor", "webhooks.api", "webhookclient", "webapp"]
    hosts = _mapped(build_service_name_map(_repos(root, *aliases)))
    assert hosts["catalog-api"] == ("catalog.api", "aspire_apphost")
    assert hosts["webhooksclient"] == ("webhookclient", "aspire_apphost")
    assert len(hosts) == 9
    assert "eventbus" not in hosts and "redis" not in hosts  # infrastructure, not projects


def test_aspire_add_project_by_generated_type() -> None:
    root = FIXTURES / "clean"
    m = build_service_name_map(
        _repos(root, "Clean.Architecture.AspireHost", "Clean.Architecture.Web")
    )
    assert _mapped(m) == {"web": ("Clean.Architecture.Web", "aspire_apphost")}


@pytest.mark.parametrize(
    ("key", "value", "host"),
    [
        ("CATALOGUE_URL", "http://catalogue:8080/api", "catalogue"),
        ("ANY", "cart:8080", "cart"),
        ("CART_HOST", "cart", "cart"),
        ("MYSQL_USER", "cart", None),
        ("CART_URL", "${CART}", None),
        ("ASPNETCORE_URLS", "http://+:8080", None),
    ],
)
def test_host_of(key: str, value: str, host: str | None) -> None:
    assert host_of(key, value) == host


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _http(repo: str, role: str, path: str, method: str = "GET", **meta) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::{method}::{path}",
        contract_type="http",
        role=role,
        file_path=f"{repo}/{role}.java",
        symbol_name=path,
        confidence=0.85 if role == "provider" else 0.75,
        meta={"method": method, "path": path, **meta},
    )


def _names(**entries: str) -> ServiceNameMap:
    from repowise.core.workspace.service_names import ServiceName

    m = ServiceNameMap(repos=frozenset(entries.values()) | {"web"})
    for name, repo in entries.items():
        kind, _, name = name.rpartition("__")
        kind = kind or HOST
        m.entries[(kind, name)] = ServiceName(name, kind, repo, "compose", "deploy/compose.yml")
    return m


def test_a_mapped_host_narrows_a_shared_path_and_names_the_rule() -> None:
    contracts = [
        _http("account", "provider", "/{param}"),
        _http("statistics", "provider", "/{param}"),
        _http("web", "consumer", "/{param}", host="statistics-service"),
    ]
    annotate_consumer_targets(contracts, {}, _names(**{"statistics-service": "statistics"}))
    consumer = contracts[-1]
    assert consumer.meta["target_via"] == "compose"
    assert consumer.meta["target_evidence"] == "deploy/compose.yml"
    state = run_matching(contracts)
    assert [(lk.provider_repo, lk.strategy) for lk in state.links] == [
        ("statistics", LinkStrategy.SERVICE_MAPPED)
    ]
    assert state.service_links == []


def test_service_bases_win_over_the_map() -> None:
    contracts = [
        _http("a", "provider", "/x"),
        _http("b", "provider", "/x"),
        _http("web", "consumer", "/x", host="api"),
    ]
    annotate_consumer_targets(contracts, {"api": "b"}, _names(api="a"))
    assert contracts[-1].meta["target_via"] == "service_bases"
    [link] = run_matching(contracts).links
    assert link.provider_repo == "b"


def test_a_token_resolves_through_an_env_entry() -> None:
    contracts = [
        _http("a", "provider", "/x"),
        _http("b", "provider", "/x"),
        _http("web", "consumer", "/x", base_stripped=True, base_token="CATALOGUE_URL"),
    ]
    annotate_consumer_targets(contracts, {}, _names(env__catalogue_url="b"))
    [link] = run_matching(contracts).links
    assert (link.provider_repo, link.strategy) == ("b", LinkStrategy.SERVICE_MAPPED)


def test_a_known_host_with_an_unread_route_is_a_service_level_edge() -> None:
    contracts = [
        _http("auth", "provider", "/users", "POST"),
        _http("web", "consumer", "/uaa/users", "POST", host="auth-service"),
        _http("web", "consumer", "/nowhere", host="unmapped"),
    ]
    annotate_consumer_targets(contracts, {}, _names(**{"auth-service": "auth"}))
    state = run_matching(contracts)
    assert state.links == []
    [svc] = state.service_links
    assert (svc.consumer_repo, svc.provider_repo, svc.provider_file) == ("web", "auth", "")
    assert (svc.strategy, svc.match_type) == (LinkStrategy.HOST_ONLY, "candidate")
    assert svc.confidence == link_confidence(LinkStrategy.HOST_ONLY, 0.75)

    store = ContractStore(contracts=contracts, service_links=state.service_links)
    assert ContractStore.from_dict(store.to_dict()).service_links == state.service_links

    graph = build_system_graph(contracts, [], CrossRepoOverlay(), {}, service_links=state.service_links)
    [edge] = graph.edges
    assert (edge.source, edge.target, edge.kind, edge.strategy) == (
        "web",
        "auth",
        "http",
        LinkStrategy.HOST_ONLY,
    )
    assert edge.structural


def test_no_service_edge_to_a_repo_outside_the_workspace() -> None:
    contracts = [
        _http("a", "provider", "/x"),
        _http("web", "consumer", "/y", base_stripped=True, base_token="API_BASE"),
    ]
    annotate_consumer_targets(contracts, {"API_BASE": "typo_repo"})
    assert "target_via" not in contracts[-1].meta
    assert run_matching(contracts).service_links == []


def test_a_mapped_public_looking_host_is_internal_and_others_stay_external() -> None:
    contracts = [
        _http("a", "provider", "/x"),
        _http("web", "consumer", "/x", host="api.stripe.com"),
        _http("web", "consumer", "/x", host="orders.svc.cluster.local"),
    ]
    annotate_consumer_targets(contracts, {}, _names(orders="a"))
    assert contracts[1].meta.get("external") is True
    assert contracts[2].meta["target_repo"] == "a"


def test_an_absent_map_changes_nothing() -> None:
    contracts = [_http("a", "provider", "/x"), _http("web", "consumer", "/x", host="svc")]
    annotate_consumer_targets(contracts)
    assert "target_repo" not in contracts[-1].meta
    [link] = run_matching(contracts).links
    assert link.strategy == LinkStrategy.EXACT_ID


def test_aspire_launch_profile_is_not_a_path(tmp_path: Path) -> None:
    host = tmp_path / "host"
    host.mkdir()
    (tmp_path / "catalog").mkdir()
    (host / "host.csproj").write_text(
        r'<Project><ItemGroup><ProjectReference Include="..\catalog\Catalog.API.csproj" />'
        "</ItemGroup></Project>"
    )
    (host / "AppHost.cs").write_text(
        "var b = DistributedApplication.CreateBuilder(args);\n"
        'b.AddProject<Projects.Catalog_API>("catalog-api", "https");\n'
    )
    m = build_service_name_map(_repos(tmp_path, "host", "catalog"))
    assert _mapped(m) == {"catalog-api": ("catalog", "aspire_apphost")}


def test_k8s_external_name_service_is_not_a_repo(tmp_path: Path) -> None:
    (tmp_path / "pay" / "k8s").mkdir(parents=True)
    (tmp_path / "pay" / "k8s" / "svc.yaml").write_text(
        "kind: Service\nmetadata: {name: stripe-api}\n"
        "spec: {type: ExternalName, externalName: api.stripe.com}\n"
    )
    assert _mapped(build_service_name_map(_repos(tmp_path, "pay"))) == {}


def test_a_host_equal_to_a_repo_name_alone_draws_no_service_edge() -> None:
    contracts = [_http("api", "provider", "/x"), _http("web", "consumer", "/y", host="api")]
    annotate_consumer_targets(contracts)
    assert contracts[-1].meta["target_via"] == "repo_alias"
    assert run_matching(contracts).service_links == []
    # The same name placed by config is evidence.
    annotate_consumer_targets(contracts, {}, _names(api="api"))
    assert contracts[-1].meta["target_via"] == "compose"
    assert len(run_matching(contracts).service_links) == 1


def test_a_mapped_target_does_not_fall_back_to_another_repo() -> None:
    contracts = [
        _http("other", "provider", "/health"),
        _http("catalogue", "provider", "/products"),
        _http("web", "consumer", "/health", host="catalogue-svc"),
    ]
    annotate_consumer_targets(contracts, {}, _names(**{"catalogue-svc": "catalogue"}))
    state = run_matching(contracts)
    assert state.links == []
    assert [lk.provider_repo for lk in state.service_links] == ["catalogue"]


def test_the_stored_map_round_trips_and_its_fingerprint_tracks_its_inputs(tmp_path: Path) -> None:
    root = FIXTURES / "robotshop"
    repos = _repos(root, "catalogue", "mongo")
    m = build_service_name_map(repos, root)
    again = ServiceNameMap.from_dict(m.to_dict("fp"))
    assert again.entries == m.entries and again.repos == m.repos

    heads = {"a": "1", "b": "2"}
    (tmp_path / "README.md").write_text("x")
    fp = service_names_fingerprint(heads, tmp_path)
    assert service_names_fingerprint(heads, tmp_path) == fp
    (tmp_path / "notes.txt").write_text("not a gated file")
    assert service_names_fingerprint(heads, tmp_path) == fp
    assert service_names_fingerprint({**heads, "b": "3"}, tmp_path) != fp
    (tmp_path / "docker-compose.yml").write_text("services: {}\n")
    assert service_names_fingerprint(heads, tmp_path) != fp


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (r"..\Catalog.API\Catalog.API.csproj", "/w/Catalog.API/Catalog.API.csproj"),
        ("../cart", "/w/cart"),
        (r".\src\Web", "/w/host/src/Web"),
    ],
)
def test_config_paths_written_on_windows_resolve_on_any_os(ref: str, expected: str) -> None:
    # PurePosixPath keeps a backslash as a literal character on every OS.
    joined = join_config_path(PurePosixPath("/w/host"), ref)
    assert posixpath.normpath(str(joined)) == expected


def test_a_backslash_path_with_other_case_finds_its_repo(tmp_path: Path) -> None:
    host = tmp_path / "eshop.apphost"
    host.mkdir()
    (tmp_path / "catalog.api").mkdir()
    (host / "AppHost.cs").write_text(
        "var b = DistributedApplication.CreateBuilder(args);\n"
        r'b.AddProject("catalog-api", @"..\Catalog.API\Catalog.API.csproj");' "\n"
    )
    m = build_service_name_map(_repos(tmp_path, "eshop.apphost", "catalog.api"))
    assert _mapped(m) == {"catalog-api": ("catalog.api", "aspire_apphost")}
