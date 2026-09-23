"""axios, ky, got and ofetch consumers, directly and through instances with a base.

An instance is usually created in one module and called from many, so every
test here extracts a whole repo: the base travels with the instance's export.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.matching import match_contracts


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _calls(repo: Path) -> set[tuple[str, str, str, str]]:
    return {
        (c.meta["client"], c.meta["method"], c.meta["path"], c.meta.get("base_token", ""))
        for c in HttpExtractor().extract(repo, "web")
        if c.role == "consumer"
    }


class TestDirectCalls:
    def test_axios_verbs_config_and_folded_urls(self, tmp_path: Path) -> None:
        text = (
            "import axios from 'axios';\n"
            "const USERS = '/api/users';\n"
            "axios.post('/api/tickets/refund', body);\n"
            "axios.get<User[]>(USERS);\n"
            "axios.delete(`${USERS}/${id}`);\n"
            "axios({ url: '/api/orders', method: 'put' });\n"
            "axios.request({ url: '/api/sync', method: 'POST' });\n"
            "axios('/api/ping');\n"
        )
        assert _calls(_write(tmp_path, {"a.ts": text})) == {
            ("axios", "POST", "/api/tickets/refund", ""),
            ("axios", "GET", "/api/users", ""),
            ("axios", "DELETE", "/api/users/{param}", ""),
            ("axios", "PUT", "/api/orders", ""),
            ("axios", "POST", "/api/sync", ""),
            ("axios", "GET", "/api/ping", ""),
        }

    def test_ky_and_got_need_their_import(self, tmp_path: Path) -> None:
        text = (
            "import ky from 'ky';\nimport got from 'got';\n"
            "ky.post('/api/a');\nky('/api/b', { method: 'patch' });\ngot.delete('/api/c');\n"
        )
        repo = _write(tmp_path, {"a.ts": text, "b.ts": "ky.post('/api/x');\ngot('/api/y');\n"})
        assert _calls(repo) == {
            ("ky", "POST", "/api/a", ""),
            ("ky", "PATCH", "/api/b", ""),
            ("got", "DELETE", "/api/c", ""),
        }

    def test_a_direct_ofetch_call_is_read_once(self, tmp_path: Path) -> None:
        text = "import { ofetch } from 'ofetch';\nofetch('/api/a');\n"
        assert _calls(_write(tmp_path, {"a.ts": text})) == {("fetch", "GET", "/api/a", "")}


class TestInstances:
    _HTTP = (
        "import axios from 'axios';\n"
        "export const api = axios.create({ baseURL: '/api/v2' });\n"
        "export const remote = axios.create({ baseURL: process.env.API_URL });\n"
        "const internal = axios.create({ baseURL: 'http://svc:8000/internal' });\n"
        "export { internal as svc };\n"
        "export default api;\n"
    )

    def test_a_base_composes_in_the_creating_file(self, tmp_path: Path) -> None:
        text = self._HTTP + "api.get('/users');\ninternal.post('jobs');\n"
        assert _calls(_write(tmp_path, {"src/lib/http.ts": text})) == {
            ("axios", "GET", "/api/v2/users", ""),
            ("axios", "POST", "/internal/jobs", ""),
        }

    def test_named_aliased_and_default_imports(self, tmp_path: Path) -> None:
        caller = (
            "import http, { remote as r, svc } from '@/lib/http';\n"
            "export const a = () => http.get(`/users/${id}`);\n"
            "export const b = () => r.post('orders');\n"
            "export const c = () => svc.delete('/jobs/1');\n"
        )
        repo = _write(tmp_path, {"src/lib/http.ts": self._HTTP, "src/api/users.ts": caller})
        assert _calls(repo) == {
            ("axios", "GET", "/api/v2/users/{param}", ""),
            ("axios", "POST", "/orders", "API_URL"),
            ("axios", "DELETE", "/internal/jobs/1", ""),
        }

    def test_a_default_export_from_an_index_module(self, tmp_path: Path) -> None:
        index = "import ky from 'ky';\nexport default ky.create({ prefixUrl: '/api' });\n"
        caller = "import client from '../client';\nclient.get('things');\n"
        repo = _write(tmp_path, {"src/client/index.ts": index, "src/pages/p.ts": caller})
        assert _calls(repo) == {("ky", "GET", "/api/things", "")}

    def test_got_extend_ofetch_create_and_class_fields(self, tmp_path: Path) -> None:
        text = (
            "import got from 'got';\nimport { ofetch } from 'ofetch';\n"
            "const g = got.extend({ prefixUrl: 'https://billing.internal/v1' });\n"
            "const f = ofetch.create({ baseURL: '/api' });\n"
            "g.post('invoices');\nf('/carts', { method: 'DELETE' });\n"
            "class Svc {\n  private http = axios.create({ baseURL: '/svc' });\n"
            "  run() { return this.http.get('/run'); }\n}\n"
        )
        assert _calls(_write(tmp_path, {"a.ts": text})) == {
            ("got", "POST", "/v1/invoices", ""),
            ("ofetch", "DELETE", "/api/carts", ""),
            ("axios", "GET", "/svc/run", ""),
        }

    def test_an_instance_call_is_not_also_a_wrapper_call(self, tmp_path: Path) -> None:
        text = "import axios from 'axios';\nconst apiClient = axios.create({ baseURL: '/api' });\napiClient('/users');\n"
        assert _calls(_write(tmp_path, {"a.ts": text})) == {("axios", "GET", "/api/users", "")}

    def test_two_instances_exported_under_one_name_are_neither(self, tmp_path: Path) -> None:
        make = "import axios from 'axios';\nexport const api = axios.create({{ baseURL: '{}' }});\n"
        repo = _write(
            tmp_path,
            {
                "a/http.ts": make.format("/a"),
                "b/http.ts": make.format("/b"),
                "c.ts": "import { api } from './a/http';\napi.get('/users');\n",
                "d/client.ts": make.format("/d"),
                "e.ts": "import { api } from './d/client';\napi.get('/users');\n",
            },
        )
        assert _calls(repo) == {("axios", "GET", "/d/users", "")}

    def test_a_relative_path_without_a_base_and_non_calls_are_skipped(self, tmp_path: Path) -> None:
        text = (
            "import axios from 'axios';\nconst api = axios.create({ timeout: 5 });\n"
            "api.get('users');\napi.interceptors.request.use(fn);\naxios.defaults.baseURL = '/x';\n"
            "axios.get('/api/ok');\n"
        )
        assert _calls(_write(tmp_path, {"a.ts": text})) == {("axios", "GET", "/api/ok", "")}


def test_an_instance_call_links_to_a_nest_controller(tmp_path: Path) -> None:
    api = _write(
        tmp_path / "api",
        {
            "src/main.ts": "app.setGlobalPrefix('api');\n",
            "src/users.controller.ts": (
                "import { Controller, Get } from '@nestjs/common';\n"
                "@Controller('users')\nexport class UsersController {\n  @Get(':id')\n  one() {}\n}\n"
            ),
        },
    )
    web = _write(
        tmp_path / "web",
        {
            "src/http.ts": "import axios from 'axios';\nexport const api = axios.create({ baseURL: '/api' });\n",
            "src/users.ts": "import { api } from './http';\napi.get(`/users/${id}`);\n",
        },
    )
    contracts = HttpExtractor().extract(api, "api") + HttpExtractor().extract(web, "web")
    links = {(link.provider_repo, link.consumer_repo, link.contract_id) for link in match_contracts(contracts)}
    assert links == {("api", "web", "http::GET::/api/users/{param}")}


class TestReviewedEdges:
    _HTTP = "import axios from 'axios';\nexport default axios.create({ baseURL: '/api' });\nexport const client = axios.create({ baseURL: '/c' });\n"

    def test_package_imports_and_other_modules_are_not_instances(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "src/lib/http.ts": self._HTTP,
                "src/server.ts": "import http from 'http';\nimport { client } from './redis';\nhttp.get('/health');\nclient.get('/x');\n",
                "src/ok.ts": "import http from './lib/http';\nhttp.get('/users');\n",
            },
        )
        assert sorted(_calls(repo)) == [("axios", "GET", "/api/users", "")]

    def test_global_and_injected_axios(self, tmp_path: Path) -> None:
        text = "window.axios.get('/api/users');\nthis.$axios.get('/api/items');\nVue.axios.post('/api/v');\nfoo.axios.get('/nope');\n"
        assert _calls(_write(tmp_path, {"a.js": text})) == {
            ("axios", "GET", "/api/users", ""),
            ("axios", "GET", "/api/items", ""),
            ("axios", "POST", "/api/v", ""),
        }

    def test_dollar_and_fetch_named_instances_are_read_once(self, tmp_path: Path) -> None:
        text = (
            "import { ofetch } from 'ofetch';\nimport axios from 'axios';\n"
            "export const $api = ofetch.create({ baseURL: '/api' });\n"
            "const apifetch = ofetch.create({ baseURL: '/f' });\n"
            "const $http = axios.create({ baseURL: '/h' });\n"
            "$api('/users');\napifetch('/x');\n$http('/y');\n"
        )
        rows = [
            (c.meta["client"], c.meta["path"])
            for c in HttpExtractor().extract(_write(tmp_path, {"a.ts": text}), "web")
            if c.role == "consumer"
        ]
        assert sorted(rows) == [("axios", "/h/y"), ("ofetch", "/api/users"), ("ofetch", "/f/x")]

    def test_factories_each_library_has(self, tmp_path: Path) -> None:
        text = (
            "import axios from 'axios';\nimport ky from 'ky';\nimport got from 'got';\n"
            "const a = axios.extend({ baseURL: '/a' });\nconst g = got.create({ prefixUrl: '/g' });\n"
            "const k = ky.extend({ prefixUrl: '/k' });\n"
            "a.get('/1');\ng.get('2');\nk.get('3');\nk.request({ url: '/4' });\n"
        )
        assert _calls(_write(tmp_path, {"a.ts": text})) == {("ky", "GET", "/k/3", "")}

    def test_an_instance_extended_from_an_import_keeps_its_base(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            {
                "src/http.js": "const axios = require('axios');\nconst api = axios.create({ baseURL: '/api' });\nexport { api as default };\n",
                "src/b.ts": "import api from './http';\nconst slow = api.create({ timeout: 5 });\nslow.get('/users');\napi.get('https://ext.io/x');\n",
            },
        )
        assert _calls(repo) == {("axios", "GET", "/api/users", ""), ("axios", "GET", "/x", "")}

    def test_an_ofetch_instance_has_no_verb_methods(self, tmp_path: Path) -> None:
        text = "import { ofetch } from 'ofetch';\nconst f = ofetch.create({ baseURL: '/api' });\nf.get('/x');\nf('/y');\n"
        assert _calls(_write(tmp_path, {"a.ts": text})) == {("ofetch", "GET", "/api/y", "")}


def test_a_base_in_the_calls_own_options_wins(tmp_path: Path) -> None:
    text = (
        "import axios from 'axios';\nconst api = axios.create({ baseURL: '/api' });\n"
        "axios.get<T>('/ui-settings', { baseURL: this.getBaseUrl() });\n"
        "api.post('/jobs', body, { baseURL: '/v2' });\napi.get('/users', { timeout: 5 });\n"
    )
    assert _calls(_write(tmp_path, {"a.ts": text})) == {
        ("axios", "GET", "/ui-settings", "getBaseUrl"),
        ("axios", "POST", "/v2/jobs", ""),
        ("axios", "GET", "/api/users", ""),
    }
