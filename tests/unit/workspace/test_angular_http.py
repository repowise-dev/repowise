"""Angular HttpClient consumers, with the base an environment file or a class field names."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.js_clients import http_resource_calls
from repowise.core.workspace.matching import match_contracts

_IMPORT = "import { HttpClient } from '@angular/common/http';\n"
_ENV = "export const environment = {\n  production: false,\n  apiUrl: 'http://localhost:4000/api',\n};\n"


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _calls(repo: Path) -> list[tuple[str, str, str, str]]:
    return sorted(
        (c.meta["client"], c.meta["method"], c.meta["path"], c.meta.get("base_token", ""))
        for c in HttpExtractor().extract(repo, "web")
        if c.role == "consumer"
    )


def _service(body: str, ctor: str = "private http: HttpClient") -> str:
    return f"{_IMPORT}export class Api {{\n  constructor({ctor}) {{}}\n{body}}}\n"


class TestReceivers:
    def test_verbs_on_a_constructor_parameter_property(self, tmp_path: Path) -> None:
        body = (
            "  list() { return this.http.get<User[]>('/api/users'); }\n"
            "  make(u: User) { return this.http.post<User>('/api/users', u, { params }); }\n"
            "  edit(id: string) { return this.http.patch(`/api/users/${id}`, {}); }\n"
            "  drop(id: string) { return this.http.delete<void>(`/api/users/${id}`); }\n"
        )
        assert _calls(_write(tmp_path, {"src/api.ts": _service(body)})) == [
            ("angular", "DELETE", "/api/users/{param}", ""),
            ("angular", "GET", "/api/users", ""),
            ("angular", "PATCH", "/api/users/{param}", ""),
            ("angular", "POST", "/api/users", ""),
        ]

    def test_inject_and_typed_fields_under_any_name(self, tmp_path: Path) -> None:
        text = (
            "import { HttpClient as Http } from '@angular/common/http';\n"
            "export class A {\n  private readonly api = inject(Http);\n"
            "  private rest!: Http;\n"
            "  a() { return this.api.get('/api/a'); }\n  b() { return this.rest.put('/api/b', x); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": text})) == [
            ("angular", "GET", "/api/a", ""),
            ("angular", "PUT", "/api/b", ""),
        ]

    def test_request_takes_the_method_first(self, tmp_path: Path) -> None:
        body = (
            "  a() { return this.http.request('POST', '/api/a', { body }); }\n"
            "  b(req: HttpRequest<unknown>) { return this.http.request(req); }\n"
            "  c(m: string) { return this.http.request(m, '/api/c'); }\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": _service(body)})) == [
            ("angular", "POST", "/api/a", ""),
        ]

    def test_no_import_no_client(self, tmp_path: Path) -> None:
        text = (
            "export class A {\n  constructor(private http: HttpClient) {}\n"
            "  a() { return this.http.get('/api/a'); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": text})) == []

    def test_a_longer_type_name_and_a_bare_call_are_not_clients(self, tmp_path: Path) -> None:
        text = (
            f"{_IMPORT}export class A {{\n  constructor(private http: MyHttpClient, private h: HttpClient) {{}}\n"
            "  a() { this.http.get('/api/a'); this.h.get('/api/c'); return h('/api/b'); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": text})) == [("angular", "GET", "/api/c", "")]

    def test_head_options_lowercase_request_and_nested_generics(self, tmp_path: Path) -> None:
        body = (
            "  a() { return this.http.head('/api/a'); }\n"
            "  b() { return this.http.options('/api/b'); }\n"
            "  c() { return this.http.request('get', '/api/c'); }\n"
            "  d() { return this.http.get<Page<User>>('/api/d'); }\n"
            "  e() { return this.http.jsonp('/api/e', 'cb'); }\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": _service(body)})) == [
            ("angular", "GET", "/api/c", ""),
            ("angular", "GET", "/api/d", ""),
            ("angular", "HEAD", "/api/a", ""),
            ("angular", "OPTIONS", "/api/b", ""),
        ]

    def test_a_method_option_does_not_make_a_second_wrapper_row(self, tmp_path: Path) -> None:
        body = "  pay() { return this.http.post('/api/pay', { method: 'card' }); }\n"
        assert _calls(_write(tmp_path, {"src/a.ts": _service(body)})) == [
            ("angular", "POST", "/api/pay", ""),
        ]


class TestEnvironmentBase:
    def test_an_imported_environment_folds(self, tmp_path: Path) -> None:
        body = "  a() { return this.http.get(`${environment.apiUrl}/users/${id}`); }\n"
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": _ENV,
                "src/app/api.ts": "import { environment } from '../environments/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/api/users/{param}", "")]

    def test_a_path_alias_finds_the_nearest_environments_directory(self, tmp_path: Path) -> None:
        body = "  a() { return this.http.get(environment.apiUrl + '/orders'); }\n"
        other = _ENV.replace("/api", "/other")
        repo = _write(
            tmp_path,
            {
                "apps/web/src/environments/environment.ts": _ENV,
                "apps/admin/src/environments/environment.ts": other,
                "src/environments/environment.ts": _ENV.replace("/api", "/root"),
                "apps/web/src/app/api.ts": "import { environment } from '@env/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/api/orders", "")]

    def test_an_aliased_import_and_a_nested_member(self, tmp_path: Path) -> None:
        env = "export const environment = { api: { base: '/api/v2' } };\n"
        body = "  a() { return this.http.get(`${env.api.base}/x`); }\n"
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": env,
                "src/app/api.ts": "import { environment as env } from '../environments/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/api/v2/x", "")]

    def test_a_package_named_environment_is_not_looked_up(self, tmp_path: Path) -> None:
        body = "  a() { return this.http.get(`${environment.apiUrl}/x`); }\n"
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": _ENV,
                "src/app/api.ts": "import { environment } from '@ngrx/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/x", "apiUrl")]

    def test_an_unreadable_member_stays_a_base_token(self, tmp_path: Path) -> None:
        env = "export const environment = { apiUrl: process.env['API_URL'] };\n"
        body = "  a() { return this.http.get(`${environment.apiUrl}/users`); }\n"
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": env,
                "src/app/api.ts": "import { environment } from '../environments/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/users", "apiUrl")]

    def test_a_const_outside_environments_does_not_travel(self, tmp_path: Path) -> None:
        body = "  a() { return this.http.get(`${environment.apiUrl}/users`); }\n"
        repo = _write(
            tmp_path,
            {
                "src/config/environment.ts": _ENV,
                "src/app/api.ts": "import { environment } from '../config/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/users", "apiUrl")]


class TestClassFields:
    def test_a_field_built_on_the_environment(self, tmp_path: Path) -> None:
        body = (
            "  private readonly base = `${environment.apiUrl}/users`;\n"
            "  one(id: string) { return this.http.get(`${this.base}/${id}`); }\n"
            "  all() { return this.http.get(this.base); }\n"
        )
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": _ENV,
                "src/app/api.ts": "import { environment } from '../environments/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [
            ("angular", "GET", "/api/users", ""),
            ("angular", "GET", "/api/users/{param}", ""),
        ]

    def test_a_field_set_in_the_constructor(self, tmp_path: Path) -> None:
        text = (
            f"{_IMPORT}import {{ environment }} from '../environments/environment';\n"
            "export class A {\n  private baseUrl: string;\n"
            "  constructor(private http: HttpClient) {\n    this.baseUrl = environment.apiUrl;\n  }\n"
            "  a() { return this.http.get(`${this.baseUrl}/auth.json`); }\n}\n"
        )
        repo = _write(tmp_path, {"src/environments/environment.ts": _ENV, "src/app/a.ts": text})
        assert _calls(repo) == [("angular", "GET", "/api/auth.json", "")]

    def test_a_field_built_on_another_field(self, tmp_path: Path) -> None:
        body = (
            "  private base = environment.apiUrl;\n"
            "  private users = `${this.base}/users`;\n"
            "  a(id: string) { return this.http.get(`${this.users}/${id}`); }\n"
        )
        repo = _write(
            tmp_path,
            {
                "src/environments/environment.ts": _ENV,
                "src/app/api.ts": "import { environment } from '../environments/environment';\n" + _service(body),
            },
        )
        assert _calls(repo) == [("angular", "GET", "/api/users/{param}", "")]

    def test_fields_that_are_not_constants_are_refused(self, tmp_path: Path) -> None:
        text = (
            f"{_IMPORT}const base = '/wrong';\nconst url = '/wrong';\n"
            "export class A {\n  private url: string;\n  static prefix = '/static';\n"
            "  private grow = '/api';\n"
            "  constructor(private http: HttpClient, base: string) { this.base = base; }\n"
            "  more() { this.grow += '/v2'; }\n"
            "  a() {\n    let url = '/x';\n    url = '/y';\n"
            "    this.http.get(`${this.base}/a`);\n    this.http.get(`${this.url}/b`);\n"
            "    this.http.get(`${this.prefix}/c`);\n    return this.http.get(`${this.grow}/d`);\n  }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": text})) == [
            ("angular", "GET", "/a", "base"),
            ("angular", "GET", "/b", "url"),
            ("angular", "GET", "/c", "prefix"),
            ("angular", "GET", "/d", "grow"),
        ]

    def test_a_field_belongs_to_its_own_class(self, tmp_path: Path) -> None:
        text = (
            f"{_IMPORT}export class A {{\n  private base = '/api/a';\n"
            "  constructor(private http: HttpClient) {}\n"
            "  a() { return this.http.get(`${this.base}/x`); }\n}\n"
            "export class B extends Base {\n  constructor(private http: HttpClient) { super(); }\n"
            "  b() { return this.http.get(`${this.base}/y`); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": text})) == [
            ("angular", "GET", "/api/a/x", ""),
            ("angular", "GET", "/y", "base"),
        ]

    def test_a_field_set_twice_is_not_folded(self, tmp_path: Path) -> None:
        body = (
            "  private base = '/api/v1';\n"
            "  use2() { this.base = '/api/v2'; }\n"
            "  a() { return this.http.get(`${this.base}/users`); }\n"
        )
        assert _calls(_write(tmp_path, {"src/a.ts": _service(body)})) == [
            ("angular", "GET", "/users", "base"),
        ]

    def test_an_inherited_class_field_resolves(self, tmp_path: Path) -> None:
        text = (
            f"{_IMPORT}class Base {{\n  protected resourceUrl = '/api/items';\n}}\n"
            "export class ItemService extends Base {\n"
            "  constructor(private http: HttpClient) { super(); }\n"
            "  list() { return this.http.get(this.resourceUrl); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"src/item.service.ts": text})) == [
            ("angular", "GET", "/api/items", ""),
        ]

    def test_base_instance_fields_do_not_leak_into_static_members(self, tmp_path: Path) -> None:
        text = (
            "class Base {\n"
            "  protected instanceUrl = '/api/instance';\n"
            "  static staticUrl = '/api/static';\n"
            "}\n"
            "export class Sub extends Base {\n"
            "  static load() { return fetch(`${this.instanceUrl}`); }\n"
            "  static loadStatic() { return fetch(`${this.staticUrl}`); }\n"
            "}\n"
        )
        _write(tmp_path, {"src/sub.ts": text})
        paths = {c.meta["path"] for c in HttpExtractor().extract(tmp_path, "web") if c.role == "consumer"}
        assert "/api/static" in paths
        assert "/api/instance" not in paths


class TestHttpResource:
    def test_shorthand_url_property_and_arrow_shorthand(self, tmp_path: Path) -> None:
        text = (
            "const url = '/api/shorthand';\n"
            "export class ItemService {\n"
            "  res1 = httpResource({ url });\n"
            "  res2 = httpResource(() => ({ url }));\n"
            "}\n"
        )
        assert _calls(_write(tmp_path, {"src/shorthand.ts": text})) == [
            ("angular", "GET", "/api/shorthand", ""),
            ("angular", "GET", "/api/shorthand", ""),
        ]

    def test_generic_and_member_named_url(self, tmp_path: Path) -> None:
        text = (
            "export class Service {\n"
            "  url = '/api/from-member';\n"
            "  res1 = httpResource<User[]>('/api/generic');\n"
            "  res2 = httpResource(this.url);\n"
            "  res3 = httpResource(() => this.url);\n"
            "}\n"
        )
        assert _calls(_write(tmp_path, {"src/generic.ts": text})) == [
            ("angular", "GET", "/api/from-member", ""),
            ("angular", "GET", "/api/from-member", ""),
            ("angular", "GET", "/api/generic", ""),
        ]

    def test_literal_and_field_forms(self, tmp_path: Path) -> None:
        text = (
            "class Base { protected resourceUrl = '/api/items'; }\n"
            "export class ItemService extends Base {\n"
            "  res1 = httpResource({ url: '/api/other' });\n"
            "  res2 = httpResource({ url: this.resourceUrl });\n"
            "  res3 = httpResource(() => ({ url: this.resourceUrl }));\n"
            "  res4 = httpResource({ url: '/api/post', method: 'POST' });\n"
            "  res5 = httpResource(() => '/api/arrow');\n"
            "}\n"
        )
        assert _calls(_write(tmp_path, {"src/service.ts": text})) == [
            ("angular", "GET", "/api/arrow", ""),
            ("angular", "GET", "/api/items", ""),
            ("angular", "GET", "/api/items", ""),
            ("angular", "GET", "/api/other", ""),
            ("angular", "POST", "/api/post", ""),
        ]

    def test_template_literal_interpolation_and_commas(self, tmp_path: Path) -> None:
        text = (
            "class Base { protected base = '/api'; }\n"
            "export class Service extends Base {\n"
            "  res1 = httpResource({ url: `${this.base}/items` });\n"
            "  res2 = httpResource(() => ({ url: `${this.base}/users` }));\n"
            "  res3 = httpResource({ url: `${this.base}/items/${id}`, method: 'POST' });\n"
            "}\n"
        )
        assert _calls(_write(tmp_path, {"src/service.ts": text})) == [
            ("angular", "GET", "/api/items", ""),
            ("angular", "GET", "/api/users", ""),
            ("angular", "POST", "/api/items/{param}", ""),
        ]
        calls = list(http_resource_calls("httpResource({ url: buildUrl(id, x), method: 'GET' })"))
        assert len(calls) == 1
        assert calls[0].url == "buildUrl(id, x)"


def test_an_angular_service_links_to_nest_and_laravel(tmp_path: Path) -> None:
    nest = _write(
        tmp_path / "api",
        {
            "src/main.ts": "app.setGlobalPrefix('api');\n",
            "src/users.controller.ts": (
                "import { Controller, Get } from '@nestjs/common';\n"
                "@Controller('users')\nexport class UsersController {\n  @Get(':id')\n  one() {}\n}\n"
            ),
        },
    )
    laravel = _write(
        tmp_path / "tickets",
        {
            "routes/api.php": (
                "<?php\nuse App\\Http\\Controllers\\TicketController;\n"
                "Route::post('/tickets/{ticket}/refund', [TicketController::class, 'refund']);\n"
            ),
        },
    )
    body = (
        "  private readonly users = `${environment.apiUrl}/users`;\n"
        "  user(id: string) { return this.http.get<User>(`${this.users}/${id}`); }\n"
        "  refund(id: string) { return this.http.post(`/api/tickets/${id}/refund`, {}); }\n"
        "  private other = '/api/v1';\n  swap() { this.other = '/api/v2'; }\n"
        "  guess(id: string) { return this.http.get(`${this.other}/orders/${id}`); }\n"
    )
    web = _write(
        tmp_path / "web",
        {
            "src/environments/environment.ts": _ENV,
            "src/app/api.service.ts": "import { environment } from '../environments/environment';\n" + _service(body),
        },
    )
    contracts = (
        HttpExtractor().extract(nest, "api")
        + HttpExtractor().extract(laravel, "tickets")
        + HttpExtractor().extract(web, "web")
    )
    links = sorted((link.provider_repo, link.consumer_repo, link.contract_id) for link in match_contracts(contracts))
    assert links == [
        ("api", "web", "http::GET::/api/users/{param}"),
        ("tickets", "web", "http::POST::/api/tickets/{param}/refund"),
    ]
