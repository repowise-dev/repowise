"""Python HTTP consumers: module calls through ``requests`` and ``httpx``."""

from __future__ import annotations

import pytest

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http.python_clients import PythonClientsDialect


def _ids(content: str) -> list[str]:
    ctx = ScanContext("svc", "client.py", ".py", content, {}, None)
    return [c.contract_id for c in PythonClientsDialect().extract(ctx)]


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ('requests.get("/api/users")', "http::GET::/api/users"),
        ("requests.get('http://users/api/users')", "http::GET::/api/users"),
        ('requests.get(f"{USERS}/api/users/{uid}/")', "http::GET::/api/users/{param}"),
        ('requests.post(f"/api/users/{uid}/reset", json={})', "http::POST::/api/users/{param}/reset"),
        ('httpx.delete("/api/" + "users")', "http::DELETE::/api/users"),
        ('requests.get(BASE + "/health")', "http::GET::/health"),
    ],
)
def test_the_url_is_read_off_the_first_argument(call: str, expected: str) -> None:
    assert _ids(f"import requests, httpx\n\n{call}\n") == [expected]


@pytest.mark.parametrize("call", ["requests.get(url)", "requests.get(build(url))", "requests.get()"])
def test_an_argument_that_names_no_url_yields_nothing(call: str) -> None:
    assert _ids(f"import requests\n\n{call}\n") == []
