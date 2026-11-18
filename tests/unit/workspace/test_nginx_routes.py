"""nginx ``proxy_pass`` routes and Dockerfile ``ENV`` addresses in the service-name map."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.matching import annotate_consumer_targets, run_matching
from repowise.core.workspace.origins import LinkStrategy
from repowise.core.workspace.service_names import (
    ENV,
    HOST,
    ServiceNameMap,
    build_service_name_map,
    dockerfile,
    nginx,
)
from repowise.core.workspace.service_names.base import Route, SourceFile

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "service_names"
ROBOTSHOP = ("cart", "catalogue", "user", "shipping", "payment", "ratings", "web")


def _routes(m: ServiceNameMap) -> set[tuple[str, str, str | None, str]]:
    return {(r.gateway, r.prefix, r.upstream, r.repo) for r in m.routes}


def _nginx(text: str, name: str = "default.conf") -> list[tuple[str, str, Route]]:
    return [(c.kind, c.name, c.route) for c in nginx.read(SourceFile(Path(name), text)) if c.route]


def _http(repo: str, role: str, path: str) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::GET::{path}",
        contract_type="http",
        role=role,
        file_path=f"{role}.js",
        symbol_name=path,
        confidence=0.85 if role == "provider" else 0.75,
        meta={"method": "GET", "path": path},
    )


def _robotshop() -> ServiceNameMap:
    root = FIXTURES / "robotshop"
    return build_service_name_map({a: root / a for a in ROBOTSHOP}, root)


def test_robotshop_template_routes_each_prefix_through_its_env_host() -> None:
    m = _robotshop()
    # web/Dockerfile's ENV defaults say which host each ${X_HOST} placeholder names.
    assert m.lookup("catalogue_host", ENV).repo == "catalogue"  # type: ignore[union-attr]
    assert m.lookup("catalogue_host", ENV).source == "dockerfile_env"  # type: ignore[union-attr]
    assert _routes(m) == {
        ("web", f"/api/{svc}", "", svc)
        for svc in ("catalogue", "user", "cart", "shipping", "payment", "ratings")
    } | {
        # Served by nginx itself: blockers with no target.
        ("web", "/images", None, ""),
        ("web", "/50x.html", None, ""),
        ("web", "/nginx_status", None, ""),
    }
    [route] = [r for r in m.routes if r.repo == "ratings"]
    assert (route.name, route.source, route.file) == (
        "ratings_host", "nginx", "web/default.conf.template"
    )


def test_calls_from_the_nginx_repo_link_on_the_stripped_path() -> None:
    m = _robotshop()
    contracts = [
        _http("catalogue", "provider", "/products"),
        _http("web", "consumer", "/api/catalogue/products"),  # the nginx repo's own page
    ]
    annotate_consumer_targets(contracts, {}, m)
    assert contracts[1].meta["route_path"] == "/products"
    state = run_matching(contracts, m.routes)
    assert [(lk.consumer_repo, lk.provider_repo, lk.contract_id, lk.strategy) for lk in state.links] == [
        ("web", "catalogue", "http::GET::/products", LinkStrategy.GATEWAY_ROUTE)
    ]
    assert {lk.provider_repo for lk in state.service_links} == {
        "catalogue", "user", "cart", "shipping", "payment", "ratings"
    }


def test_proxy_pass_shapes() -> None:
    text = """
    upstream backend { server cart:8080 weight=2; server cart:8081; }
    upstream pool { server cart:8080; server user:8080; }
    server {
        listen 80;
        location / { root /usr/share/nginx/html; }
        location /assets/ { proxy_pass http://catalogue; }
        # location /old/ { proxy_pass http://user:8080/; }
        location /api/v2/ { proxy_pass http://user:8080/v2/; }
        location ^~ /api/cart/ { proxy_pass http://backend/; }
        location /api/pool/ { proxy_pass http://pool/; }
        location = /api/exact { proxy_pass http://user:8080/; }
        location /api/rw/ { rewrite ^/api/rw/(.*)$ /$1 break; proxy_pass http://user:8080; }
        location /api/var/ { proxy_pass http://user:8080/$request_uri; }
        location /api/rt/ { proxy_pass http://$upstream_host; }
        location ~ ^/api/re/(.*)$ { proxy_pass http://user:8080/$1; }
        location /api/ext/ { proxy_pass https://api.example.com/; }
        location /api/nested/ {
            location /api/nested/deep/ { proxy_pass "http://catalogue:8080/deep/"; }
        }
        location /api/static/ { root /srv; }
        location /api/slash/ { proxy_pass http://user:8080/v2; }
        location /api/frag/ { proxy_pass http://user:8080/x#y; }
        location /api/port/ { proxy_pass http://backend:9000/; }
    }
    server {
        rewrite ^/r/(.*)$ /$1 break;
        location /r/ { proxy_pass http://user:8080; }
    }
    """
    assert _nginx(text) == [
        (HOST, "catalogue", Route("/assets", "/assets")),  # no path: forwarded unchanged
        (HOST, "user", Route("/api/v2", "/v2")),
        (HOST, "cart", Route("/api/cart", "")),  # one host behind the upstream block
        (HOST, "", Route("/api/pool", "")),  # a pool of two services: no one target
        (HOST, "user", Route("/api/exact", None)),
        (HOST, "user", Route("/api/rw", None)),
        (HOST, "user", Route("/api/var", None)),
        (HOST, "", Route("/api/rt", "/api/rt")),  # a runtime host: no target
        (HOST, "api.example.com", Route("/api/ext", "")),
        (HOST, "", Route("/api/nested", None)),  # served here: a blocker
        (HOST, "catalogue", Route("/api/nested/deep", "/deep")),
        (HOST, "", Route("/api/static", None)),
        (HOST, "user", Route("/api/slash", None)),  # /api/x would go out as /v2x
        (HOST, "user", Route("/api/frag", None)),  # a # inside a word is not a comment
        (HOST, "backend", Route("/api/port", "")),  # with a port it is a host, not the group
        (HOST, "user", Route("/r", None)),  # a server-level rewrite changes the path
    ]


def test_placeholders_are_env_keys_only_in_a_template() -> None:
    text = "server { location /api/x/ { proxy_pass http://${X_HOST}:8080/; } }"
    assert _nginx(text, "default.conf.template") == [(ENV, "x_host", Route("/api/x", ""))]
    # In a plain config ${x} is an nginx variable, known only at runtime.
    assert _nginx(text, "default.conf") == [(HOST, "", Route("/api/x", ""))]
    # An unbalanced brace ends the parse without an error.
    assert _nginx("server { location /a/ { proxy_pass http://a/; }") == [(HOST, "a", Route("/a", ""))]


def test_dockerfile_env_forms() -> None:
    text = (
        "FROM nginx\n"
        "ENV CATALOGUE_HOST=catalogue \\\n    CART_URL=\"http://cart:8080/x\" \\\n"
        "    INSTANA_SERVICE_NAME=nginx-web\n"
        "ENV USER_ENDPOINT user:8080\n"
        "ENV MYSQL_USER=root DB_HOST=${DB}\n"
    )
    claims = list(dockerfile.read(SourceFile(Path("Dockerfile"), text)))
    assert {(c.name, c.ref) for c in claims} == {
        ("catalogue_host", "catalogue"),
        ("cart_url", "cart"),
        ("user_endpoint", "user"),
    }
    assert all(c.kind == ENV for c in claims)


def test_a_key_compose_sets_to_another_host_is_dropped(tmp_path: Path) -> None:
    for name in ("web", "catalogue", "catalogue-v2"):
        (tmp_path / name).mkdir()
    (tmp_path / "web" / "Dockerfile").write_text("ENV CATALOGUE_HOST=catalogue\n")
    (tmp_path / "web" / "default.conf.template").write_text(
        "server { location /api/catalogue/ { proxy_pass http://${CATALOGUE_HOST}:8080/; } }"
    )
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  web:\n    environment:\n      CATALOGUE_HOST: catalogue-v2\n"
    )
    m = build_service_name_map(
        {n: tmp_path / n for n in ("web", "catalogue", "catalogue-v2")}, tmp_path
    )
    assert m.lookup("catalogue_host", ENV) is None
    # The route stays as a blocker: it rewrites nothing and draws no edge.
    assert _routes(m) == {("web", "/api/catalogue", "", "")}


def test_an_empty_quoted_argument_keeps_its_place() -> None:
    blocks = nginx._parse('location ^~ "" { }\nproxy_set_header Host "" ;')
    assert [(b.name, b.args) for b in blocks] == [
        ("location", ["^~", ""]),
        ("proxy_set_header", ["Host", ""]),
    ]
