"""A JS client call's base the URL leaves out: a class field, or a wrapper's own prefix."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.extractors.http import HttpExtractor


def _paths(repo: Path, rel: str, text: str) -> set[tuple[str, str]]:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {
        (c.meta["method"], c.meta["path"])
        for c in HttpExtractor().extract(repo, "web")
        if c.role == "consumer"
    }


def test_a_wrapper_that_prefixes_its_path_composes_the_prefix(tmp_path: Path) -> None:
    text = (
        "import { apiRequest as baseApiRequest } from '@/lib/apiClient';\n"
        "async function apiRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {\n"
        "  const fullEndpoint = `/api/resume-analyzer${endpoint}`;\n"
        "  return baseApiRequest(fullEndpoint, options);\n"
        "}\n"
        "export const list = () => apiRequest(`/user/analyses`);\n"
        "export const drop = (id: string) => apiRequest(`/analysis/${id}`, { method: 'DELETE' });\n"
    )
    assert _paths(tmp_path, "src/resume.ts", text) == {
        ("GET", "/api/resume-analyzer/user/analyses"),
        ("DELETE", "/api/resume-analyzer/analysis/{param}"),
    }


def test_a_class_wrapper_composes_its_field_base(tmp_path: Path) -> None:
    text = (
        "const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';\n"
        "class Analytics {\n"
        "  private baseUrl = `${API_BASE_URL}/api/analytics/ai-interview`;\n"
        "  private async makeRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {\n"
        "    const response = await fetch(`${this.baseUrl}${endpoint}`, { ...options });\n"
        "    return response.json();\n"
        "  }\n"
        "  async getOverview(userId: string) {\n"
        "    return this.makeRequest<Overview>(\n      `/overview/${userId}?date_range=30`\n    );\n"
        "  }\n"
        "}\n"
    )
    assert ("GET", "/api/analytics/ai-interview/overview/{param}") in _paths(
        tmp_path, "src/analytics.ts", text
    )


def test_a_call_through_a_field_reads_the_field(tmp_path: Path) -> None:
    text = (
        "class Pipecat {\n"
        "  private baseUrl = '/api/pipecat';\n"
        "  async end(id: string) {\n"
        "    return apiRequest(`${this.baseUrl}/sessions/${id}`, {\n      method: 'DELETE'\n    });\n"
        "  }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/pipecat.ts", text) == {("DELETE", "/api/pipecat/sessions/{param}")}


def test_a_base_that_does_not_resolve_is_not_matched_bare(tmp_path: Path) -> None:
    # A field set from a constructor argument is no constant this can settle,
    # so the call is left out rather than read as `/plans`.
    text = (
        "class Plans {\n"
        "  constructor(private baseUrl: string) { this.baseUrl = baseUrl; }\n"
        "  list() { return apiRequest(`${this.baseUrl}/plans`); }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/plans.ts", text) == set()


def test_a_static_method_reads_this_as_the_class(tmp_path: Path) -> None:
    # In a static method `this` is the class, so `this.BASE_URL` is the static field.
    text = (
        "class Plans {\n"
        "  private static readonly BASE_URL = '/api/v1/study-plan';\n"
        "  static generate() { return apiRequest(`${this.BASE_URL}/generate`, { method: 'POST' }); }\n"
        "  static async list(): Promise<Plan[]> {\n"
        "    return fetch(`${this.BASE_URL}/plans`);\n"
        "  }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/plans.ts", text) == {
        ("POST", "/api/v1/study-plan/generate"),
        ("GET", "/api/v1/study-plan/plans"),
    }


def test_a_static_field_read_through_the_class_name(tmp_path: Path) -> None:
    # `Api.BASE` is the static field from any member of the class and from
    # the rest of the file.
    text = (
        "export class Api {\n"
        "  static BASE = '/api';\n"
        "  static get() { return fetch(`${Api.BASE}/x`); }\n"
        "  list() { return fetch(`${Api.BASE}/items`); }\n"
        "}\n"
        "export function top() { return fetch(`${Api.BASE}/top`); }\n"
    )
    assert _paths(tmp_path, "src/api.ts", text) == {
        ("GET", "/api/x"),
        ("GET", "/api/items"),
        ("GET", "/api/top"),
    }


@pytest.mark.parametrize(
    "write",
    [
        "Api.BASE = '/v2';",
        "Api.BASE += '/v2';",
        "Api['BASE'] = '/v2';",
        "Object.assign(Api, { BASE: '/v2' });",
        "Object.defineProperty(Api, 'BASE', { value: '/v2' });",
        "Reflect.set(Api, 'BASE', '/v2');",
    ],
)
def test_a_static_field_the_file_writes_from_outside_is_no_constant(tmp_path: Path, write: str) -> None:
    text = (
        "export class Api {\n"
        "  static BASE = '/api';\n"
        "}\n"
        f"{write}\n"
        "export function top() { return fetch(`${Api.BASE}/top`); }\n"
    )
    assert ("GET", "/api/top") not in _paths(tmp_path, "src/api.ts", text)


def test_a_class_in_a_block_comment_gives_no_static_constant(tmp_path: Path) -> None:
    text = (
        "/*\n"
        "class Api {\n"
        "  static BASE = '/api';\n"
        "}\n"
        "*/\n"
        "export function top() { return fetch(`${Api.BASE}/top`); }\n"
    )
    assert ("GET", "/api/top") not in _paths(tmp_path, "src/api.ts", text)


def test_a_comment_opener_inside_a_string_keeps_the_static_constant(tmp_path: Path) -> None:
    text = (
        "const GLOB = 'src/*';\n"
        "export class Api {\n"
        "  static BASE = '/api';\n"
        "}\n"
        "export function top() { return fetch(`${Api.BASE}/top`); }\n"
    )
    assert ("GET", "/api/top") in _paths(tmp_path, "src/api.ts", text)


def test_a_static_field_a_static_member_rewrites_is_no_constant(tmp_path: Path) -> None:
    text = (
        "export class Api {\n"
        "  static BASE = '/api';\n"
        "  static init() { this.BASE = '/v2'; }\n"
        "}\n"
        "export function top() { return fetch(`${Api.BASE}/top`); }\n"
    )
    found = _paths(tmp_path, "src/api.ts", text)
    assert ("GET", "/api/top") not in found and ("GET", "/v2/top") not in found


def test_this_reads_static_fields_only_in_static_members(tmp_path: Path) -> None:
    # `this.BASE` in an instance method is no static field (it is undefined at
    # run time), and an instance field is not what a static method reads.
    text = (
        "class Api {\n"
        "  static BASE = '/api';\n"
        "  private root = '/root';\n"
        "  list() { return fetch(`${this.BASE}/items`); }\n"
        "  static all() { return fetch(`${this.root}/all`); }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/api.ts", text) == set()


def test_a_field_read_from_the_environment_stays_the_host(tmp_path: Path) -> None:
    text = (
        "class A {\n"
        "  private baseUrl = process.env.API_URL;\n"
        "  async g() { return fetch(`${this.baseUrl}/api/users`); }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/a.ts", text) == {("GET", "/api/users")}


def test_only_the_requested_template_is_the_prefix(tmp_path: Path) -> None:
    # A log line and a callback name the parameter too; neither is the URL sent.
    text = (
        "import { baseFetch } from './base';\n"
        "async function apiRequest(endpoint: string) {\n"
        "  console.log(`/debug${endpoint}`);\n"
        "  logger.error(`/failed${endpoint}`);\n"
        "  this.log.info(`/seen${endpoint}`);\n"
        "  items.map((x) => `/other${endpoint}`);\n"
        "  return baseFetch(`/api/v1${endpoint}`);\n"
        "}\n"
        "export const a = () => apiRequest('/a');\n"
    )
    paths = {p for _m, p in _paths(tmp_path, "src/b.ts", text)}
    assert "/api/v1/a" in paths
    assert not paths & {"/debug/a", "/failed/a", "/seen/a", "/other/a", "/a", "/api/v1"}


@pytest.mark.parametrize(
    "definition",
    [
        "async function apiFetch(endpoint: string) {\n  return baseFetch(`/api/v1${endpoint}`);\n}\n",
        "export const apiFetch = (endpoint: string) => fetch(`/api/v1${endpoint}`);\n",
        "export const apiFetch = async (endpoint: string, init?: RequestInit): Promise<Response> => {\n"
        "  const res = await fetch(`/api/v1${endpoint}`, init);\n  return res;\n};\n",
        "class Client {\n  async apiFetch(endpoint: string) {\n"
        "    return retry(() => axios.get(`/api/v1${endpoint}`));\n  }\n}\n",
    ],
)
def test_a_wrappers_own_request_is_no_route(tmp_path: Path, definition: str) -> None:
    # The path is the caller's: the wrapper's inner call names only its prefix.
    text = "import axios from 'axios';\nimport { baseFetch } from './base';\n" + definition
    assert "/api/v1" not in {p for _m, p in _paths(tmp_path, "src/w.ts", text)}


def test_a_trailing_hole_that_is_no_parameter_still_reads_its_path(tmp_path: Path) -> None:
    # A query string built in the body, a path parameter segment, a name some
    # other function takes, and a condition all keep their route.
    text = (
        "export function list(page: number) {\n"
        "  const qs = `?page=${page}`;\n"
        "  return fetch(`/api/repos${qs}`);\n"
        "}\n"
        "export function getUser(id: string) { return fetch(`/api/users/${id}`); }\n"
        "export function other(qs: string) { return qs; }\n"
        "export const more = () => fetch(`/api/more${qs}`);\n"
        "if (qs) { fetch(`/api/cond${qs}`); }\n"
        # An arrow body a line break ends, with no semicolon, before a loop
        # that reuses its parameter's name.
        "const norm = (endpoint) => endpoint.trim()\n"
        "for (const endpoint of endpoints) { fetch(`/api/loop${endpoint}`) }\n"
    )
    assert {p for _m, p in _paths(tmp_path, "src/l.ts", text)} == {
        "/api/repos",
        "/api/users/{param}",
        "/api/more",
        "/api/cond",
        "/api/loop",
    }


@pytest.mark.parametrize(
    "definition",
    [
        # The parameter is rebuilt before it is sent.
        "async function apiRequest(endpoint: string) {\n"
        "  endpoint = '/x' + endpoint;\n  return baseFetch(`/api/v1${endpoint}`);\n}\n",
        # Destructured: which value is the path cannot be read.
        "async function apiRequest({ endpoint }: Opts) {\n  return baseFetch(`/api${endpoint}`);\n}\n",
        # Two definitions with different prefixes.
        "function apiRequest(e: string) { return baseFetch(`/a${e}`); }\n"
        "const o = { apiRequest(e: string) { return baseFetch(`/b${e}`); } };\n",
    ],
)
def test_a_wrapper_prefix_that_cannot_be_read_leaves_the_call_out(
    tmp_path: Path, definition: str
) -> None:
    text = (
        "import { baseFetch } from './base';\n"
        + definition
        + "export const a = () => apiRequest('/users/list');\n"
    )
    # The call through the wrapper is not read on its bare path.
    assert not {p for _m, p in _paths(tmp_path, "src/c.ts", text) if p.endswith("/users/list")}


def test_a_call_on_another_object_does_not_take_the_local_prefix(tmp_path: Path) -> None:
    text = (
        "import { other } from './other';\n"
        "async function apiRequest(endpoint: string) { return baseFetch(`/api${endpoint}`); }\n"
        "export const a = () => other.apiRequest('/users/list');\n"
    )
    paths = {p for _m, p in _paths(tmp_path, "src/d.ts", text)}
    assert "/users/list" in paths
    assert "/api/users/list" not in paths


def test_a_receiver_named_like_a_logger_is_still_a_request(tmp_path: Path) -> None:
    text = (
        "async function apiRequest(endpoint: string) {\n"
        "  return logsApi.request(`/api/logs${endpoint}`);\n"
        "}\n"
        "export const a = () => apiRequest('/recent');\n"
    )
    assert "/api/logs/recent" in {p for _m, p in _paths(tmp_path, "src/e.ts", text)}
