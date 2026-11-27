"""``step_findings`` adds, for an import row's never-run libraries, where the repo calls them."""

from __future__ import annotations

import json
from types import SimpleNamespace

from repowise.core.ingestion.external_calls import ExternalCallSite
from repowise.core.persistence.crud import replace_external_call_sites, step_findings
from repowise.core.persistence.models import GraphEdge
from tests.unit.persistence.helpers import insert_repo, make_wiki_symbol


def _site(path: str, line: int, caller: str, package: str) -> ExternalCallSite:
    return ExternalCallSite(
        file_path=path,
        line=line,
        caller_symbol_id=f"{path}::{caller}",
        ecosystem="pypi",
        module=package,
        package=package,
        external_node=f"external:{package}",
        local_name=package,
        symbol_path="parse",
        binding="module",
    )


def _import_row(inside: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        symbol_id="pkg/__init__.py::<module>", drivers_json=None, inside_json=json.dumps(inside)
    )


INSIDE = [
    {"name": "dateutil", "kind": "library", "share": 0.6, "called": False},
    {"name": "structlog", "kind": "library", "share": 0.2, "called": False},
    {"name": "click", "kind": "library", "share": 0.2},
]


def _imports(repo_id: str, source: str, target: str, *, deferred: bool = False) -> GraphEdge:
    return GraphEdge(
        repository_id=repo_id,
        source_node_id=source,
        target_node_id=target,
        edge_type="imports",
        deferred=deferred,
    )


def _variable(repo_id: str, path: str, name: str):
    symbol = make_wiki_symbol(repo_id, path, name)
    symbol.kind = "variable"
    return symbol


_LIVE = {"pkg/recipes.py", "pkg/log.py", "tests/test_recipes.py", "pkg/cli.py"}


async def _seed(session) -> str:
    repo = await insert_repo(session)
    sites = {
        "pkg/recipes.py": [
            _site("pkg/recipes.py", 30, "parsedate", "dateutil"),
            _site("pkg/recipes.py", 60, "parsedatetime", "dateutil"),
        ],
        "pkg/log.py": [_site("pkg/log.py", 3, "logger", "structlog")],
        # A test calling the package at its top level says nothing about production.
        "tests/test_recipes.py": [_site("tests/test_recipes.py", 2, "__module__", "dateutil")],
        "pkg/cli.py": [_site("pkg/cli.py", 9, "main", "click")],
    }
    await replace_external_call_sites(session, repo.id, sites, scope=None, live_paths=set(sites))
    session.add_all(
        [
            make_wiki_symbol(repo.id, "pkg/recipes.py", "parsedate", start_line=28),
            make_wiki_symbol(repo.id, "pkg/recipes.py", "parsedatetime", start_line=58),
            _variable(repo.id, "pkg/log.py", "logger"),
            _imports(repo.id, "pkg/recipes.py", "external:dateutil.parser"),
            _imports(repo.id, "tests/test_recipes.py", "external:dateutil"),
            # Imported inside a function: never loaded at the module's top level.
            _imports(repo.id, "pkg/lazy.py", "external:dateutil", deferred=True),
        ]
    )
    await session.flush()
    return repo.id


async def test_an_import_rows_never_run_libraries_get_their_call_sites(async_session) -> None:
    repo_id = await _seed(async_session)
    facts = await step_findings(async_session, repo_id, [_import_row(INSIDE)])
    assert facts == [
        {
            "kind": "package_calls",
            "package": "dateutil",
            "callers": ["pkg/recipes.py::parsedate", "pkg/recipes.py::parsedatetime"],
            "callers_total": 2,
            "at_import": False,
        },
        {
            "kind": "package_calls",
            "package": "structlog",
            "callers": [],
            "callers_total": 0,
            "at_import": True,
        },
    ]


async def test_no_package_read_without_a_never_run_library(async_session) -> None:
    repo_id = await _seed(async_session)
    called = [{"name": "dateutil", "kind": "library", "share": 0.6}]
    assert await step_findings(async_session, repo_id, [_import_row(called)]) == []
    # A function row's callees are never package facts.
    fn = SimpleNamespace(
        symbol_id="pkg/recipes.py::parsedate", drivers_json=None, inside_json=json.dumps(INSIDE)
    )
    assert await step_findings(async_session, repo_id, [fn]) == []


async def test_a_default_value_call_or_a_top_level_importer_keeps_it_at_import(
    async_session,
) -> None:
    repo_id = await _seed(async_session)
    # ``def parsedate(value=parser.parse("now")):`` calls it when defined.
    sites = {"pkg/recipes.py": [_site("pkg/recipes.py", 28, "parsedate", "dateutil")]}
    await replace_external_call_sites(
        async_session, repo_id, sites, scope=frozenset(sites), live_paths=_LIVE
    )
    [dateutil, _] = await step_findings(async_session, repo_id, [_import_row(INSIDE)])
    assert dateutil["at_import"] is True
    sites = {"pkg/recipes.py": [_site("pkg/recipes.py", 30, "parsedate", "dateutil")]}
    await replace_external_call_sites(
        async_session, repo_id, sites, scope=frozenset(sites), live_paths=_LIVE
    )
    async_session.add(_imports(repo_id, "pkg/startup.py", "external:dateutil"))
    await async_session.flush()
    [dateutil, _] = await step_findings(async_session, repo_id, [_import_row(INSIDE)])
    assert dateutil["at_import"] is True


async def test_incomplete_call_sites_give_no_package_facts(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await step_findings(async_session, repo.id, [_import_row(INSIDE)]) == []
