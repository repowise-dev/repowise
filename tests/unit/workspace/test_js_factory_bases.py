"""A JS client factory whose URL base is a parameter: the base its call sites pass."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor

_FACTORY = (
    'import { apiRequest, type ApiRequest } from "./client";\n'
    "export function createEvidenceApi(request: ApiRequest, base: (repoId: string) => string) {\n"
    "  return {\n"
    "    getStatus: (repoId: string) => request<Status>(`${base(repoId)}/status`),\n"
    "    mark: (repoId: string, id: string) =>\n"
    '      request(`${base(repoId)}/items/${encodeURIComponent(id)}/mark`, { method: "PUT" }),\n'
    "  };\n"
    "}\n"
)


def _consumers(repo: Path, files: dict[str, str]) -> set[tuple[str, str]]:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return {
        (c.meta["method"], c.meta["path"])
        for c in HttpExtractor().extract(repo, "web")
        if c.role == "consumer"
    }


def test_one_base_from_another_file_is_put_before_each_request(tmp_path: Path) -> None:
    caller = (
        'import { createEvidenceApi } from "./evidence";\n'
        "export const api = createEvidenceApi(apiRequest, (repoId) => `/api/repos/${repoId}/evidence`);\n"
    )
    assert _consumers(tmp_path, {"src/evidence.ts": _FACTORY, "src/api.ts": caller}) == {
        ("GET", "/api/repos/{param}/evidence/status"),
        ("PUT", "/api/repos/{param}/evidence/items/{param}/mark"),
    }


def test_a_base_bound_in_the_factory_file_is_read(tmp_path: Path) -> None:
    text = _FACTORY + (
        "export const { getStatus } = createEvidenceApi(apiRequest, (id) => `/v2/repos/${id}`);\n"
    )
    assert ("GET", "/v2/repos/{param}/status") in _consumers(tmp_path, {"src/evidence.ts": text})


def test_two_bases_give_one_request_each(tmp_path: Path) -> None:
    web = (
        'import { createEvidenceApi } from "@acme/client";\n'
        "const api = createEvidenceApi(fetcher, (id: string) => `/api/repos/${id}/evidence`);\n"
    )
    admin = (
        'import { createEvidenceApi } from "@acme/client";\n'
        "const api = createEvidenceApi(fetcher, (id: string) => `/admin/repos/${id}/evidence`);\n"
    )
    found = _consumers(
        tmp_path, {"client/evidence.ts": _FACTORY, "web/api.ts": web, "admin/api.ts": admin}
    )
    assert {p for m, p in found if m == "GET"} == {
        "/api/repos/{param}/evidence/status",
        "/admin/repos/{param}/evidence/status",
    }


def test_an_unread_base_builder_emits_no_request(tmp_path: Path) -> None:
    # No call site, and one whose base is not a literal: the tail alone is no route.
    caller = (
        'import { createEvidenceApi } from "./evidence";\n'
        "const api = createEvidenceApi(apiRequest, routes.evidence);\n"
    )
    assert _consumers(tmp_path, {"src/evidence.ts": _FACTORY}) == set()
    assert _consumers(tmp_path, {"src/api.ts": caller}) == set()


def test_a_plain_string_base_is_put_before_the_path(tmp_path: Path) -> None:
    factory = (
        "export const makeUsersClient = (base: string) => ({\n"
        "  list: () => fetch(`${base}/users`),\n"
        "});\n"
    )
    caller = (
        'import { makeUsersClient } from "./users";\nconst users = makeUsersClient("/api/v1");\n'
    )
    assert _consumers(tmp_path, {"src/users.ts": factory, "src/app.ts": caller}) == {
        ("GET", "/api/v1/users")
    }


def test_a_plain_base_with_no_readable_caller_stays_the_host_placeholder(tmp_path: Path) -> None:
    factory = (
        "export function makeUsersClient(baseUrl: string) {\n"
        "  return { list: () => fetch(`${baseUrl}/users`) };\n"
        "}\n"
    )
    caller = (
        'import { makeUsersClient } from "./users";\n'
        "const users = makeUsersClient(process.env.USERS_URL);\n"
    )
    assert _consumers(tmp_path, {"src/users.ts": factory, "src/app.ts": caller}) == {
        ("GET", "/users")
    }


def test_a_wrapper_whose_whole_url_is_its_parameter_is_left_alone(tmp_path: Path) -> None:
    # `${path}` with nothing after it is a wrapper's own request, read at its call sites.
    text = (
        "export function apiFetch(path: string) {\n"
        "  return fetch(`${path}`);\n"
        "}\n"
        'export const list = () => apiFetch("/api/items");\n'
    )
    assert _consumers(tmp_path, {"src/api.ts": text}) == {("GET", "/api/items")}


_CALLER = (
    'import { createEvidenceApi } from "./evidence";\n'
    "export const api = createEvidenceApi(apiRequest, (id) => `/api/repos/${id}/evidence`);\n"
)


def test_an_object_return_type_does_not_hide_the_body(tmp_path: Path) -> None:
    factory = (
        "export function createEvidenceApi(\n"
        "  request: ApiRequest, base: (id: string) => string,\n"
        "): { getStatus: (id: string) => Promise<void> } {\n"
        "  return { getStatus: (id: string) => request(`${base(id)}/status`) };\n"
        "}\n"
    )
    assert _consumers(tmp_path, {"src/evidence.ts": factory, "src/api.ts": _CALLER}) == {
        ("GET", "/api/repos/{param}/evidence/status")
    }


def test_a_curried_factory_is_read(tmp_path: Path) -> None:
    factory = (
        "export const createEvidenceApi = (request: ApiRequest, base: (id: string) => string) =>\n"
        "  (id: string) => request(`${base(id)}/status`);\n"
    )
    assert _consumers(tmp_path, {"src/evidence.ts": factory, "src/api.ts": _CALLER}) == {
        ("GET", "/api/repos/{param}/evidence/status")
    }


def test_a_call_in_a_ternary_branch_is_read(tmp_path: Path) -> None:
    caller = (
        'import { createEvidenceApi } from "./evidence";\n'
        "export const api = local\n"
        "  ? createEvidenceApi(apiRequest, (id) => `/api/repos/${id}/evidence`) : remote;\n"
    )
    assert ("GET", "/api/repos/{param}/evidence/status") in _consumers(
        tmp_path, {"src/evidence.ts": _FACTORY, "src/api.ts": caller}
    )


def test_an_unnamed_factory_emits_no_tail_only_request(tmp_path: Path) -> None:
    factory = (
        "export default function (request: ApiRequest, base: (id: string) => string) {\n"
        "  return { getStatus: (id: string) => request(`${base(id)}/status`) };\n"
        "}\n"
    )
    assert _consumers(tmp_path, {"src/evidence.ts": factory}) == set()
