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
    # A field set from a constructor argument, and a static field read through
    # `this`: neither is a constant this can settle, so the call is left out
    # rather than read as `/generate`.
    text = (
        "class Plans {\n"
        "  private static readonly BASE_URL = '/api/v1/study-plan';\n"
        "  constructor(private baseUrl: string) { this.baseUrl = baseUrl; }\n"
        "  static generate() { return apiRequest(`${this.BASE_URL}/generate`, { method: 'POST' }); }\n"
        "  list() { return apiRequest(`${this.baseUrl}/plans`); }\n"
        "}\n"
    )
    assert _paths(tmp_path, "src/plans.ts", text) == set()


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
    assert not paths & {"/debug/a", "/failed/a", "/seen/a", "/other/a", "/a"}


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
    # Only the wrapper's own inner call is read; the call through it is not.
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
